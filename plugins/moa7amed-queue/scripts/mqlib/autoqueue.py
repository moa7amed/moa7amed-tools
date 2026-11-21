"""Auto Queue: a project that opted in gets a runner started for it when a task is saved.

Nothing here executes tasks. The existing runner does that (`moa7amed-queue run`, in watch
mode, with an idle timeout); this module only decides whether one has to be started, starts
it detached, and keeps the state that has to survive restarts:

    task_requests       request id -> task id, so a repeated submission with the same id is
                        not saved twice (an intentional repeat without an id always is)
    meta auto_paused    pause: no runner claims a new task until resume
    meta auto_stop      stop after the current task: the named runner exits after it
    meta auto_launch    a runner has been started and has not taken the lock yet
    meta auto_failures  consecutive launches that failed; at the limit, launching stops
    meta auto_budget    since when the automatic-execution allowance is counted

The order of a submission is fixed: the task is committed first, then the lock is checked,
then (outside any transaction) a process may be started. A runner that has nothing to do
releases its lock inside the same transaction that confirms nothing is runnable
(engine.release_if_idle), so a task saved at that moment is either seen by the runner or
finds no runner and starts one. It is never left behind.
"""
from __future__ import annotations

import json
import os

from . import util
from .util import QueueError

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_requests (
  request_id TEXT PRIMARY KEY,
  task_id    INTEGER NOT NULL,
  source     TEXT,
  created_at TEXT NOT NULL
);
"""

RUNNER_KIND = "auto"
LAUNCH_GRACE_SECONDS = 120     # a started runner has this long to take the lock
WAKE_FILE = "wake"

# Variables that tie a process to one interactive Claude Code session. A runner started from
# a session (a hook, a skill) must not carry them into the unattended task sessions it starts:
# they name that session, its process and its messaging channel. Provider and configuration
# settings (ANTHROPIC_*, CLAUDE_CONFIG_DIR, CLAUDE_CODE_USE_BEDROCK, ...) are kept.
SESSION_VARIABLES = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_PROJECT_DIR",
                     "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_ENTRYPOINT",
                     "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED",
                     "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_CHILD_NOTES_DIR",
                     "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                     "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_QUESTION_PREVIEW_FORMAT",
                     "CLAUDE_CODE_EMIT_STARTUP_TIMING", "CLAUDE_CODE_STARTUP_FAILURE_RESULTS",
                     "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "CLAUDE_CODE_ENABLE_TASKS",
                     "CLAUDE_CODE_SSE_PORT")


def ensure_schema(conn):
    conn.executescript(SCHEMA)


def detached_env(base=None):
    env = dict(os.environ if base is None else base)
    for name in SESSION_VARIABLES:
        env.pop(name, None)
    return env


# --------------------------------------------------------------------------- state

def _meta(q, key):
    row = q.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return None


def _set_meta(q, key, value):
    if value is None:
        q.conn.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        q.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                       (key, json.dumps(value)))


def enabled(config):
    return bool(config.get("auto_queue_enabled"))


def paused(q):
    return _meta(q, "auto_paused")


def stop_requested(q, runner_id):
    stop = _meta(q, "auto_stop")
    return bool(stop and stop.get("runner_id") == runner_id)


def clear_stop(q, runner_id):
    stop = _meta(q, "auto_stop")
    if stop and stop.get("runner_id") == runner_id:
        _set_meta(q, "auto_stop", None)


def wake(q):
    """Tell a waiting runner to look for work now instead of at its next poll."""
    try:
        util.write_text_file(os.path.join(q.dir, WAKE_FILE), util.now_iso() + "\n")
    except OSError:
        pass


def wake_stamp(q):
    try:
        return os.path.getmtime(os.path.join(q.dir, WAKE_FILE))
    except OSError:
        return None


# --------------------------------------------------------------------------- request ids

def known_request(q, request_id):
    row = q.conn.execute("SELECT task_id FROM task_requests WHERE request_id=?",
                         (request_id,)).fetchone()
    return row["task_id"] if row else None


def remember_request(q, request_id, task_id, source):
    q.conn.execute("INSERT INTO task_requests(request_id, task_id, source, created_at) "
                   "VALUES(?,?,?,?)", (request_id, task_id, source, util.now_iso()))


# --------------------------------------------------------------------------- allowance

def allowance(q, config):
    """The automatic-execution allowance: what Claude Code reported for queued work since
    the allowance was granted, against `auto_queue_budget_usd`. Runs with no reported cost
    are counted separately, never estimated. None when no allowance is set."""
    limit = config.get("auto_queue_budget_usd")
    if not limit:
        return None
    since = (_meta(q, "auto_budget") or {}).get("since") or "1970-01-01T00:00:00Z"
    row = q.conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS spent, "
        "SUM(CASE WHEN cost_usd IS NULL AND finished_at IS NOT NULL THEN 1 ELSE 0 END) AS unknown "
        "FROM runs WHERE started_at >= ?", (since,)).fetchone()
    from . import agents, routing
    spent = float(row["spent"] or 0) + routing.review_costs(q, since) + agents.spend_since(q, since)
    return {"limit_usd": float(limit), "spent_usd": round(spent, 6),
            "remaining_usd": round(max(float(limit) - spent, 0.0), 6),
            "unknown_runs": int(row["unknown"] or 0), "since": since,
            "exhausted": spent >= float(limit)}


def renew_allowance(q, by):
    with q.tx():
        _set_meta(q, "auto_budget", {"since": util.now_iso(), "by": by})
        q.log_event(None, None, "auto-allowance", "allowance counted again from now (%s)" % by)


# --------------------------------------------------------------------------- runner state

def _reservation_alive(q, reservation):
    if not reservation:
        return False
    age = (util.now() - util.parse_iso(reservation["at"])).total_seconds()
    if reservation.get("pid") is None:
        return age < 30              # the launching process is between reserving and starting
    if age > LAUNCH_GRACE_SECONDS:
        return False
    return util.process_alive(reservation["pid"], reservation.get("token"))


def runner_state(q, config=None):
    """What runs this project right now, for status lines. Never starts anything."""
    from . import engine
    config = config or util.effective_config(q.dir)
    info = engine.lock_info(q)
    state = {"enabled": enabled(config), "paused": paused(q), "runner": "none",
             "lock": info, "failures": _meta(q, "auto_failures"),
             "stop_after_current": _meta(q, "auto_stop"),
             "last_stop": _meta(q, "auto_last_stop"), "allowance": allowance(q, config)}
    if info and info["alive"]:
        state["runner"] = "running" if info["current_task_id"] else "idle"
        state["current_task_id"] = info["current_task_id"]
        state["runner_kind"] = info["kind"]
    elif _reservation_alive(q, _meta(q, "auto_launch")):
        state["runner"] = "starting"
    elif info:
        state["runner"] = "stale"
    return state


def blockers(q, config=None):
    """Why nothing (or not everything) can run, as short actionable lines."""
    from . import engine
    config = config or util.effective_config(q.dir)
    lines = []
    if paused(q):
        lines.append("Automatic execution is paused: `moa7amed-queue auto resume`.")
    failures = _meta(q, "auto_failures") or {}
    if failures.get("count", 0) >= config["auto_queue_launch_retry_limit"] > 0:
        lines.append("Automatic launching stopped after %d failed launch(es): %s. Fix the cause, "
                     "then `moa7amed-queue auto resume`." % (failures["count"],
                                                             failures.get("error")))
    spent = allowance(q, config)
    if spent and spent["exhausted"]:
        lines.append("The automatic-execution allowance of $%.2f is used up ($%.4f reported%s). "
                     "Renew it with `moa7amed-queue auto budget --renew` (or set another amount)."
                     % (spent["limit_usd"], spent["spent_usd"], _unknown(spent)))
    dirty = _dirty_reason(q, config)
    if dirty:
        lines.append(dirty)
    last = _meta(q, "auto_last_stop")
    if last and last.get("reason") not in ("idle", None) and not dirty:
        lines.append("The last runner stopped: %s" % last.get("message"))
    if q.counts()["pending"] and q.next_eligible() is None and not q.next_retry_time():
        lines.append(engine._idle(q)["message"] + ".")
    return lines


def _unknown(spent):
    return ("; %d run(s) reported no cost and are not counted" % spent["unknown_runs"]
            if spent["unknown_runs"] else "")


def _dirty_reason(q, config):
    """In-place work refuses to start on uncommitted changes (on_dirty=stop). Checking that
    before starting a runner avoids a runner that starts only to stop again."""
    if config["workspace"] != "inplace" or config["on_dirty"] != "stop":
        return None
    from . import gitops
    try:
        if not gitops.is_repo(q.root):
            return None
        # Bounded: in a very large repository this check is skipped rather than holding up
        # a submission; the runner checks the workspace again before every task.
        dirty = gitops.status_lines(q.root, timeout=5)
    except QueueError:
        return None
    if not dirty:
        return None
    return ("The project has uncommitted changes, and tasks run in the project folder only on "
            "a clean workspace (%d changed path(s)). Commit or stash them (`moa7amed-queue "
            "reconcile` shows what a stopped task left), or let tasks run in a separate "
            "worktree: `moa7amed-queue config set workspace worktree`." % len(dirty))


# --------------------------------------------------------------------------- launching

def _record_failure(q, error):
    failures = _meta(q, "auto_failures") or {"count": 0}
    failures = {"count": int(failures.get("count", 0)) + 1, "error": str(error)[:300],
                "at": util.now_iso()}
    _set_meta(q, "auto_failures", failures)
    q.log_event(None, None, "auto-launch-failed", failures["error"])
    return failures


def ensure_runner(q, config=None, task_id=None):
    """Make sure a runner is (or is about to be) working on this project, if Auto Queue is on.

    Returns {"state": ..., "message": ...}. States: manual (not enabled), in-runner, paused,
    stopped (launch limit reached), allowance, blocked, dirty, nothing, active, starting,
    started, failed. Must be called after the task's own transaction committed."""
    config = config or util.effective_config(q.dir)
    if not enabled(config):
        return {"state": "manual", "message": None}
    if util.env("RUNNER"):
        return {"state": "in-runner", "message": "The runner executing this task picks it up."}
    if paused(q):
        return {"state": "paused", "message": "Automatic execution is paused."}
    failures = _meta(q, "auto_failures") or {}
    limit = config["auto_queue_launch_retry_limit"]
    if limit and failures.get("count", 0) >= limit:
        return {"state": "stopped",
                "message": "Automatic launching stopped after %d failed launch(es) (%s). Fix "
                           "the cause, then `moa7amed-queue auto resume`."
                           % (failures["count"], failures.get("error"))}
    spent = allowance(q, config)
    if spent and spent["exhausted"]:
        return {"state": "allowance",
                "message": "Not started: the automatic-execution allowance of $%.2f is used up "
                           "($%.4f reported%s). Renew: `moa7amed-queue auto budget --renew`."
                           % (spent["limit_usd"], spent["spent_usd"], _unknown(spent))}
    from . import engine
    with q.tx():
        info = engine.lock_info(q)
        if info and info["alive"]:
            wake(q)
            if info["current_task_id"] and info["current_task_id"] != task_id:
                return {"state": "active", "waiting_behind": info["current_task_id"],
                        "message": "Waiting behind task #%d." % info["current_task_id"]}
            return {"state": "active", "message": "The runner is active and starts it next."}
        reservation = _meta(q, "auto_launch")
        if _reservation_alive(q, reservation):
            return {"state": "starting", "message": "Runner starting."}
        if reservation:
            # The process started earlier never took the lock and is gone.
            _set_meta(q, "auto_launch", None)
            if not reservation.get("lost_race"):
                failures = _record_failure(q, "the runner exited before it started working "
                                              "(see launch.out in the queue's data folder)")
                if limit and failures["count"] >= limit:
                    return {"state": "stopped",
                            "message": "Runner launch failed %d time(s): automatic launching "
                                       "is stopped. Fix the cause, then `moa7amed-queue auto "
                                       "resume`." % failures["count"]}
        # A runner that died leaves its lock and its task marked running. Only a starting
        # runner recovers them (engine.acquire), so one is started even when nothing is
        # pending, and even on uncommitted changes: the task cut off may have left them, and
        # the runner records the interruption and then refuses to work over them as usual.
        recovery = info is not None or q.conn.execute(
            "SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone() is not None
        if not recovery and q.next_eligible() is None and not q.next_retry_time():
            pending = q.counts()["pending"]
            if not pending:
                return {"state": "nothing", "message": None}
            return {"state": "blocked", "message": "Blocked: " + engine._idle(q)["message"]}
        dirty = None if recovery else _dirty_reason(q, config)
        if dirty:
            return {"state": "dirty", "message": "Not started: " + dirty}
        _set_meta(q, "auto_launch", {"pid": None, "at": util.now_iso()})
    # Outside the transaction: starting a process can take a moment.
    from . import pyrunner
    try:
        result = pyrunner.launch(q, None, {"watch": True, "auto": True,
                                           "idle_timeout": config["auto_queue_idle_timeout_seconds"]},
                                 hidden=True, engine_kind="python")
    except (OSError, QueueError) as exc:
        with q.tx():
            _set_meta(q, "auto_launch", None)
            failures = _record_failure(q, exc)
        return {"state": "failed", "error": str(exc),
                "message": "Runner launch failed: %s. The task stays pending; start it with "
                           "`moa7amed-queue launch`, or fix the cause and submit again "
                           "(%d of %d automatic attempts used)."
                           % (exc, failures["count"], limit)}
    with q.tx():
        current = _meta(q, "auto_launch")
        if current is not None and current.get("pid") is None:   # not yet taken by the runner
            _set_meta(q, "auto_launch", {"pid": result["pid"], "at": current["at"],
                                         "token": util.process_token(result["pid"])})
        q.log_event(task_id, None, "auto-launch", "runner started automatically (pid %d)"
                    % result["pid"])
    return {"state": "started", "pid": result["pid"], "message": "Runner started."}


def runner_acquired(q, pid):
    """Called by an automatic runner once it holds the lock (inside acquire's transaction)."""
    _set_meta(q, "auto_launch", None)
    _set_meta(q, "auto_failures", None)


def runner_lost_race(q, pid):
    """An automatic runner found the lock taken: not a failure, and nothing is lost."""
    with q.tx():
        reservation = _meta(q, "auto_launch")
        if reservation and reservation.get("pid") in (None, pid):
            _set_meta(q, "auto_launch", None)


def record_stop(q, reason, message):
    with q.tx():
        _set_meta(q, "auto_last_stop", {"reason": reason, "message": message,
                                        "at": util.now_iso()})


# --------------------------------------------------------------------------- owner actions

def describe_enable(q, config, budget):
    """What enabling means for this project, before anything is changed."""
    lines = ["Auto Queue for %s" % q.root,
             "  When a task is saved here (queue: prefix, /moa7amed-queue:queue-add or "
             "`moa7amed-queue add`), a runner is started in the background if none is "
             "working, and executes eligible tasks one at a time.",
             "  Runner permissions: --permission-mode %s%s%s; nothing is widened."
             % (config["permission_mode"],
                ", allowed tools: %s" % ", ".join(config["allowed_tools"])
                if config["allowed_tools"] else "",
                ", disallowed tools: %s" % ", ".join(config["disallowed_tools"])
                if config["disallowed_tools"] else ""),
             "  Workspace: %s." % ("a separate git worktree (you can keep working here)"
                                   if config["workspace"] == "worktree" else
                                   "the project folder; a task starts only when it has no "
                                   "uncommitted changes (--worktree runs tasks in a separate "
                                   "worktree instead)"),
             "  Cost: %s" % (
                 "an allowance of $%.2f for automatic runs. Automatic runners start no new task "
                 "once Claude Code has reported that much for this project's queued work since "
                 "the allowance was granted; it is not renewed by restarting, resuming or "
                 "enabling again (`moa7amed-queue auto budget --renew` does that)." % budget
                 if budget else
                 "no allowance for automatic runs (--no-budget): only the per-task and "
                 "per-session limits below apply."),
             "  Per task: %s; per runner session: %s." % (
                 "$%g (max_budget_usd)" % config["max_budget_usd"] if config["max_budget_usd"]
                 else "no limit set (max_budget_usd)",
                 "$%g (cost_limit_run_usd, starts again with each runner)"
                 % config["cost_limit_run_usd"] if config["cost_limit_run_usd"]
                 else "no limit set (cost_limit_run_usd)"),
             "  These are soft limits on the amounts Claude Code reports after each run: a "
             "running task can end above them, a run that is cut off may report nothing (shown "
             "as unknown, never as $0), and nothing here buys credits, reserves funds or caps "
             "spending at the provider.",
             "  The runner waits up to %d s for new tasks, then exits; the next task starts it "
             "again." % config["auto_queue_idle_timeout_seconds"],
             "  Approvals, decisions, dependencies, verification and model rules apply as "
             "before.",
             "  Pause: `moa7amed-queue auto pause`. Stop after the current task: `moa7amed-queue "
             "auto stop`. Turn off: `moa7amed-queue auto disable`."]
    return "\n".join(lines)


def enable(q, budget, worktree=False):
    """Turn Auto Queue on. The allowance is counted from the first time it was granted;
    enabling again never renews it (`auto budget --renew` does). A pause stays."""
    path = util.project_config_path(q.dir)
    current = util.load_json(path, {}) or {}
    current["auto_queue_enabled"] = True
    current["auto_queue_budget_usd"] = float(budget or 0)    # 0: no allowance, chosen explicitly
    if worktree:
        current["workspace"] = "worktree"
    util.save_json_atomic(path, current)
    with q.tx():
        if _meta(q, "auto_budget") is None:
            _set_meta(q, "auto_budget", {"since": util.now_iso(), "by": "enable"})
        _set_meta(q, "auto_failures", None)
        q.log_event(None, None, "auto-enabled",
                    "allowance %s" % ("$%.2f" % budget if budget else "none"))


def disable(q):
    path = util.project_config_path(q.dir)
    current = util.load_json(path, {}) or {}
    current["auto_queue_enabled"] = False
    util.save_json_atomic(path, current)
    with q.tx():
        q.log_event(None, None, "auto-disabled", "automatic launching off; tasks kept")


def pause(q, by="owner"):
    with q.tx():
        _set_meta(q, "auto_paused", {"at": util.now_iso(), "by": by})
        q.log_event(None, None, "auto-paused", "no new task is claimed until resume")


def resume(q):
    with q.tx():
        _set_meta(q, "auto_paused", None)
        _set_meta(q, "auto_failures", None)
        _set_meta(q, "auto_last_stop", None)
        q.log_event(None, None, "auto-resumed", None)
    wake(q)


def stop_after_current(q):
    from . import engine
    with q.tx():
        info = engine.lock_info(q)
        if not info or not info["alive"]:
            return None
        _set_meta(q, "auto_stop", {"runner_id": info["runner_id"], "at": util.now_iso()})
        q.log_event(None, None, "auto-stop", "runner %s stops after its current task"
                    % info["runner_id"])
    wake(q)
    return info


def stop_now(q):
    """Immediate stop, on the owner's explicit request. Only the process that holds the lock
    (identified by id and start time, on this host) and the task process it recorded are
    stopped. The interrupted task is held for review, never retried by itself: it may have
    done part of its work. Uncommitted changes stay where they are."""
    import time
    from . import engine
    info = engine.lock_info(q)
    if not info:
        return {"stopped": False, "reason": "no runner holds this project"}
    if info["host"] != util.HOST:
        raise QueueError("the runner runs on another host (%s); stop it there" % info["host"])
    pause(q, by="stop --now")
    row = engine.lock_row(q)
    runner_token = engine._token(q, "runner", row["runner_id"])
    targets = []
    if util.process_alive(row["pid"], runner_token) and runner_token:
        targets.append(row["pid"])
    run = q.conn.execute("SELECT * FROM runs WHERE finished_at IS NULL AND runner_id=?",
                         (row["runner_id"],)).fetchone()
    if run and run["child_pid"]:
        child_token = engine._token(q, "run", run["id"])
        if child_token and util.process_alive(run["child_pid"], child_token):
            targets.append(run["child_pid"])
    for pid in targets:
        util.kill_tree(pid)
    deadline = time.time() + 20
    while time.time() < deadline and any(util.pid_alive(pid) for pid in targets):
        time.sleep(0.2)
    recovered = engine.recover(q, force=False, hold=True,
                               reason="stopped by the owner (auto stop --now)")
    return {"stopped": True, "killed": targets, "recovered": recovered["recovered"]}


def status_line(state):
    if not state["enabled"]:
        text = "manual (Auto Queue is off for this project)"
    elif state["paused"]:
        text = "paused"
    else:
        text = "on"
    runner = {"running": "runner working on task #%s" % state.get("current_task_id"),
              "idle": "runner waiting for tasks", "starting": "runner starting",
              "stale": "runner lock left by a stopped runner", "none": "no runner"}[state["runner"]]
    spent = state["allowance"]
    if spent:
        runner += "; allowance $%.4f of $%.2f used" % (spent["spent_usd"], spent["limit_usd"])
    return "%s; %s" % (text, runner)
