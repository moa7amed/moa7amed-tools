"""Adaptive engineering workflow: the right amount of process for each task.

Every task gets one of three profiles, decided from the analysis the model router already
made (no extra model call):

    quick     small, low-risk, well-defined:      understand -> implement -> verify
    standard  ordinary development:               understand -> targeted plan -> implement
                                                  -> relevant tests -> verify
    deep      architecture, database or security  requirements -> options -> plan (strong
              design, infrastructure, major       model, approval when material) -> implement
              refactoring, high-risk changes:     -> tests -> review -> verify

The profile decides which engineering capabilities are switched on (CAPABILITIES below).
That table is policy the code applies, not documentation: it decides what the task's
prompt contains, whether the plan/implement/review phases run, whether a plan waits for
approval, and whether a result without a verification report is accepted.

Nothing here adds a model call. Profiles can be set by hand, escalate once on evidence,
and never lower a requirement that a plan approval or verification imposes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

from . import util

PROFILES = ("quick", "standard", "deep")
SCHEMA = """
CREATE TABLE IF NOT EXISTS task_workflow (
  task_id      INTEGER PRIMARY KEY REFERENCES tasks(id),
  profile      TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'auto',
  reason       TEXT,
  material     INTEGER NOT NULL DEFAULT 0,
  bug          INTEGER NOT NULL DEFAULT 0,
  capabilities TEXT NOT NULL DEFAULT '{}',
  escalations  INTEGER NOT NULL DEFAULT 0,
  approval     TEXT,
  verified     TEXT,
  history      TEXT NOT NULL DEFAULT '[]',
  updated_at   TEXT NOT NULL
);
"""

# capability -> level per profile. Levels: off, if_relevant, when_needed, light, targeted,
# risk_based, on, reuse (an approved baseline is used instead of redoing the work).
CAPABILITIES = {
    "requirements":   {"quick": "minimal", "standard": "targeted", "deep": "comprehensive"},
    "brainstorming":  {"quick": "off", "standard": "when_needed", "deep": "on"},
    "infrastructure": {"quick": "if_relevant", "standard": "if_affected", "deep": "on"},
    "architecture":   {"quick": "off", "standard": "if_changed", "deep": "on"},
    "planning":       {"quick": "off", "standard": "light", "deep": "full"},
    "tdd":            {"quick": "optional", "standard": "when_suitable", "deep": "preferred"},
    "debugging":      {"quick": "if_bug", "standard": "if_bug", "deep": "if_bug"},
    "subagents":      {"quick": "off", "standard": "optional", "deep": "when_beneficial"},
    "review":         {"quick": "targeted", "standard": "risk_based", "deep": "comprehensive"},
    "verification":   {"quick": "always", "standard": "always", "deep": "always"},
}
MATERIAL_SIGNAL = re.compile(r"architecture|database design|migration|schema|authentication|"
                             r"authori[sz]ation|security design|access control|infrastructure|"
                             r"deployment|migration to another stack|rewrite", re.I)
BUG_KINDS = ("fix",)
LIGHT_KINDS = {"text", "doc", "config", "mechanical", "explain"}
VERIFIED_RE = re.compile(r"^[ \t>*_`]*MQ_VERIFIED:[ \t]*(.+?)[ \t*_`]*$", re.I | re.M)
ESCALATE_WORKFLOW_RE = re.compile(r"^[ \t>*_`]*MQ_ESCALATE_WORKFLOW:[ \t]*(.+?)[ \t*_`]*$",
                                  re.I | re.M)
DONE_RE = re.compile(r"^[ \t>*_`]*MQ_RESULT:[ \t]*DONE", re.I | re.M)


def ensure_schema(conn):
    """Create the table if needed. A single statement, so it is safe inside a transaction
    (executescript would commit the transaction first)."""
    conn.execute(SCHEMA.strip().rstrip(";"))


def get(q, task_id):
    ensure_schema(q.conn)
    return q.conn.execute("SELECT * FROM task_workflow WHERE task_id=?", (task_id,)).fetchone()


def _history(row):
    try:
        data = json.loads(row["history"] or "[]")
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _add_history(q, task_id, entry):
    row = get(q, task_id)
    entries = _history(row) if row else []
    entry = dict(entry, at=util.now_iso())
    entries.append(entry)
    q.conn.execute("UPDATE task_workflow SET history=?, updated_at=? WHERE task_id=?",
                   (json.dumps(entries[-30:]), util.now_iso(), task_id))


# --------------------------------------------------------------------------- selection

def select(decision, task=None):
    """{"profile", "reason", "material", "bug"} from a router decision. The same evidence
    the model choice rests on: dimensions, critical signals, kinds of work, score."""
    dims = decision.get("dimensions") or {}
    critical = decision.get("critical") or []
    kinds = set(decision.get("kinds") or [])
    score = decision.get("score") or 0
    tier = decision.get("tier")
    bug = bool(kinds & set(BUG_KINDS))
    material_signals = [text for text in critical if MATERIAL_SIGNAL.search(text)]
    heavy = [name for name in ("architecture_impact", "database", "security")
             if (dims.get(name) or 0) >= 7]
    if material_signals or heavy or (score >= 85 and (dims.get("code_changes") or 0) >= 4):
        reasons = material_signals or ["%s impact" % name.replace("_", " ") for name in heavy] \
            or ["large, high-risk change (complexity %d/100)" % score]
        return {"profile": "deep", "reason": "; ".join(reasons[:3]),
                "material": bool(material_signals or heavy), "bug": bug}
    small = (tier == "haiku" or (score < 12 and not critical and kinds and kinds <= LIGHT_KINDS))
    risky = (dims.get("regression_risk") or 0) >= 2 or (dims.get("affected_modules") or 0) >= 2
    if small and not critical and not risky:
        return {"profile": "quick", "reason": "small, low-risk change (%s, complexity %d/100)"
                % (", ".join(sorted(kinds)) or "unspecified", score), "material": False, "bug": bug}
    reason = "ordinary development (complexity %d/100)" % score
    if critical:
        reason = "focused but demanding work: %s" % critical[0]
    return {"profile": "standard", "reason": reason, "material": False, "bug": bug}


def capabilities(profile, info, context=None, config=None):
    """The level of each capability for this task. `context` may say an approved, current
    baseline exists (architecture and infrastructure are then reused, not redone) or that
    the task belongs to an approved plan (planning is reused)."""
    context = context or {}
    config = config or {}
    caps = {name: levels[profile] for name, levels in CAPABILITIES.items()}
    caps["debugging"] = "on" if info.get("bug") else "off"
    if config.get("workflow_agent_policy") == "never":
        caps["subagents"] = "off"
    if config.get("workflow_risk_based_review") is False:
        caps["review"] = "comprehensive"
    if context.get("baseline_current") and profile == "deep" \
            and config.get("workflow_reuse_approved_plan", True):
        caps["infrastructure"] = caps["architecture"] = "reuse"
    if context.get("plan_task"):
        caps["planning"] = caps["architecture"] = caps["infrastructure"] = "reuse"
        caps["requirements"] = "reuse"
        caps["brainstorming"] = "off"
    return caps


def needs_approval(row, config, plan_task=False):
    """A deep task's plan waits for the owner before implementation when it is material
    (architecture, database, security or infrastructure design) and does not already
    come from an approved project plan."""
    if row is None or plan_task:
        return False
    policy = config.get("workflow_deep_approval", "material")
    if row["profile"] != "deep" or policy == "never":
        return False
    return policy == "always" or bool(row["material"])


def assign(q, task, decision, config, route_row=None):
    """Record the profile of a newly classified task and apply its consequences to routing.
    Called inside the transaction that classified it. A manual profile is kept."""
    ensure_schema(q.conn)
    if config.get("workflow_mode", "auto") == "off":
        return None
    existing = get(q, task["id"])
    plan_task = str(task["source"] or "").startswith("plan:")
    info = select(decision, task)
    if existing is not None and existing["source"] in ("manual", "escalated"):
        info = dict(info, profile=existing["profile"])
    context = project_context(q, decision, config, minimal=True)
    caps = capabilities(info["profile"], info, dict(context, plan_task=plan_task), config)
    stamp = util.now_iso()
    if existing is None:
        q.conn.execute(
            "INSERT INTO task_workflow(task_id, profile, source, reason, material, bug, "
            "capabilities, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (task["id"], info["profile"], "plan" if plan_task else "auto", info["reason"],
             int(info["material"]), int(info["bug"]), json.dumps(caps), stamp))
    else:
        q.conn.execute("UPDATE task_workflow SET profile=?, reason=?, material=?, bug=?, "
                       "capabilities=?, updated_at=? WHERE task_id=?",
                       (info["profile"], info["reason"] if existing["source"] == "auto"
                        else existing["reason"], int(info["material"]), int(info["bug"]),
                        json.dumps(caps), stamp, task["id"]))
    _apply_phases(q, task, info["profile"], caps, route_row)
    return get(q, task["id"])


def ensure_assigned(q, task, config, route_row):
    """A task classified before the workflow existed (queued under 0.4) gets its profile
    from the stored analysis when it is about to run. Inside a transaction."""
    if config.get("workflow_mode", "auto") == "off" or route_row is None \
            or get(q, task["id"]) is not None:
        return None
    try:
        details = json.loads(route_row["details"] or "{}")
    except ValueError:
        details = {}
    decision = dict(details, score=route_row["complexity_score"] or 0,
                    tier=route_row["recommended_model"])
    return assign(q, task, decision, config, route_row)


def _apply_phases(q, task, profile, caps, route_row):
    """Deep work runs as plan -> implement -> review (planning with the top tier). Tasks
    from an approved plan already have their plan: they run as one phase."""
    if route_row is None:
        return
    wanted = int(profile == "deep" and caps.get("planning") == "full")
    if route_row["phase"] or route_row["phased"] == wanted:
        return
    q.conn.execute("UPDATE task_routing SET phased=?, selected_model=CASE WHEN mode='auto' "
                   "AND ?=1 THEN 'sonnet' WHEN mode='auto' THEN recommended_model "
                   "ELSE selected_model END WHERE task_id=?", (wanted, wanted, task["id"]))


def set_profile(q, task_id, profile, config):
    """A manual profile. Lowering it never removes verification, and a task from an
    approved plan keeps the plan's approval."""
    from . import routing
    if profile not in PROFILES + ("auto",):
        raise util.QueueError("profile is quick, standard, deep or auto")
    task = q.get(task_id)
    if task["status"] == "running":
        raise util.QueueError("task #%d is running; its workflow cannot change now" % task_id)
    with q.tx():
        ensure_schema(q.conn)
        row = routing.get(q, task_id)
        if row is None:
            row = routing.classify_and_store(q, task, config)
        if get(q, task_id) is None:
            q.conn.execute("INSERT INTO task_workflow(task_id, profile, source, updated_at) "
                           "VALUES(?,?,?,?)", (task_id, "standard", "auto", util.now_iso()))
        if profile == "auto":
            q.conn.execute("UPDATE task_workflow SET source='auto' WHERE task_id=?", (task_id,))
            routing.classify_and_store(q, task, config)
        else:
            q.conn.execute("UPDATE task_workflow SET profile=?, source='manual', reason=? "
                           "WHERE task_id=?", (profile, "set by hand", task_id))
            details = json.loads(row["details"] or "{}")
            info = {"bug": "fix" in (details.get("kinds") or []), "material": False}
            caps = capabilities(profile, info, {"plan_task": str(task["source"]).startswith("plan:")},
                                config)
            q.conn.execute("UPDATE task_workflow SET capabilities=? WHERE task_id=?",
                           (json.dumps(caps), task_id))
            _apply_phases(q, task, profile, caps, routing.get(q, task_id))
        _add_history(q, task_id, {"type": "profile", "to": profile, "source": "manual"})
    return get(q, task_id)


def approve(q, task_id):
    """The owner approves the plan a deep task produced; implementation may start."""
    from . import routing
    row = get(q, task_id)
    if row is None or row["approval"] != "waiting":
        raise util.QueueError("task #%d has no plan waiting for approval" % task_id)
    with q.tx():
        q.conn.execute("UPDATE task_workflow SET approval='approved', updated_at=? WHERE "
                       "task_id=?", (util.now_iso(), task_id))
        _add_history(q, task_id, {"type": "plan-approved"})
        q.log_event(task_id, None, "workflow-approved", "the owner approved the task's plan")
    routing.unblock(q, [task_id])
    return get(q, task_id)


# --------------------------------------------------------------------------- run results

def after_plan_phase(q, task_id, config):
    """Called when a deep task's planning phase has finished. Returns the reason the task
    now waits for the owner, or None when implementation can follow directly."""
    row = get(q, task_id)
    task = q.get(task_id)
    if not needs_approval(row, config, str(task["source"]).startswith("plan:")):
        return None
    if row["approval"] == "approved":
        return None
    q.conn.execute("UPDATE task_workflow SET approval='waiting' WHERE task_id=?", (task_id,))
    _add_history(q, task_id, {"type": "plan-waiting", "reason": row["reason"]})
    return ("approval: the plan for this %stask waits for your review. Read it with "
            "`moa7amed-queue show %d`, then `moa7amed-queue workflow approve %d` (or cancel it)"
            % ("material " if row["material"] else "", task_id, task_id))


def verification_problem(row, config, phase, text):
    """The reason a finished run is not accepted for lack of a verification report, or None.
    Planning runs produce a plan, not a change, and need none."""
    if not config.get("workflow_enforce_verification", True) or phase == "plan" \
            or config.get("workflow_mode", "auto") == "off":
        return None
    if VERIFIED_RE.search(text or ""):
        return None
    return ("the task did not report how it verified the result (an MQ_VERIFIED line is "
            "required: the commands, tests or checks it ran)")


def after_run(q, task, run, outcome, text, files, config, stopped_by=None):
    """Record verification and decide on a bounded escalation of the workflow for the next
    attempt. Returns a note for the log, or None. Inside the finishing transaction.
    `stopped_by` names an outside stop (a budget limit) that says nothing about the task."""
    row = get(q, task["id"])
    if row is None:
        return None
    match = None
    for match in VERIFIED_RE.finditer(text or ""):
        pass
    if match:
        q.conn.execute("UPDATE task_workflow SET verified=? WHERE task_id=?",
                       (match.group(1)[:300], task["id"]))
    limit = int(config.get("workflow_max_escalations", 1))
    asked = None
    for asked in ESCALATE_WORKFLOW_RE.finditer(text or ""):
        pass
    target, reason = None, None
    claimed_done = bool(DONE_RE.search(text or ""))
    if stopped_by or outcome not in ("done", "failed", "timeout"):
        return None       # a budget stop or an interruption is not evidence about the task
    if outcome != "done" and claimed_done and not asked:
        # finished but not accepted (no verification report, a budget stop after the
        # work): not evidence that the task needs more process
        return None
    if outcome != "done":
        if asked and row["profile"] != "deep":
            target, reason = "deep", "the task reported: %s" % asked.group(1)[:200]
        elif row["profile"] == "quick":
            target, reason = "standard", "the quick attempt failed"
    elif row["profile"] == "quick" and len(files or []) > 3:
        _add_history(q, task["id"], {"type": "note", "text": "a quick task changed %d files"
                                     % len(files)})
        return "quick task changed %d files; consider a review" % len(files)
    if not target:
        return None
    if row["escalations"] >= limit:
        _add_history(q, task["id"], {"type": "escalation-refused", "to": target,
                                     "reason": reason})
        return "workflow escalation to %s refused: limit of %d reached" % (target, limit)
    from . import routing
    plan_task = str(task["source"]).startswith("plan:")
    info = {"bug": bool(row["bug"]), "material": bool(row["material"]) or target == "deep"}
    caps = capabilities(target, info, {"plan_task": plan_task}, config)
    q.conn.execute("UPDATE task_workflow SET profile=?, source='escalated', escalations="
                   "escalations+1, capabilities=?, material=?, reason=? WHERE task_id=?",
                   (target, json.dumps(caps), int(info["material"] and target == "deep"),
                    "escalated from %s: %s" % (row["profile"], reason), task["id"]))
    _add_history(q, task["id"], {"type": "escalation", "from": row["profile"], "to": target,
                                 "reason": reason})
    _apply_phases(q, task, target, caps, routing.get(q, task["id"]))
    return "workflow %s -> %s for the next attempt (%s)" % (row["profile"], target, reason)


# --------------------------------------------------------------------------- context index

def project_context(q, decision=None, config=None, minimal=True):
    """The approved architecture that matters for a task, from the project's approved plans:
    {"lines": [...], "baseline_current": bool, "stale": [...], "plans": [...]}. Only the
    decisions related to what the task touches are included, never whole documents."""
    out = {"lines": [], "baseline_current": False, "stale": [], "plans": []}
    try:
        exists = q.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                                "name='plan_approvals'").fetchone()
    except Exception:  # noqa: BLE001
        exists = None
    if not exists:
        return out
    from . import plan_model, plan_store
    dims = (decision or {}).get("dimensions") or {}
    wanted = {"architecture"}
    if (dims.get("database") or 0) >= 2:
        wanted.update(("database", "data-migration"))
    if (dims.get("security") or 0) >= 2:
        wanted.add("security")
    if (dims.get("architecture_impact") or 0) >= 4:
        wanted.update(("infrastructure", "deployment", "framework"))
    for item in plan_store.plans(q):
        number = plan_store.execution_revision(q, item["id"])
        if number is None:
            continue
        try:
            plan = plan_store.revision(q, item["id"], number)
        except util.QueueError:
            continue
        out["plans"].append(item["id"])
        stale = baseline_changes(q, plan)
        out["stale"] += stale
        decisions = [entry for entry in plan.get("decisions") or []
                     if entry.get("status") == "APPROVED"
                     and (not minimal or entry.get("category") in wanted)]
        for entry in decisions[:6]:
            option = plan_model.by_id(entry.get("options")).get(entry.get("selected")) or {}
            out["lines"].append("- %s (plan %s r%d, approved): %s -> %s" % (
                entry["id"], item["id"], number, entry.get("title"),
                option.get("name") or entry.get("selected")))
        allowed = sorted(plan_model.approved_components(plan))
        if allowed:
            out["lines"].append("- Approved technologies (plan %s): %s" % (item["id"],
                                                                          ", ".join(allowed)))
        target = plan_model.target_scenario(plan)
        if target:
            env = target.get("env") or {}
            summary = ", ".join("%s %s" % (field, (env.get(field) or {}).get("value"))
                                for field in ("hosting", "os", "web_server", "ram_gb")
                                if env.get(field))
            if summary:
                out["lines"].append("- Deployment target (plan %s): %s" % (item["id"], summary))
    out["baseline_current"] = bool(out["plans"]) and not out["stale"]
    return out


def baseline_changes(q, plan):
    """Why an approved baseline no longer describes the project: manifest files that changed
    since it was approved. Empty when it is current (or recorded no fingerprint)."""
    recorded = plan.get("baseline_fingerprint") or {}
    if not recorded:
        return []
    from . import plan_discover
    current = plan_discover.fingerprint(q.root)
    changed = sorted(path for path in set(recorded) | set(current)
                     if recorded.get(path) != current.get(path))
    return ["%s changed since the plan was approved" % path for path in changed[:10]]


# --------------------------------------------------------------------------- prompt

def prompt_block(q, task, route, config):
    """The workflow section of a task's prompt: only the capabilities that are on."""
    row = get(q, task["id"])
    if row is None or config.get("workflow_mode", "auto") == "off":
        return []
    caps = json.loads(row["capabilities"] or "{}")
    profile = row["profile"]
    phase = (route or {}).get("phase")
    authority = config.get("workflow_authority", "moa7amed")
    lines = ["", "<workflow>", "Workflow: QUICK." if profile == "quick" else
             "Workflow: %s (%s)." % (profile.upper(), row["reason"] or "")]
    if authority == "superpowers" and superpowers_present(config):
        lines += ["Use the Superpowers workflow you have installed for this task; it is the "
                  "authoritative process here. Whatever it does, verification below is required.",
                  "</workflow>"]
        return lines
    if profile == "quick":
        lines += ["Make the change and verify it. No planning documents or architecture work. "
                  "Do not start subagents.",
                  "If it proves larger or riskier (several modules, failing tests), stop: "
                  "MQ_RESULT: FAILED - needs a deeper workflow: <why>."]
    elif phase == "plan":
        lines.append("Plan thoroughly before anything is built:")
        steps = ["the requirements and acceptance criteria, and what is still unclear"]
        if caps.get("brainstorming") == "on":
            steps.append("at least two approaches, with their trade-offs")
        if caps.get("infrastructure") == "on":
            steps.append("the deployment environment and its constraints (state what is "
                         "known and what is assumed; do not invent hardware figures)")
        if caps.get("architecture") == "on":
            steps.append("the architectural impact and the decision, with its rationale")
        elif caps.get("architecture") == "reuse":
            steps.append("how the work fits the approved architecture below; if it cannot, "
                         "say so explicitly: that needs a plan revision")
        steps.append("the steps, files and tests, in order")
        lines += ["%d. %s" % (number, text) for number, text in enumerate(steps, 1)]
        if needs_approval(row, config):
            lines.append("The owner reviews this plan before implementation starts.")
    else:
        steps = ["Understand the relevant code and its conventions."]
        if caps.get("planning") == "light":
            steps.append("Decide on a short, targeted plan (no planning documents).")
        elif caps.get("planning") == "reuse":
            steps.append("Follow the approved plan; the task is one step of it.")
        if caps.get("debugging") == "on":
            steps.append("Reproduce the problem first, find the root cause, fix that, and "
                         "prove the fix with a test.")
        steps.append("Implement.")
        tdd = caps.get("tdd")
        if tdd == "preferred":
            steps.append("Write or update tests first where the behaviour is testable; run "
                         "the relevant test suite if you are permitted to run commands.")
        elif tdd == "when_suitable":
            steps.append("Add or update the relevant tests (test first when that is natural) "
                         "and run them if you are permitted to run commands.")
        review = caps.get("review")
        if review in ("risk_based", "comprehensive"):
            steps.append("Review your own diff for regressions in the code it touches before "
                         "you finish.")
        lines += ["%d. %s" % (number, text) for number, text in enumerate(steps, 1)]
        if caps.get("architecture") in ("if_changed", "reuse"):
            lines.append("If the work needs a change to the architecture (a new service, "
                         "database, framework or deployment model), do not make it: report "
                         "MQ_ESCALATE_WORKFLOW: <why> and MQ_RESULT: FAILED.")
    subagents = caps.get("subagents")
    if subagents == "off" and profile == "quick":
        pass                                     # said above
    elif subagents == "off":
        lines.append("Do not start subagents.")
    elif subagents == "optional":
        lines.append("Use subagents only for independent parts that can safely run in "
                     "parallel, never two on the same files.")
    elif subagents == "when_beneficial":
        lines.append("A separate subagent is justified for an independent security or "
                     "architecture review, or for parts that do not touch the same files.")
    if superpowers_present(config):
        lines.append("Superpowers is installed: do not also run its brainstorming, planning "
                     "or execution skills for this task; this workflow is the authoritative "
                     "one.")
    if profile == "quick" and caps.get("planning") != "reuse":
        context = {"lines": []}       # a small change does not need the architecture
    elif config.get("workflow_minimal_context", True):
        context = project_context(q, _decision_of(q, task), config, minimal=True)
    else:
        context = project_context(q, None, config, minimal=False)
    if context["lines"]:
        lines += ["Approved project context (do not change it without a plan revision):"]
        lines += context["lines"]
        if context["stale"]:
            lines.append("Note: the approved baseline may be out of date: %s."
                         % "; ".join(context["stale"][:3]))
    lines.append("</workflow>")
    return lines


def verification_rule(config, phase):
    if not config.get("workflow_enforce_verification", True) or phase == "plan" \
            or config.get("workflow_mode", "auto") == "off":
        return []
    return ["- Before the status line, add one line saying how you verified the result:",
            "  MQ_VERIFIED: <the commands, tests or checks you ran and what they showed>",
            "  If you may not run commands here, check the result by reading it and say so on "
            "that line; that alone is no reason to report FAILED."]


def _decision_of(q, task):
    row = q.conn.execute("SELECT details FROM task_routing WHERE task_id=?",
                         (task["id"],)).fetchone()
    try:
        return json.loads(row["details"]) if row and row["details"] else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- Superpowers

_SUPERPOWERS_CACHE = {}


def superpowers_present(config):
    """Is a Superpowers plugin installed and enabled for Claude Code? Asked from Claude Code
    itself (`claude plugin list --json`) at most every six hours; cached in the data
    directory. False when that cannot be determined."""
    setting = config.get("workflow_superpowers", "auto")
    if setting in ("yes", "no"):
        return setting == "yes"
    path = os.path.join(util.home(), "plugins-seen.json")
    now = time.time()
    cached = _SUPERPOWERS_CACHE.get(path)
    if cached and now - cached[0] < 6 * 3600:
        return cached[1]
    try:
        data = util.load_json(path, None) or {}
    except util.QueueError:
        data = {}
    if data and now - float(data.get("checked", 0)) < 6 * 3600:
        _SUPERPOWERS_CACHE[path] = (data["checked"], bool(data.get("superpowers")))
        return bool(data.get("superpowers"))
    present = False
    try:
        from . import models
        executable = models.resolve_claude(config.get("claude_bin") or util.env("CLAUDE_BIN")
                                           or "claude")
        result = subprocess.run([executable, "plugin", "list", "--json"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=60,
                                **util.no_window_kwargs())
        listed = json.loads(result.stdout.decode("utf-8", errors="replace") or "[]")
        present = any(str(item.get("id", "")).split("@")[0].lower() == "superpowers"
                      and item.get("enabled", True) for item in listed if isinstance(item, dict))
    except Exception:  # noqa: BLE001 - unknown means "not detected"
        present = False
    try:
        if os.path.isdir(util.home()):
            util.save_json_atomic(path, {"checked": now, "superpowers": present})
    except OSError:
        pass
    _SUPERPOWERS_CACHE[path] = (now, present)
    return present


# --------------------------------------------------------------------------- reporting

def describe(q, task_id):
    """Everything about a task's workflow, for `workflow` and `show`."""
    from . import routing
    row = get(q, task_id)
    task = q.get(task_id)
    if row is None:
        return None
    route = routing.get(q, task_id)
    caps = json.loads(row["capabilities"] or "{}")
    conditional = ("off", "optional", "if_relevant", "if_affected", "if_changed", "if_bug",
                   "minimal")
    full = ("on", "full", "comprehensive", "always", "preferred")
    active = [name if level in full else "%s (%s)" % (name, level.replace("_", " "))
              for name, level in caps.items() if level not in conditional]
    usage = q.conn.execute("SELECT COUNT(*) AS runs, SUM(cost_usd) AS cost FROM runs WHERE "
                           "task_id=? AND finished_at IS NOT NULL", (task_id,)).fetchone()
    planning = "reused (approved plan)" if caps.get("planning") == "reuse" else {
        "off": "none", "light": "targeted, in the session", "full": "plan phase"}.get(
        caps.get("planning"), caps.get("planning"))
    if route is not None and route["phased"]:
        planning += "; phase now: %s" % (route["phase"] or "plan")
    if row["approval"]:
        planning += "; approval: %s" % row["approval"]
    model = route["selected_model"] if route is not None else None
    if route is not None and route["phased"]:
        model = ("%s in every phase (plan, implement, review when justified)" % model
                 if route["mode"] == "manual" else
                 "opus plans, %s implements, opus reviews when justified" % model)
    return {"task": task_id, "title": task["title"], "profile": row["profile"],
            "source": row["source"], "reason": row["reason"], "material": bool(row["material"]),
            "capabilities": caps, "active": active, "model": model,
            "agents": {"off": "not allowed", "optional": "only for independent parts",
                       "when_beneficial": "allowed when justified"}.get(caps.get("subagents"),
                                                                        "-"),
            "planning": planning, "verification": row["verified"] or (
                "required" if caps.get("verification") == "always" else "-"),
            "escalations": row["escalations"], "history": _history(row),
            "runs": usage["runs"], "reported_cost_usd": usage["cost"]}
