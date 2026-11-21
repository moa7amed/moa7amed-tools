"""Per-task model routing: persistent metadata, selection, fallback, escalation, phases.

Routing data lives in two tables of its own (`task_routing`, `run_routing`).
They are created next to the existing tables and never alter them, so a queue
written by an earlier version keeps working unchanged: a task without a
routing row is simply classified the first time it is needed.

Lifecycle of a task's model:

  queued    -> classified by rules (router.classify); recommendation stored
  claimed   -> optional model review of borderline cases, availability check,
               fallback or block; the model is fixed for this run
  finished  -> the model the CLI reports is recorded and compared with the
               request; a refused model triggers a recorded fallback; repeated
               failure can trigger one recorded escalation
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import models, util


class _OnFirstUse(object):
    """The classifier compiles several hundred patterns when it is imported (measured:
    about 0.1 s). Most commands never classify anything, so it is loaded when first used."""

    def __getattr__(self, name):
        from . import router as module
        return getattr(module, name)


router = _OnFirstUse()
from .util import QueueError

TIERS = models.TIERS
PHASES = ("plan", "implement", "review")
PHASE_TIER = {"plan": "opus", "review": "opus"}
# Tools withheld in the phases that must not change files.
READ_ONLY_DISALLOWED = ["Edit", "Write", "NotebookEdit"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_routing (
  task_id           INTEGER PRIMARY KEY REFERENCES tasks(id),
  mode              TEXT NOT NULL DEFAULT 'auto',
  recommended_model TEXT,
  selected_model    TEXT,
  selected_source   TEXT NOT NULL DEFAULT 'auto',
  actual_model      TEXT,
  reason            TEXT,
  complexity_score  INTEGER,
  min_tier          TEXT,
  confidence        TEXT,
  details           TEXT,
  policy            TEXT,
  policy_version    TEXT,
  classified_at     TEXT,
  classifier        TEXT,
  review_pending    INTEGER NOT NULL DEFAULT 0,
  manual_override   INTEGER NOT NULL DEFAULT 0,
  escalation_count  INTEGER NOT NULL DEFAULT 0,
  history           TEXT NOT NULL DEFAULT '[]',
  blocked_reason    TEXT,
  phased            INTEGER NOT NULL DEFAULT 0,
  phase             TEXT,
  plan_text         TEXT,
  phase_notes       TEXT
);

CREATE TABLE IF NOT EXISTS run_routing (
  run_id       INTEGER PRIMARY KEY REFERENCES runs(id),
  task_id      INTEGER NOT NULL,
  phase        TEXT,
  tier         TEXT,
  model_arg    TEXT,
  source       TEXT,
  actual_model TEXT,
  verified     TEXT,
  usage        TEXT
);
"""

# Status lines the model writes. The CTQ_ spelling is the legacy one from before the rebrand;
# it is still read so that output produced by an earlier version is understood.
_ESCALATE_RE = re.compile(r"^[ \t>*_`]*(?:MQ|CTQ)_ESCALATE:[ \t]*(.*?)[ \t*_`]*$",
                          re.IGNORECASE | re.MULTILINE)
_STATUS_LINE_RE = re.compile(r"^[ \t>*_`]*(?:MQ|CTQ)_(RESULT|ESCALATE):.*$",
                             re.IGNORECASE | re.MULTILINE)


def ensure_schema(conn):
    """Create the routing tables if this queue does not have them yet (additive only)."""
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                         "name='run_routing'").fetchone()
    if found is None:
        conn.executescript(SCHEMA)


def get(q, task_id):
    return q.conn.execute("SELECT * FROM task_routing WHERE task_id=?", (task_id,)).fetchone()


def history(row):
    """The recorded routing history of a task. Anything that is not a list of entries
    (a damaged value) reads as no history rather than stopping the queue."""
    try:
        data = json.loads(row["history"] or "[]") if row is not None else []
    except (ValueError, TypeError):
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def review_costs(q, since=None):
    """What the model-review calls cost, as Claude Code reported it (optionally only those
    made at or after the timestamp `since`)."""
    total = 0.0
    for row in q.conn.execute("SELECT history FROM task_routing"):
        for entry in history(row):
            try:
                if entry.get("type") != "review" or entry.get("cost_usd") is None:
                    continue
                if since and str(entry.get("at") or "") < since:
                    continue
                total += float(entry["cost_usd"])
            except (TypeError, ValueError):
                continue
    return total


def _add_history(q, task_id, entry):
    row = get(q, task_id)
    if row is None:
        return
    entries = history(row)
    entry = dict(entry)
    entry.setdefault("at", util.now_iso())
    entries.append(entry)
    q.conn.execute("UPDATE task_routing SET history=? WHERE task_id=?",
                   (json.dumps(entries[-60:]), task_id))


def _label(config, selected):
    """How a selection is shown: the tier name, plus the model when it is not the alias."""
    if selected in TIERS:
        model = models.tier_model(config, selected)
        return selected if model in (selected, None) else "%s (%s)" % (selected, model)
    return selected or "default"


# --------------------------------------------------------------------------- classification

def classify_and_store(q, task, config, reclassify=False):
    """Classify a task with the rules and store the result. Call inside a transaction.

    A manual override is never replaced: only the recommendation is refreshed."""
    text = task["prompt"]
    if str(task["source"] or "").startswith("plan:"):
        from . import plan_model
        text = plan_model.routing_text(text) or text
    decision = router.classify(text, task["acceptance"], config["routing_mode"],
                               project_root=q.root)
    phased = int(config["routing_phased"] == "auto" and decision["tier"] == "opus"
                 and decision["score"] >= 70)
    details = json.dumps({key: decision[key] for key in (
        "dimensions", "critical", "signals", "kinds", "doubts", "haiku_blockers", "facts")})
    floor = "haiku" if decision["haiku_eligible"] else "sonnet"
    stamp = util.now_iso()
    row = get(q, task["id"])
    selected = "sonnet" if phased else decision["tier"]
    if row is None:
        q.conn.execute(
            "INSERT INTO task_routing(task_id, mode, recommended_model, selected_model, "
            "selected_source, reason, complexity_score, min_tier, confidence, details, policy, "
            "policy_version, classified_at, classifier, review_pending, phased) "
            "VALUES(?, 'auto', ?, ?, 'auto', ?, ?, ?, ?, ?, ?, ?, ?, 'rules', ?, ?)",
            (task["id"], decision["tier"], selected, decision["reason"], decision["score"], floor,
             decision["confidence"], details, decision["policy"], decision["version"], stamp,
             int(decision["confidence"] != "high"), phased))
    else:
        manual = row["mode"] == "manual"
        q.conn.execute(
            "UPDATE task_routing SET recommended_model=?, reason=?, complexity_score=?, "
            "min_tier=?, confidence=?, details=?, policy=?, policy_version=?, classified_at=?, "
            "classifier='rules', review_pending=?, selected_model=?, selected_source=?, phased=?, "
            "phase=CASE WHEN ? THEN phase ELSE NULL END, blocked_reason=NULL WHERE task_id=?",
            (decision["tier"], decision["reason"], decision["score"], floor,
             decision["confidence"], details, decision["policy"], decision["version"], stamp,
             0 if manual else int(decision["confidence"] != "high"),
             row["selected_model"] if manual else selected,
             row["selected_source"] if manual else "auto",
             row["phased"] if manual else phased, int(manual), task["id"]))
        if reclassify:
            _add_history(q, task["id"], {"type": "reclassified", "to": decision["tier"],
                                         "policy": decision["policy"]})
    from . import workflow
    try:
        workflow.assign(q, task, decision, config, get(q, task["id"]))
    except Exception as exc:  # noqa: BLE001 - the workflow must never cost a classification
        q.log_event(task["id"], None, "workflow-error", str(exc)[:300])
    return get(q, task["id"])


def on_task_added(q, task_id, model=None, phased=None):
    """Classify a newly queued task (inside the transaction that added it)."""
    config = util.effective_config(q.dir)
    if not config["routing_enabled"] and model is None and not phased:
        return
    classify_and_store(q, q.get(task_id), config)
    if phased is not None:
        set_phased(q, task_id, phased)
    if model and model != "auto":
        set_model(q, [task_id], model, config)


def set_phased(q, task_id, enabled):
    with q.tx():
        row = get(q, task_id)
        if row is None:
            row = classify_and_store(q, q.get(task_id), util.effective_config(q.dir))
        if row["phase"] and not enabled:
            raise QueueError("task #%d is already in its %s phase" % (task_id, row["phase"]))
        q.conn.execute(
            "UPDATE task_routing SET phased=?, selected_model=CASE WHEN ?=1 AND mode='auto' "
            "AND selected_model='opus' THEN 'sonnet' WHEN ?=0 AND mode='auto' AND "
            "selected_source='auto' THEN recommended_model ELSE selected_model END "
            "WHERE task_id=?", (int(enabled), int(enabled), int(enabled), task_id))
        _add_history(q, task_id, {"type": "phased", "enabled": bool(enabled)})


def set_model(q, task_ids, model, config):
    """Manual override ("opus", "sonnet", "haiku" or a model name) or back to "auto"."""
    if not config["routing_allow_manual_override"]:
        raise QueueError("manual model overrides are switched off "
                         "(routing_allow_manual_override)")
    if model != "auto" and model not in TIERS:
        model = models.validate_model(model)
    changed = []
    with q.tx():
        for task_id in task_ids:
            task = q.get(task_id)
            if task["status"] in ("done", "cancelled"):
                raise QueueError("task #%d is %s; its model can no longer change"
                                 % (task_id, task["status"]))
            if task["status"] == "running" and config["routing_preserve_active_task_model"]:
                raise QueueError(
                    "task #%d is running; the model of a running task is never changed "
                    "(routing_preserve_active_task_model). Nothing was changed." % task_id)
            row = get(q, task_id)
            if row is None:
                row = classify_and_store(q, task, config)
            previous = row["selected_model"]
            if model == "auto":
                row = classify_and_store(q, task, config)
                q.conn.execute(
                    "UPDATE task_routing SET mode='auto', manual_override=0, "
                    "selected_model=CASE WHEN phased=1 THEN 'sonnet' ELSE recommended_model END, "
                    "selected_source='auto', blocked_reason=NULL, review_pending=? "
                    "WHERE task_id=?", (int(row["confidence"] != "high"), task_id))
            else:
                # A manual model applies to every phase. It does not remove the planning
                # session of a DEEP task, so a material plan still waits for approval.
                from . import workflow
                flow = workflow.get(q, task_id)
                try:
                    caps = json.loads(flow["capabilities"] or "{}") if flow is not None else {}
                except ValueError:
                    caps = {}
                keep_phases = int(bool(flow is not None and flow["profile"] == "deep"
                                       and caps.get("planning") == "full"))
                q.conn.execute(
                    "UPDATE task_routing SET mode='manual', manual_override=1, selected_model=?, "
                    "selected_source='manual', blocked_reason=NULL, review_pending=0, "
                    "phased=CASE WHEN phase IS NULL AND ?=0 THEN 0 ELSE phased END WHERE task_id=?",
                    (model, keep_phases, task_id))
            _add_history(q, task_id, {"type": "override", "from": previous, "to": model})
            q.log_event(task_id, None, "model",
                        "automatic selection restored" if model == "auto"
                        else "manually set to %s (was %s)" % (model, previous))
            changed.append(task_id)
    return changed


def reclassify(q, task_ids, config):
    """Re-run the rules for pending tasks (for example after changing the policy)."""
    done = []
    with q.tx():
        for task_id in task_ids:
            task = q.get(task_id)
            if task["status"] not in ("pending", "failed"):
                continue
            classify_and_store(q, task, config, reclassify=True)
            done.append(task_id)
    return done


def unblock(q, task_ids=None):
    """Clear "no usable model" blocks so the tasks are tried again."""
    with q.tx():
        if task_ids is None:
            # a plan waiting for the owner's approval is not a model block
            rows = q.conn.execute("SELECT task_id FROM task_routing WHERE blocked_reason "
                                  "IS NOT NULL AND blocked_reason NOT LIKE 'approval:%' "
                                  "AND blocked_reason NOT LIKE 'agents:%' "
                                  "AND blocked_reason NOT LIKE 'decision:%'"
                                  ).fetchall()
            task_ids = [row["task_id"] for row in rows]
        for task_id in task_ids:
            q.conn.execute("UPDATE task_routing SET blocked_reason=NULL WHERE task_id=?",
                           (task_id,))
    return list(task_ids)


def _hold_for_approval(q, task_id, config):
    """After the plan phase of a material deep task: wait for the owner."""
    from . import workflow
    reason = workflow.after_plan_phase(q, task_id, config)
    if reason:
        q.conn.execute("UPDATE task_routing SET blocked_reason=? WHERE task_id=?",
                       (reason, task_id))
        _add_history(q, task_id, {"type": "blocked", "reason": reason})
        q.log_event(task_id, None, "workflow-waiting", reason)


def _block(q, task_id, reason):
    q.conn.execute("UPDATE task_routing SET blocked_reason=? WHERE task_id=?", (reason, task_id))
    _add_history(q, task_id, {"type": "blocked", "reason": reason})
    q.log_event(task_id, None, "model-blocked", reason)


# --------------------------------------------------------------------------- model review

def _review(q, task, row, config, claude_bin):
    """Ask a small model to confirm a borderline rule-based decision. Never fatal."""
    reviewer = None
    restrictions = models.settings_restrictions(q.root)
    for tier in TIERS:  # cheapest usable tier
        candidate = models.tier_model(config, tier)
        if candidate and models.check(candidate, config, restrictions)[0] is not False:
            reviewer = candidate
            break
    note, tier, error, cost = None, row["selected_model"], None, None
    if reviewer is None:
        error = "no model available for the review"
    else:
        decision = {"tier": row["selected_model"], "score": row["complexity_score"] or 0,
                    "reason": row["reason"] or "", "haiku_eligible": row["min_tier"] == "haiku"}
        command = [models.resolve_claude(claude_bin), "-p", "--model", reviewer,
                   "--output-format", "json", "--no-session-persistence",
                   "--disable-slash-commands", "--strict-mcp-config", "--tools", "",
                   "--system-prompt", router.REVIEW_SYSTEM_PROMPT]
        env = dict(os.environ)
        # The current name and the legacy one (see engine.task_environment).
        env["MOA7AMED_QUEUE_RUNNER"] = env["CTQ_RUNNER"] = "1"
        try:
            result = subprocess.run(
                command, input=router.review_prompt(task["prompt"], task["acceptance"],
                                                    decision).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, env=env,
                cwd=q.root, **util.no_window_kwargs())
            raw = result.stdout.decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(raw)
                if isinstance(payload, list):
                    payload = [item for item in payload if isinstance(item, dict)][-1]
                text = payload.get("result") or ""
                cost = payload.get("total_cost_usd")
                failed = bool(payload.get("is_error"))
            except (ValueError, IndexError, AttributeError):
                text, failed = raw, result.returncode != 0
            if failed or result.returncode != 0:
                error = "the review call failed: %s" % (text.strip().splitlines() or ["no output"])[0][:160]
            else:
                reviewed, why = router.parse_review(text)
                if reviewed is None:
                    error = "the review answer could not be read"
                else:
                    tier, note = router.merge_review(decision, reviewed, why)
        except (OSError, subprocess.SubprocessError, QueueError) as exc:
            error = "the review call failed: %s" % exc
    with q.tx():
        entry = {"type": "review", "model": reviewer}
        if error:
            entry["error"] = error
        else:
            entry["result"] = tier
            if cost is not None:
                entry["cost_usd"] = cost
        if note and row["mode"] == "auto":
            entry["from"] = row["selected_model"]
            q.conn.execute(
                "UPDATE task_routing SET selected_model=?, reason=?, classifier='rules+model', "
                "review_pending=0 WHERE task_id=?",
                (tier, "%s (%s)" % (row["reason"], note), task["id"]))
            q.log_event(task["id"], None, "model", "review moved the task from %s to %s"
                        % (row["selected_model"], tier))
        else:
            q.conn.execute(
                "UPDATE task_routing SET review_pending=0, classifier=? WHERE task_id=?",
                ("rules" if error else "rules+model", task["id"]))
        _add_history(q, task["id"], entry)
    return get(q, task["id"])


# --------------------------------------------------------------------------- selection

def _usable(config, restrictions, tier):
    """(model argument, explanation) when the tier can be tried, else (None, why not)."""
    model = models.tier_model(config, tier)
    if model is None:
        return None, "the %s tier is switched off (routing_model_%s)" % (tier, tier)
    ok, why = models.check(model, config, restrictions)
    return (None, why) if ok is False else (model, why)


def _restore_after_fallback(q, task, row, config):
    """A fallback is temporary: once the recommended model can be used again
    (the refusal expired, or the evidence was reset) the task returns to it."""
    if row["selected_source"] != "fallback" or row["phase"] or row["recommended_model"] not in TIERS:
        return row
    target = "sonnet" if row["phased"] else row["recommended_model"]
    if target == row["selected_model"]:
        return row
    if not _usable(config, models.settings_restrictions(q.root), target)[0]:
        return row
    with q.tx():
        q.conn.execute("UPDATE task_routing SET selected_model=?, selected_source='auto' "
                       "WHERE task_id=?", (target, task["id"]))
        _add_history(q, task["id"], {"type": "restored", "from": row["selected_model"],
                                     "to": target})
    return get(q, task["id"])


def select_for_run(q, task, config):
    """Decide which model the next run of this task uses.

    Returns a dict with `action`: "run" (with model_arg, possibly None for the CLI default),
    "blocked" (the task was marked blocked) or "stop" (the runner cannot continue)."""
    claude_bin = config["claude_bin"] or util.env("CLAUDE_BIN") or "claude"
    row = get(q, task["id"])
    route = {"action": "run", "model_arg": None, "tier": None, "source": "default",
             "reason": None, "phase": None, "score": None, "disallowed": [],
             "escalation_hint": False, "note": ""}
    manual = (row is not None and row["mode"] == "manual"
              and config["routing_allow_manual_override"])
    if manual:
        selected, source = row["selected_model"], "manual"
        if row["phased"]:                    # the chosen model runs every phase
            route["phase"] = row["phase"] or "plan"
    elif config["model"]:
        selected, source = config["model"], "run-option"
    elif not config["routing_enabled"]:
        return route
    else:
        if row is None:
            with q.tx():
                row = classify_and_store(q, task, config)
        else:
            from . import workflow
            with q.tx():
                if workflow.ensure_assigned(q, task, config, row) is not None:
                    row = get(q, task["id"])
        row = _restore_after_fallback(q, task, row, config)
        wants_review = (config["routing_classifier"] == "always" and row["classifier"] == "rules"
                        and not any(item.get("type") == "review" for item in history(row)))
        if config["routing_classifier"] != "off" and (row["review_pending"] or wants_review) \
                and not row["phase"]:
            row = _review(q, task, row, config, claude_bin)
        selected, source = row["selected_model"], row["selected_source"] or "auto"
        if row["phased"]:
            route["phase"] = row["phase"] or "plan"
            if source != "manual":                  # your model choice holds in every phase
                selected = PHASE_TIER.get(route["phase"], selected)
    if row is not None:
        route["reason"], route["score"] = row["reason"], row["complexity_score"]

    info = models.cli_info(claude_bin)
    if not info.get("supports_model") and not info.get("error"):
        route["note"] = ("this Claude Code version (%s) has no --model option; the task runs on "
                         "its default model" % info.get("version"))
        route["source"] = "unsupported"
        return route

    restrictions = models.settings_restrictions(q.root)
    if selected in TIERS:
        model, why = _usable(config, restrictions, selected)
    else:
        ok, why = models.check(selected, config, restrictions)
        model = None if ok is False else selected
    tier = models.tier_of(config, selected)

    if model is None:
        label = _label(config, selected)
        if source == "manual":
            reason = ("the manually selected model %s cannot be used: %s. Choose another with "
                      "`moa7amed-queue model <auto|opus|sonnet|haiku> %d`."
                      % (label, str(why).rstrip("."), task["id"]))
            with q.tx():
                _block(q, task["id"], reason)
            return {"action": "blocked", "message": reason}
        if source == "run-option":
            return {"action": "stop", "reason": "model-unavailable",
                    "message": "the model set for this run (%s) cannot be used: %s. Remove the "
                               "model option to let each task use its routed model." % (label, why)}
        if route["phase"] in ("plan", "review"):
            # The phased workflow needs the top tier; without it the task runs as one phase.
            with q.tx():
                q.conn.execute("UPDATE task_routing SET phased=0, phase=NULL WHERE task_id=?",
                               (task["id"],))
                _add_history(q, task["id"], {"type": "phased", "enabled": False,
                                             "reason": "%s unavailable: %s" % (label, why)})
            route["phase"] = None
            selected = row["selected_model"]
            model, why = _usable(config, restrictions, selected) if selected in TIERS else (selected, "")
            tier = models.tier_of(config, selected)
        if model is None:
            chosen = None
            if config["routing_fallback_on_unavailable"] and tier in TIERS:
                for candidate in models.fallback_order(tier, row["min_tier"]):
                    candidate_model, _ = _usable(config, restrictions, candidate)
                    if candidate_model:
                        chosen = (candidate, candidate_model)
                        break
            if chosen is None:
                reason = ("no permitted model is available: %s cannot be used (%s)%s"
                          % (label, why, "" if config["routing_fallback_on_unavailable"]
                             else "; fallback is switched off (routing_fallback_on_unavailable)"))
                with q.tx():
                    _block(q, task["id"], reason)
                return {"action": "blocked", "message": reason}
            with q.tx():
                q.conn.execute("UPDATE task_routing SET selected_model=?, selected_source="
                               "'fallback' WHERE task_id=?", (chosen[0], task["id"]))
                _add_history(q, task["id"], {"type": "fallback", "from": selected,
                                             "to": chosen[0], "reason": why})
                q.log_event(task["id"], None, "model", "%s cannot be used (%s); falling back to %s"
                            % (label, why, chosen[0]))
            route["note"] = "%s unavailable; using %s" % (label, chosen[0])
            selected, tier, model, source = chosen[0], chosen[0], chosen[1], "fallback"

    route.update(model_arg=model, tier=tier, source=source)
    if route["phase"] in ("plan", "review"):
        route["disallowed"] = list(READ_ONLY_DISALLOWED) if route["phase"] == "plan" else []
    if (source in ("auto", "fallback", "escalation") and tier in TIERS and row is not None
            and config["routing_allow_escalation"]
            and row["escalation_count"] < config["routing_max_escalations"]
            and route["phase"] in (None, "implement")):
        route["escalation_hint"] = any(
            _usable(config, restrictions, higher)[0] for higher in models.escalation_order(tier))
    return route


def record_start(q, run_id, task_id, route):
    q.conn.execute(
        "INSERT INTO run_routing(run_id, task_id, phase, tier, model_arg, source) "
        "VALUES(?,?,?,?,?,?)", (run_id, task_id, route["phase"], route["tier"],
                                route["model_arg"], route["source"]))


# --------------------------------------------------------------------------- after a run

def _review_justified(row, config):
    mode = config["routing_phased_review"]
    if mode == "always":
        return True
    if mode == "never":
        return False
    try:
        details = json.loads(row["details"] or "{}")
    except ValueError:
        details = {}
    dims = details.get("dimensions") or {}
    return ((row["complexity_score"] or 0) >= 85 or dims.get("security", 0) >= 8
            or dims.get("database", 0) >= 8 or dims.get("regression_risk", 0) >= 7)


def _failures_on_tier(row, tier):
    """How many runs in a row have already failed on this tier (before the current one)."""
    count = 0
    for entry in reversed(history(row)):
        if entry.get("type") in ("escalation", "override", "reclassified"):
            break
        if "run" not in entry:
            continue
        if entry.get("tier") == tier and entry.get("outcome") in ("failed", "timeout"):
            count += 1
        else:
            break
    return count


def settle(q, ctx):
    """Work out what a finished run means for the task's model.

    `ctx` carries the engine's own verdict. The returned dict may change it (a refused
    model is not a failed attempt; a phase that ended is not a finished task; an
    escalation keeps a task alive) and holds `apply`, to be called inside the
    engine's transaction, which stores every routing change at once.
    """
    task, run, parsed, config = ctx["task"], ctx["run"], ctx["parsed"], ctx["config"]
    out = {"outcome": ctx["outcome"], "error": ctx["error"], "status": ctx["status"],
           "attempts": ctx["attempts"], "not_before": ctx["not_before"],
           "max_attempts": task["max_attempts"], "will_retry": ctx["will_retry"],
           "task_done": ctx["outcome"] == "done", "note": "", "model": None,
           "actual_model": None, "apply": lambda queue: None,
           "failure": ctx["outcome"] in ("failed", "timeout")}
    started = q.conn.execute("SELECT * FROM run_routing WHERE run_id=?", (run["id"],)).fetchone()
    if started is None:
        return out
    row = get(q, task["id"])
    model, tier, source, phase = (started["model_arg"], started["tier"], started["source"],
                                  started["phase"])
    out["model"] = model
    operations = []
    entry = {"run": run["id"], "phase": phase, "requested": model, "tier": tier, "source": source}

    def finish_with(result):
        def apply(queue):
            for operation in operations:
                operation(queue)
        result["apply"] = apply
        return result

    # ---- 1. the CLI refused the model -------------------------------------------------
    if (ctx["outcome"] == "failed" and model
            and models.is_model_error(parsed, ctx["stderr_tail"])):
        message = models.model_error_text(parsed, ctx["stderr_tail"]).rstrip(".")
        models.note_unavailable(model, message)
        entry.update(outcome="model-unavailable", error=message)
        out["outcome"] = "model-unavailable"
        out["error"] = "the model %s could not be used: %s" % (model, message)
        operations.append(lambda queue: queue.conn.execute(
            "UPDATE run_routing SET verified='refused' WHERE run_id=?", (run["id"],)))
        if not ctx["files"] and not ctx["stash_ref"]:
            # Nothing was changed, so trying another model cannot duplicate work,
            # and the attempt is not counted against the task.
            out.update(attempts=max(0, task["attempts"] - 1), not_before=None, status="pending",
                       will_retry=True, failure=False)
            restrictions = models.settings_restrictions(q.root)
            if source == "manual":
                reason = ("the manually selected model %s cannot be used: %s. Choose another "
                          "with `moa7amed-queue model <auto|opus|sonnet|haiku> %d`."
                          % (model, message, task["id"]))
                operations.append(lambda queue: _block(queue, task["id"], reason))
                out.update(will_retry=False, blocked=reason,
                           note="blocked until another model is chosen for it")
            elif source == "run-option":
                out["note"] = "the model set for this run cannot be used"
            elif row is not None:
                chosen = None
                if config["routing_fallback_on_unavailable"] and tier in TIERS \
                        and phase in (None, "implement"):
                    for candidate in models.fallback_order(tier, row["min_tier"]):
                        if _usable(config, restrictions, candidate)[0]:
                            chosen = candidate
                            break
                if phase in ("plan", "review"):
                    # No top tier: continue as a single-phase task on its own model.
                    operations.append(lambda queue: queue.conn.execute(
                        "UPDATE task_routing SET phased=0, phase=NULL WHERE task_id=?",
                        (task["id"],)))
                    out["note"] = "%s unavailable; the phased workflow is dropped" % model
                elif chosen:
                    operations.append(lambda queue: queue.conn.execute(
                        "UPDATE task_routing SET selected_model=?, selected_source='fallback' "
                        "WHERE task_id=?", (chosen, task["id"])))
                    operations.append(lambda queue: _add_history(
                        queue, task["id"], {"type": "fallback", "from": tier, "to": chosen,
                                            "reason": message}))
                    out["note"] = "%s unavailable; falling back to %s" % (model, chosen)
                    out["fallback_to"] = chosen
                else:
                    reason = "no permitted model is available: %s was refused (%s)" % (model, message)
                    operations.append(lambda queue: _block(queue, task["id"], reason))
                    out.update(will_retry=False, blocked=reason,
                               note="blocked: no permitted model is available")
        operations.insert(0, lambda queue: _add_history(queue, task["id"], entry))
        return finish_with(out)

    # ---- 2. record what actually ran --------------------------------------------------
    actual, verdict = models.verify(model, config, parsed)
    usage = models.usage_summary(parsed)
    out["actual_model"] = actual
    entry.update(outcome=ctx["outcome"], actual=actual, verified=verdict)
    if usage.get("cost_usd") is not None:
        entry["cost_usd"] = usage["cost_usd"]
    if model and verdict == "confirmed":
        models.note_confirmed(model, actual)
    elif model and verdict == "mismatch":
        models.note_unavailable(model, "asked for %s, the CLI ran %s" % (model, actual),
                                kind="mismatch")
        out["note"] = ("asked for %s but the CLI ran %s; %s is treated as unavailable from now on"
                       % (model, actual, model))
    operations.append(lambda queue: queue.conn.execute(
        "UPDATE run_routing SET actual_model=?, verified=?, usage=? WHERE run_id=?",
        (actual, verdict, json.dumps(usage), run["id"])))
    if row is None:
        return finish_with(out)
    if actual:
        operations.append(lambda queue: queue.conn.execute(
            "UPDATE task_routing SET actual_model=? WHERE task_id=?", (actual, task["id"])))

    # ---- 3. phases ---------------------------------------------------------------------
    if row["phased"] and phase:
        text = _STATUS_LINE_RE.sub("", (parsed or {}).get("result") or "").strip()
        if ctx["outcome"] == "done":
            if phase == "plan":
                next_phase = "implement"
                operations.append(lambda queue: queue.conn.execute(
                    "UPDATE task_routing SET plan_text=? WHERE task_id=?",
                    (text[:20000], task["id"])))
                operations.append(lambda queue: _hold_for_approval(queue, task["id"], config))
            elif phase == "implement":
                next_phase = "review" if _review_justified(row, config) else None
                notes = json.dumps({"summary": ctx["summary"], "commit": ctx["commit_sha"],
                                    "base": run["head_before"], "report": text[:6000]})
                operations.append(lambda queue: queue.conn.execute(
                    "UPDATE task_routing SET phase_notes=? WHERE task_id=?", (notes, task["id"])))
            else:
                next_phase = None
            operations.append(lambda queue: queue.conn.execute(
                "UPDATE task_routing SET phase=? WHERE task_id=?", (next_phase, task["id"])))
            if next_phase:
                out.update(status="pending", attempts=max(0, task["attempts"] - 1),
                           not_before=None, task_done=False,
                           note="%s phase done; next: %s" % (phase, next_phase))
        elif ctx["outcome"] in ("failed", "timeout") and phase == "review":
            # The review rejected the work: implement again with the reviewer's findings.
            operations.append(lambda queue: queue.conn.execute(
                "UPDATE task_routing SET phase='implement' WHERE task_id=?", (task["id"],)))

    # ---- 4. escalation -----------------------------------------------------------------
    if (ctx["outcome"] in ("failed", "timeout") and row["mode"] == "auto"
            and source in ("auto", "fallback", "escalation") and tier in TIERS
            and phase in (None, "implement") and config["routing_allow_escalation"]
            and row["escalation_count"] < config["routing_max_escalations"]
            and ctx["exit_code"] != 127):
        asked = None
        for asked in _ESCALATE_RE.finditer((parsed or {}).get("result") or ""):
            pass
        failures = _failures_on_tier(row, tier) + 1
        denied = bool((parsed or {}).get("denials"))
        if asked is not None:
            why = "the model reported the task needs a stronger model: %s" % (
                asked.group(1).strip() or "no reason given")
        elif failures >= config["routing_escalate_after_failures"] and not denied:
            why = "%d failed attempts on %s; last: %s" % (
                failures, tier, (ctx["error"] or "").splitlines()[0][:160] if ctx["error"] else "")
        elif row["escalation_count"] > 0 and out["status"] == "failed" and not denied:
            # Already escalated once, failed again, and out of attempts: the evidence
            # that the task is harder than classified has only grown.
            why = "failed again on %s after an earlier escalation; last: %s" % (
                tier, (ctx["error"] or "").splitlines()[0][:160] if ctx["error"] else "")
        else:
            why = None
        if why:
            restrictions = models.settings_restrictions(q.root)
            target = next((higher for higher in models.escalation_order(tier)
                           if _usable(config, restrictions, higher)[0]), None)
            if target:
                operations.append(lambda queue: queue.conn.execute(
                    "UPDATE task_routing SET selected_model=?, selected_source='escalation', "
                    "escalation_count=escalation_count+1 WHERE task_id=?", (target, task["id"])))
                operations.append(lambda queue: _add_history(
                    queue, task["id"], {"type": "escalation", "from": tier, "to": target,
                                        "reason": why}))
                operations.append(lambda queue: queue.log_event(
                    task["id"], run["id"], "escalated", "%s -> %s: %s" % (tier, target, why)))
                if out["status"] == "failed":
                    out.update(status="pending", will_retry=True, not_before=None,
                               max_attempts=task["attempts"] + max(
                                   1, int(config["routing_escalation_attempts"])))
                out["note"] = "escalating from %s to %s (%s)" % (tier, target, why)
                out["escalated_to"] = target
    operations.insert(0, lambda queue: _add_history(queue, task["id"], entry))
    return finish_with(out)


# --------------------------------------------------------------------------- prompt additions

def prompt_sections(q, task, route):
    """Extra blocks for the task prompt: (context lines, rule lines, replacement for the
    closing instructions or None)."""
    context, rules = [], []
    row = get(q, task["id"])
    phase = route.get("phase")
    if row is not None and phase in ("implement", "review") and row["plan_text"]:
        context += ["", "<approved_plan>",
                    "This plan was produced for the task in an earlier planning phase.",
                    row["plan_text"], "</approved_plan>"]
    if row is not None and phase == "review" and row["phase_notes"]:
        try:
            notes = json.loads(row["phase_notes"])
        except ValueError:
            notes = {}
        context += ["", "<implementation_to_review>",
                    "The implementation phase reported: %s" % (notes.get("summary") or "(no summary)")]
        if notes.get("commit"):
            context.append("Its changes are in commit %s (inspect with: git show %s)."
                           % (notes["commit"], notes["commit"][:12]))
        if notes.get("report"):
            context += ["Its full report, including any test results:", notes["report"]]
        context.append("</implementation_to_review>")
    if phase == "plan":
        rules = [
            "- This run is the PLANNING phase of a larger task. Do not create, edit or delete "
            "any file.",
            "- Study the code and write a concrete technical plan: the files and modules to "
            "change, the steps in order, the risks, and how each acceptance criterion will be "
            "verified. A different model implements the plan next, so it must stand on its own.",
            "- Your final message is the plan. End it with: MQ_RESULT: DONE - plan ready",
        ]
    elif phase == "implement":
        rules = ["- This run is the IMPLEMENTATION phase. Follow the approved plan; where the "
                 "plan turns out to be wrong, do what is right and say what you changed and why."]
    elif phase == "review":
        rules = ["- This run is the REVIEW phase. Check the implementation against the task, "
                 "the acceptance criteria and the plan; run the tests if you are able to.",
                 "- Fix real defects you find. Report DONE when the work is acceptable (after "
                 "your fixes) and FAILED, with the reasons, when it is not."]
    if route.get("escalation_hint"):
        rules.append(
            "- If the task clearly needs deeper reasoning than you can give reliably (a failing "
            "test alone is not a reason), add a line `MQ_ESCALATE: <why>` before the status "
            "line and report FAILED. A stronger model will then take over.")
    if row is not None and row["escalation_count"] and phase in (None, "implement"):
        last = next((item for item in reversed(history(row)) if item.get("type") == "escalation"),
                    None)
        if last:
            context += ["", "<escalation>",
                        "A smaller model (%s) could not complete this task: %s"
                        % (last.get("from"), last.get("reason")), "</escalation>"]
    return context, rules


# --------------------------------------------------------------------------- reporting

def describe(q, task, config=None):
    """Routing metadata of a task for display and JSON output (None if never classified)."""
    row = get(q, task["id"])
    if row is None:
        return None
    try:
        details = json.loads(row["details"] or "{}")
    except ValueError:
        details = {}
    entries = history(row)
    runs = q.conn.execute("SELECT * FROM run_routing WHERE task_id=? ORDER BY run_id",
                          (task["id"],)).fetchall()
    usage = []
    for run in runs:
        try:
            data = json.loads(run["usage"] or "{}")
        except ValueError:
            data = {}
        usage.append({"run": run["run_id"], "phase": run["phase"], "requested": run["model_arg"],
                      "actual": run["actual_model"], "verified": run["verified"], "usage": data})
    return {
        "modelSelectionMode": row["mode"],
        "recommendedModel": row["recommended_model"],
        "selectedModel": row["selected_model"],
        "selectedBy": row["selected_source"],
        "actualModel": row["actual_model"],
        "modelReason": row["reason"],
        "complexityScore": row["complexity_score"],
        "routingPolicyVersion": "%s/%s" % (row["policy_version"], row["policy"]),
        "classificationTimestamp": row["classified_at"],
        "manualOverride": bool(row["manual_override"]),
        "escalationCount": row["escalation_count"],
        "executionModelHistory": entries,
        "classifier": row["classifier"],
        "confidence": row["confidence"],
        "blockedReason": row["blocked_reason"],
        "phased": bool(row["phased"]),
        "phase": row["phase"],
        "dimensions": details.get("dimensions"),
        "criticalSignals": details.get("critical"),
        "runs": usage,
    }


def short_label(q, task):
    """Compact model column for the status table: `sonnet`, `opus*` (manual), `haiku>sonnet`."""
    row = get(q, task["id"])
    if row is None:
        return "-"
    label = str(row["selected_model"] or "-")
    moves = [item for item in history(row) if item.get("type") in ("escalation", "fallback")]
    if moves and moves[-1].get("to") == row["selected_model"]:
        label = "%s>%s" % (moves[-1].get("from"), label)
    if row["phased"]:
        label = "phased"
    if row["mode"] == "manual":
        label += "*"
    if row["blocked_reason"]:
        label += "!"
    return label
