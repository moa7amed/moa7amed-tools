"""Plans in the project's queue database: drafts, immutable revisions, approvals, and the
link between plan tasks and queue tasks.

The queue stays the only execution authority. A plan task becomes an ordinary queue task
(source "plan:<plan id>") and the link table is the single record of which queue task
carries which plan task. The state of a plan task is always read from its queue task, so
the plan and the queue cannot disagree.

Tables (additive: older versions of the plugin ignore them):

    plans           the working draft of each plan
    plan_revisions  every approved revision, never changed or deleted
    plan_approvals  who approved what, for which revision and content hash
    plan_links      plan task -> queue task, per revision
    plan_findings   compliance findings recorded after a plan task ran
    plan_log        issues, revisions and imports, in order
"""
from __future__ import annotations

import json
import re

from . import plan_catalog as catalog
from . import plan_model as model
from . import util
from .util import QueueError

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  draft      TEXT NOT NULL,
  revision   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS plan_revisions (
  plan_id    TEXT NOT NULL,
  revision   INTEGER NOT NULL,
  content    TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (plan_id, revision)
);
CREATE TABLE IF NOT EXISTS plan_approvals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id     TEXT NOT NULL,
  revision    INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  scope       TEXT NOT NULL,
  decisions   TEXT NOT NULL DEFAULT '[]',
  note        TEXT,
  approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_links (
  plan_id       TEXT NOT NULL,
  plan_task_id  TEXT NOT NULL,
  revision      INTEGER NOT NULL,
  digest        TEXT NOT NULL,
  queue_task_id INTEGER NOT NULL UNIQUE,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (plan_id, plan_task_id, queue_task_id)
);
CREATE TABLE IF NOT EXISTS plan_findings (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id       TEXT NOT NULL,
  plan_task_id  TEXT NOT NULL,
  queue_task_id INTEGER,
  run_id        INTEGER,
  level         TEXT NOT NULL,
  component     TEXT,
  message       TEXT NOT NULL,
  at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id TEXT NOT NULL,
  kind    TEXT NOT NULL,
  detail  TEXT,
  at      TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS plan_revisions_are_immutable
BEFORE UPDATE ON plan_revisions
BEGIN SELECT RAISE(ABORT, 'an approved plan revision cannot be changed; revise the plan'); END;
CREATE TRIGGER IF NOT EXISTS plan_revisions_are_kept
BEFORE DELETE ON plan_revisions
BEGIN SELECT RAISE(ABORT, 'approved plan revisions are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS plan_approvals_are_kept
BEFORE DELETE ON plan_approvals
BEGIN SELECT RAISE(ABORT, 'approvals are never deleted'); END;
"""

PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SOURCE_PREFIX = "plan:"
LIST_SECTIONS = ("facts", "requirements", "decisions", "milestones", "tasks", "risks",
                 "open_questions", "existing_components")


def ensure_schema(q):
    found = q.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                           "name='plan_log'").fetchone()
    if found is None:
        q.conn.executescript(SCHEMA)


def _log(q, plan_id, kind, detail):
    q.conn.execute("INSERT INTO plan_log(plan_id, kind, detail, at) VALUES(?,?,?,?)",
                   (plan_id, kind, detail, util.now_iso()))


# --------------------------------------------------------------------------- reading

def plans(q):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT id, title, revision, created_at, updated_at FROM plans ORDER BY created_at")]


def load(q, plan_id):
    """The draft of a plan (a dict). Raises if it does not exist."""
    ensure_schema(q)
    row = q.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        known = ", ".join(item["id"] for item in plans(q)) or "none"
        raise QueueError("no plan '%s' (plans: %s)" % (plan_id, known))
    draft = json.loads(row["draft"])
    draft["_revision"] = row["revision"]
    return draft


def revision(q, plan_id, number):
    ensure_schema(q)
    row = q.conn.execute("SELECT * FROM plan_revisions WHERE plan_id=? AND revision=?",
                         (plan_id, number)).fetchone()
    if row is None:
        raise QueueError("plan %s has no approved revision %s" % (plan_id, number))
    content = json.loads(row["content"])
    if model.content_hash(content) != row["sha256"]:
        raise QueueError("revision %s of plan %s does not match its recorded hash; it has "
                         "been altered outside moa7amed-queue" % (number, plan_id))
    return content


def approvals(q, plan_id):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT * FROM plan_approvals WHERE plan_id=? ORDER BY id", (plan_id,))]


def execution_revision(q, plan_id):
    """The newest revision approved for execution, or None."""
    rows = [item for item in approvals(q, plan_id) if item["scope"] == "execution"]
    return rows[-1]["revision"] if rows else None


def last_approved(q, plan_id):
    rows = approvals(q, plan_id)
    return revision(q, plan_id, rows[-1]["revision"]) if rows else None


# --------------------------------------------------------------------------- writing the draft

def create(q, plan_id, title, mode="new", summary=""):
    ensure_schema(q)
    if not PLAN_ID.match(plan_id or ""):
        raise QueueError("a plan id is lowercase letters, digits, '.', '_' or '-' (up to 64)")
    if mode not in ("new", "existing"):
        raise QueueError("mode is 'new' or 'existing'")
    stamp = util.now_iso()
    plan = model.new_plan(plan_id, title, mode)
    plan["summary"] = summary or ""
    with q.tx():
        if q.conn.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone():
            raise QueueError("plan '%s' already exists" % plan_id)
        q.conn.execute("INSERT INTO plans(id, title, created_at, updated_at, draft, revision) "
                       "VALUES(?,?,?,?,?,1)", (plan_id, title, stamp, stamp, json.dumps(plan)))
        _log(q, plan_id, "created", "%s plan: %s" % (mode, title))
    return plan


def _merge(plan, fragment):
    """Merge a JSON fragment into a draft: list sections are upserted by id, scenarios by id
    with their environment merged field by field, other keys replaced. A `remove` key
    lists ids to drop: {"remove": {"tasks": ["T9"]}}."""
    for key, value in fragment.items():
        if key in ("id", "remove", "_revision"):
            continue
        if key in LIST_SECTIONS and isinstance(value, list):
            current = plan.get(key) or []
            index = {item.get("id"): position for position, item in enumerate(current)
                     if isinstance(item, dict) and item.get("id")}
            for item in value:
                if isinstance(item, dict) and item.get("id") in index:
                    merged = dict(current[index[item["id"]]])
                    merged.update(item)
                    current[index[item["id"]]] = merged
                elif isinstance(item, dict) and item.get("id"):
                    index[item["id"]] = len(current)
                    current.append(item)
                elif item not in current:
                    current.append(item)
            plan[key] = current
        elif key == "infrastructure" and isinstance(value, dict):
            infrastructure = plan.setdefault("infrastructure", {"target": None, "scenarios": []})
            for sub, subvalue in value.items():
                if sub == "scenarios" and isinstance(subvalue, list):
                    existing = model.by_id(infrastructure.get("scenarios"))
                    for scenario in subvalue:
                        if not isinstance(scenario, dict) or not scenario.get("id"):
                            raise QueueError("every scenario needs an id")
                        if scenario["id"] in existing:
                            old = existing[scenario["id"]]
                            env = dict(old.get("env") or {})
                            env.update(scenario.get("env") or {})
                            old.update({k: v for k, v in scenario.items() if k != "env"})
                            old["env"] = env
                        else:
                            infrastructure.setdefault("scenarios", []).append(scenario)
                else:
                    infrastructure[sub] = subvalue
        elif key == "capacity" and isinstance(value, dict):
            capacity = plan.setdefault("capacity", {"inputs": {}})
            capacity.setdefault("inputs", {}).update(value.get("inputs") or {})
        else:
            plan[key] = value
    for section, ids in (fragment.get("remove") or {}).items():
        if section in LIST_SECTIONS:
            plan[section] = [item for item in plan.get(section) or []
                             if not (isinstance(item, dict) and item.get("id") in ids)]
    return plan


def _guard_approved(q, plan_id, plan):
    """An approved decision is never edited. A change is a new decision that supersedes it."""
    approved = last_approved(q, plan_id) or {}
    before = {item["id"]: item for item in approved.get("decisions") or []
              if item.get("status") == "APPROVED"}
    after = model.by_id(plan.get("decisions"))
    for decision_id, original in before.items():
        current = after.get(decision_id)
        if current is None:
            raise QueueError("%s is approved and cannot be removed; record a decision that "
                             "supersedes it" % decision_id)
        if current.get("status") == "SUPERSEDED":
            continue   # set by the approval of the amendment
        if model.content_hash(current) != model.content_hash(original):
            raise QueueError("%s is approved and cannot be edited. Record the change as a new "
                             "decision with \"supersedes\": \"%s\"; it needs its own approval, "
                             "and the tasks it affects are held until then (`moa7amed-queue "
                             "project revise`)" % (decision_id, decision_id))
    for decision in plan.get("decisions") or []:
        if decision.get("status") == "APPROVED" and decision.get("id") not in before:
            raise QueueError("%s cannot be set to APPROVED by editing the plan; use `moa7amed-"
                             "queue project approve`" % decision.get("id"))


def save(q, plan_id, plan, note):
    plan = {key: value for key, value in plan.items() if key != "_revision"}
    with q.tx():
        _guard_approved(q, plan_id, plan)
        q.conn.execute("UPDATE plans SET draft=?, title=?, updated_at=? WHERE id=?",
                       (json.dumps(plan, ensure_ascii=False), plan.get("title") or plan_id,
                        util.now_iso(), plan_id))
        _log(q, plan_id, "edited", note)
    return plan


def apply(q, plan_id, fragment, note="applied"):
    if not isinstance(fragment, dict):
        raise QueueError("the plan fragment must be a JSON object")
    plan = load(q, plan_id)
    plan = _merge(plan, fragment)
    for decision in plan.get("decisions") or []:
        decision.setdefault("status", "PROPOSED")
        decision.setdefault("date", util.now_iso()[:10])
        if decision.get("category") in model.MATERIAL_CATEGORIES:
            decision.setdefault("material", True)
    for decision in plan.get("decisions") or []:
        # The stored status follows the evidence; only an approval sets APPROVED.
        if decision.get("status") in ("PROPOSED", "NEEDS_INFORMATION"):
            decision["status"], missing = model.suggested_status(plan, decision)
            decision["missing"] = missing
    return save(q, plan_id, plan, note)


def set_env(q, plan_id, scenario_id, field, value, status, source, label=None):
    if field not in catalog.ENV_FIELDS:
        raise QueueError("unknown field %s (known: %s)" % (field, ", ".join(catalog.ENV_FIELDS)))
    if status not in model.EVIDENCE:
        raise QueueError("status must be one of %s" % ", ".join(model.EVIDENCE))
    if status == "VERIFIED" and not source:
        raise QueueError("VERIFIED needs a source (a document, a measurement, a configuration)")
    scenario = {"id": scenario_id, "env": {field: {"value": value, "status": status,
                                                    "source": source or ""}}}
    if label:
        scenario["label"] = label
    return apply(q, plan_id, {"infrastructure": {"scenarios": [scenario]}},
                 "%s.%s = %s (%s)" % (scenario_id, field, value, status))


# --------------------------------------------------------------------------- approval

def approve(q, plan_id, decisions=None, execution=True, note=None, confirmed=False):
    """Approve decisions and/or the plan for execution. Freezes the draft as a new immutable
    revision and records the approval against its content hash. Without `confirmed`,
    returns what would be approved and changes nothing."""
    plan = load(q, plan_id)
    number = plan.pop("_revision")
    findings = model.validate(plan)
    errors = [item for item in findings if item["level"] == "error"]
    proposed = [item for item in plan.get("decisions") or [] if item.get("status") == "PROPOSED"
                or item.get("status") == "NEEDS_INFORMATION"]
    wanted = decisions if decisions is not None else [item["id"] for item in proposed]
    known = model.by_id(plan.get("decisions"))
    approvable, refused = [], []
    for decision_id in wanted:
        decision = known.get(decision_id)
        if decision is None:
            raise QueueError("no decision %s in plan %s" % (decision_id, plan_id))
        if decision.get("status") == "APPROVED":
            continue
        status, reasons = model.suggested_status(plan, decision)
        own_errors = [item for item in errors if item["where"] == decision_id]
        if status != "PROPOSED" or own_errors:
            refused.append({"decision": decision_id, "reasons": reasons +
                            [item["message"] for item in own_errors]})
        else:
            approvable.append(decision_id)
    assumptions = {decision_id: model.decision_assumptions(plan, known[decision_id])
                   for decision_id in approvable}
    task_errors = [item for item in errors if item["where"] not in known]
    preview = {"plan": plan_id, "revision": number, "decisions": approvable, "refused": refused,
               "assumptions": {key: value for key, value in assumptions.items() if value},
               "execution": execution, "errors": task_errors if execution else [],
               "approved": False}
    if execution and task_errors:
        preview["message"] = ("the plan cannot be approved for execution: %d error(s); "
                              "`moa7amed-queue project check %s` lists them"
                              % (len(task_errors), plan_id))
        return preview
    if not approvable and not execution:
        preview["message"] = "nothing to approve"
        return preview
    from . import challenge
    waiting = challenge.open_for_plan(q, plan_id)
    if waiting:
        preview["concerns"] = ["C%d: %s" % (row["id"], row["concern"]) for row in waiting]
        preview["message"] = ("%d concern(s) about this plan wait for your decision (%s); "
                              "decide first: `moa7amed-queue challenge decide C%d "
                              "--recommendation` or `--original`"
                              % (len(waiting), "; ".join(preview["concerns"]), waiting[0]["id"]))
        if confirmed:
            raise QueueError(preview["message"])
        return preview
    if not confirmed:
        return preview
    stamp = util.now_iso()
    for decision in plan.get("decisions") or []:
        if decision["id"] in approvable:
            decision["status"] = "APPROVED"
            decision["approved_at"] = stamp
            decision["revision"] = number
            superseded = decision.get("supersedes")
            if superseded and superseded in known:
                known[superseded]["status"] = "SUPERSEDED"
                known[superseded]["superseded_by"] = decision["id"]
    for decision in plan.get("decisions") or []:
        if decision.get("status") in ("PROPOSED", "NEEDS_INFORMATION"):
            decision["status"] = model.suggested_status(plan, decision)[0]
    from . import plan_discover
    plan["baseline_fingerprint"] = plan_discover.fingerprint(q.root)
    frozen = model.freeze(plan)
    digest = model.content_hash(frozen)
    with q.tx():
        q.conn.execute("INSERT INTO plan_revisions(plan_id, revision, content, sha256, created_at) "
                       "VALUES(?,?,?,?,?)", (plan_id, number, json.dumps(frozen, ensure_ascii=False),
                                             digest, stamp))
        q.conn.execute("INSERT INTO plan_approvals(plan_id, revision, sha256, scope, decisions, "
                       "note, approved_at) VALUES(?,?,?,?,?,?,?)",
                       (plan_id, number, digest, "execution" if execution else "decision",
                        json.dumps(approvable), note, stamp))
        q.conn.execute("UPDATE plans SET draft=?, revision=?, updated_at=? WHERE id=?",
                       (json.dumps(plan, ensure_ascii=False), number + 1, stamp, plan_id))
        _log(q, plan_id, "approved", "revision %d (%s); decisions: %s" % (
            number, "execution" if execution else "decisions only",
            ", ".join(approvable) or "none"))
    preview.update(approved=True, sha256=digest)
    return preview


# --------------------------------------------------------------------------- queue integration

def links(q, plan_id):
    ensure_schema(q)
    rows = q.conn.execute("SELECT * FROM plan_links WHERE plan_id=? ORDER BY created_at, "
                          "queue_task_id", (plan_id,)).fetchall()
    current = {}
    for row in rows:
        current[row["plan_task_id"]] = dict(row)   # the newest link wins
    return current


def _adopt_orphans(q, plan_id, plan, revision_number):
    """A queue task created by an import that was cut off before its link was written.
    (Import writes both in one transaction; this covers databases from elsewhere.)"""
    linked = {row["queue_task_id"] for row in q.conn.execute(
        "SELECT queue_task_id FROM plan_links WHERE plan_id=?", (plan_id,))}
    for row in q.conn.execute("SELECT id, prompt FROM tasks WHERE source=?",
                              (SOURCE_PREFIX + plan_id,)).fetchall():
        if row["id"] in linked:
            continue
        match = re.match(r"\[%s (\S+)\]" % re.escape(plan_id), row["prompt"])
        task = model.by_id(plan.get("tasks")).get(match.group(1)) if match else None
        if task:
            q.conn.execute("INSERT INTO plan_links(plan_id, plan_task_id, revision, digest, "
                           "queue_task_id, created_at) VALUES(?,?,?,?,?,?)",
                           (plan_id, task["id"], revision_number, model.task_digest(task),
                            row["id"], util.now_iso()))


def execute(q, plan_id, confirmed=False):
    """Import the tasks of the newest execution-approved revision into the queue, in
    dependency order. Idempotent: an unchanged task that is already queued is not added
    again. Tasks that wait for an unapproved decision are held (not imported). Without
    `confirmed`, returns the import plan only."""
    number = execution_revision(q, plan_id)
    if number is None:
        raise QueueError("plan %s has no revision approved for execution. Review it with "
                         "`moa7amed-queue project check %s` and approve it with `moa7amed-queue "
                         "project approve %s`" % (plan_id, plan_id, plan_id))
    plan = revision(q, plan_id, number)
    with q.tx():
        _adopt_orphans(q, plan_id, plan, number)
    existing = links(q, plan_id)
    tasks = model.by_id(plan.get("tasks"))
    order = model.task_graph(plan)["order"]
    states = model.readiness(plan)
    depth = model.planning_depth(plan)[0]
    report = {"plan": plan_id, "revision": number, "added": [], "unchanged": [], "replaced": [],
              "held": [], "kept_done": [], "cancelled": [], "running": [], "applied": False}
    queue_id = {}
    for task_id in order:
        task = tasks[task_id]
        digest = model.task_digest(task)
        link = existing.get(task_id)
        queued = q.get(link["queue_task_id"]) if link else None
        state, reason = states.get(task_id, ("waiting", ""))
        held = state == "held" or any(dep in [item["task"] for item in report["held"]]
                                      for dep in task.get("depends_on") or [])
        if held:
            report["held"].append({"task": task_id, "reason": reason or "a prerequisite is held"})
            if queued is not None and queued["status"] in ("pending", "failed"):
                report["cancelled"].append({"task": task_id, "queue_task": queued["id"],
                                            "reason": "now waits for a decision"})
            continue
        if queued is not None and link["digest"] == digest and queued["status"] != "cancelled":
            report["unchanged"].append({"task": task_id, "queue_task": queued["id"]})
            queue_id[task_id] = queued["id"]
            continue
        if queued is not None and queued["status"] == "done":
            report["kept_done"].append({"task": task_id, "queue_task": queued["id"],
                                        "note": "done under revision %d; the change needs a "
                                                "follow-up task" % link["revision"]})
            queue_id[task_id] = queued["id"]
            continue
        if queued is not None and queued["status"] == "running":
            report["running"].append({"task": task_id, "queue_task": queued["id"]})
            queue_id[task_id] = queued["id"]
            continue
        if queued is not None and queued["status"] in ("pending", "failed"):
            report["replaced"].append({"task": task_id, "queue_task": queued["id"]})
        report["added"].append({"task": task_id})
    if not confirmed:
        return report
    for item in report["cancelled"]:
        q.cancel([item["queue_task"]])
        with q.tx():
            q.log_event(item["queue_task"], None, "plan-held",
                        "plan %s revision %d: %s" % (plan_id, number, item["reason"]))
    for item in report["replaced"]:
        q.cancel([item["queue_task"]])
        with q.tx():
            q.log_event(item["queue_task"], None, "plan-replaced",
                        "plan %s revision %d changed this task; a new queue task replaces it"
                        % (plan_id, number))
    for item in report["added"]:
        task = tasks[item["task"]]
        prompt, acceptance = model.compose_task_prompt(plan, task)
        after = [queue_id[dep] for dep in task.get("depends_on") or [] if dep in queue_id]
        priority = task.get("priority", "normal")
        priority = model.PRIORITY_VALUES.get(str(priority).lower(), priority)
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = 0
        chosen = task.get("model")
        if not chosen or chosen == "auto":
            chosen = "opus" if depth == "deep" and task.get("kind") == "analysis" else None
        with q.tx():
            new_id = q.add(prompt, acceptance=acceptance or None, title="[%s %s] %s" % (
                plan_id, task["id"], task.get("title")), priority=priority, after=after,
                max_attempts=1 if task.get("side_effects") else None,
                source=SOURCE_PREFIX + plan_id, model=chosen)
            q.conn.execute("INSERT INTO plan_links(plan_id, plan_task_id, revision, digest, "
                           "queue_task_id, created_at) VALUES(?,?,?,?,?,?)",
                           (plan_id, task["id"], number, model.task_digest(task), new_id,
                            util.now_iso()))
        queue_id[task["id"]] = new_id
        item["queue_task"] = new_id
    with q.tx():
        _log(q, plan_id, "imported", "revision %d: %d added, %d unchanged, %d replaced, %d held"
             % (number, len(report["added"]), len(report["unchanged"]), len(report["replaced"]),
                len(report["held"])))
    report["applied"] = True
    return report


def progress(q, plan_id):
    """Plan tasks with their state read from the queue, per milestone."""
    plan = load(q, plan_id)
    number = execution_revision(q, plan_id)
    source = revision(q, plan_id, number) if number else plan
    existing = links(q, plan_id)
    done = {task_id for task_id, link in existing.items()
            if (q.get(link["queue_task_id"]) or {})["status"] == "done"}
    failed = {task_id for task_id, link in existing.items()
              if (q.get(link["queue_task_id"]) or {})["status"] == "failed"}
    states = model.readiness(source, done, failed)
    rows = []
    for task in source.get("tasks") or []:
        link = existing.get(task["id"])
        queued = q.get(link["queue_task_id"]) if link else None
        state, reason = states.get(task["id"], ("waiting", ""))
        if queued is not None:
            queue_state = q.state_of(queued)
            state = {"done": "done", "failed": "failed", "running": "running",
                     "cancelled": "cancelled"}.get(queued["status"], queue_state)
            if queued["status"] == "failed" and queued["last_error"]:
                reason = queued["last_error"][:200]
        elif number is not None and state in ("ready", "waiting"):
            state, reason = "not imported", "run `moa7amed-queue project execute %s`" % plan_id
        rows.append({"task": task["id"], "title": task.get("title"),
                     "milestone": task.get("milestone"), "state": state, "reason": reason,
                     "queue_task": queued["id"] if queued else None})
    milestones = []
    for milestone in source.get("milestones") or []:
        mine = [row for row in rows if row["milestone"] == milestone["id"]]
        milestones.append({"id": milestone["id"], "title": milestone.get("title"),
                           "done": len([row for row in mine if row["state"] == "done"]),
                           "total": len(mine)})
    findings = [dict(row) for row in q.conn.execute(
        "SELECT * FROM plan_findings WHERE plan_id=? ORDER BY id", (plan_id,))]
    return {"plan": plan_id, "title": plan.get("title"), "stage": model.stage(source, number),
            "execution_revision": number, "draft_revision": plan["_revision"], "tasks": rows,
            "milestones": milestones, "findings": findings,
            "depth": model.planning_depth(source)}


def revise(q, plan_id, issue, task_ids=(), decision_ids=()):
    """Record an implementation issue and work out what it affects: the tasks named, those
    linked to the decisions named, and everything that depends on them. Completed work is
    listed as kept. Changes nothing else; the fix is a plan edit plus a new approval."""
    plan = load(q, plan_id)
    tasks = model.by_id(plan.get("tasks"))
    for ref in task_ids:
        if ref not in tasks:
            raise QueueError("no task %s in plan %s" % (ref, plan_id))
    decisions = model.by_id(plan.get("decisions"))
    for ref in decision_ids:
        if ref not in decisions:
            raise QueueError("no decision %s in plan %s" % (ref, plan_id))
    affected = set(task_ids)
    affected.update(task["id"] for task in tasks.values()
                    if set(task.get("decisions") or []) & set(decision_ids))
    changed = True
    while changed:
        changed = False
        for task in tasks.values():
            if task["id"] not in affected and set(task.get("depends_on") or []) & affected:
                affected.add(task["id"])
                changed = True
    existing = links(q, plan_id)
    kept, open_ = [], []
    for task_id in sorted(affected):
        link = existing.get(task_id)
        queued = q.get(link["queue_task_id"]) if link else None
        (kept if queued is not None and queued["status"] == "done" else open_).append(task_id)
    approved_impacted = [ref for ref in decision_ids if decisions[ref].get("status") == "APPROVED"]
    entry = {"id": "I%d" % (len(plan.get("issues") or []) + 1), "text": issue,
             "tasks": sorted(task_ids), "decisions": sorted(decision_ids),
             "affected": sorted(affected), "at": util.now_iso()}
    plan.setdefault("issues", []).append(entry)
    save(q, plan_id, plan, "issue %s: %s" % (entry["id"], issue[:200]))
    return {"issue": entry, "affected": sorted(affected), "completed_kept": kept,
            "to_replan": open_, "approved_decisions_impacted": approved_impacted,
            "next": ("record an amendment: a new decision with \"supersedes\": \"%s\", then "
                     "approve it; the tasks above are held until then" % approved_impacted[0])
                    if approved_impacted else
                    "edit the affected tasks (`moa7amed-queue project apply`), approve the new "
                    "revision and import it; unaffected tasks continue"}


# --------------------------------------------------------------------------- compliance

def plan_of_task(q, queue_task_id):
    ensure_schema(q)
    row = q.conn.execute("SELECT * FROM plan_links WHERE queue_task_id=?",
                         (queue_task_id,)).fetchone()
    return dict(row) if row else None


def check_run(q, task, run, cwd, head_before, commit_sha, policy="block"):
    """Compliance of a finished plan task's commit with the approved architecture. Records
    the findings and returns (blocking findings, all findings)."""
    from . import gitops
    link = plan_of_task(q, task["id"])
    if link is None or not commit_sha:
        return [], []
    plan = revision(q, link["plan_id"], link["revision"])
    diffs = {}
    for path in gitops.changed_between(cwd, head_before, commit_sha):
        code, out, _ = gitops.git(cwd, "diff", "--unified=0", head_before or gitops.EMPTY_TREE,
                                  commit_sha, "--", path)
        diffs[path] = out if code == 0 else ""
    plan_task = model.by_id(plan.get("tasks")).get(link["plan_task_id"]) or {}
    findings = model.compliance(plan, plan_task, diffs)
    stamp = util.now_iso()
    with q.tx():
        for item in findings:
            q.conn.execute("INSERT INTO plan_findings(plan_id, plan_task_id, queue_task_id, run_id, "
                           "level, component, message, at) VALUES(?,?,?,?,?,?,?,?)",
                           (link["plan_id"], link["plan_task_id"], task["id"], run["id"],
                            item["level"], item["component"], item["message"], stamp))
    blocking = [item for item in findings if item["level"] == "violation"
                or (item["level"] == "unapproved" and policy == "block")]
    return blocking, findings
