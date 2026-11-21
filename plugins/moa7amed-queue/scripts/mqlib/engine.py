"""Runner protocol: workspace lock, crash recovery, and the claim -> run -> finish cycle.

Every runner (PowerShell or Python) drives the same five steps:

    acquire   take the project's single runner lock, recovering after a crash
    claim     pick the next runnable task, check the workspace, mark it running
    heartbeat keep the lock fresh and record the Claude process id
    finish    judge the result, commit or set aside changes, schedule retries
    release   give the lock back
"""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid

from . import gitops, models, routing, util
from .util import QueueError

EXIT_LOCK_HELD = 2
EXIT_WORKSPACE_STOP = 3
EXIT_LOCK_LOST = 4
EXIT_COST_LIMIT = 5

# The status line of a task result. MQ_RESULT is what tasks are asked to write; CTQ_RESULT is
# the legacy spelling from before the rebrand and is still understood.
_MARKER_RE = re.compile(
    r"^[ \t>*_`]*(?:MQ|CTQ)_RESULT:[ \t]*(DONE|FAILED)\b[ \t]*[-:–—]*[ \t]*(.*?)[ \t*_`]*$",
    re.IGNORECASE | re.MULTILINE)


class LockHeld(QueueError):
    code = EXIT_LOCK_HELD


class LockLost(QueueError):
    code = EXIT_LOCK_LOST


class WorkspaceStop(Exception):
    def __init__(self, reason, message):
        Exception.__init__(self, message)
        self.reason = reason
        self.message = message


# --------------------------------------------------------------------------- lock

def lock_row(q):
    return q.conn.execute("SELECT * FROM runner_lock WHERE id=1").fetchone()


def _token(q, kind, ref):
    row = q.conn.execute("SELECT token FROM process_tokens WHERE kind=? AND ref=?",
                         (kind, str(ref))).fetchone()
    return row["token"] if row else None


def _remember_process(q, kind, ref, pid):
    """Record which process this is (id plus start time), so a later check cannot be fooled
    by another program that was given the same id."""
    q.conn.execute("INSERT OR REPLACE INTO process_tokens(kind, ref, pid, token) VALUES(?,?,?,?)",
                   (kind, str(ref), int(pid), util.process_token(pid)))


def _holder_alive(q, row, stale_seconds):
    if row["host"] == util.HOST:
        return util.process_alive(row["pid"], _token(q, "runner", row["runner_id"]))
    age = (util.now() - util.parse_iso(row["heartbeat_at"])).total_seconds()
    return age < stale_seconds


def lock_info(q):
    """Describe the current runner lock, or None when no runner holds the queue."""
    row = lock_row(q)
    if row is None:
        return None
    info = {key: row[key] for key in ("runner_id", "pid", "host", "kind", "started_at",
                                      "heartbeat_at", "tasks_run", "current_run_id")}
    info["alive"] = _holder_alive(q, row, util.effective_config(q.dir)["stale_lock_seconds"])
    info["current_task_id"] = None
    if row["current_run_id"]:
        run = q.conn.execute("SELECT task_id FROM runs WHERE id=?",
                             (row["current_run_id"],)).fetchone()
        if run:
            info["current_task_id"] = run["task_id"]
    return info


def _held_message(row):
    return ("another runner is already working on this project (runner %s, pid %s on %s, "
            "started %s). Only one runner may use a workspace at a time."
            % (row["runner_id"], row["pid"], row["host"], util.local_time(row["started_at"])))


def _completed_output(run):
    """The result of a run whose Claude process finished, even though nobody recorded it."""
    if not run["stdout_file"]:
        return None
    parsed = parse_claude_output(run["stdout_file"])
    return parsed if parsed and parsed.get("complete") else None


def _recover_open_runs(q, force, reason):
    """Deal with runs that were never closed because their runner died.

    Returns (recovered, to_reconcile). A run whose Claude process had already written its
    complete result is not "interrupted": its id is returned in `to_reconcile` and it is
    closed properly once the new runner holds the lock, so the task is not done twice.
    Every other open run is closed as interrupted and its task goes back in the queue."""
    recovered, to_reconcile = [], []
    for run in q.conn.execute("SELECT * FROM runs WHERE finished_at IS NULL").fetchall():
        child = run["child_pid"]
        if child and not force and run["host"] == util.HOST \
                and util.process_alive(child, _token(q, "run", run["id"])):
            raise QueueError(
                "task #%d was interrupted, but its Claude process (pid %d) still appears to be "
                "running and may still be changing the workspace. Wait for it to exit or stop "
                "it, then try again. If that pid now belongs to an unrelated program, run "
                "`moa7amed-queue recover --force`." % (run["task_id"], child), code=EXIT_LOCK_HELD)
        if _completed_output(run) is not None:
            to_reconcile.append(run["id"])
            continue
        task = q.get(run["task_id"])
        exhausted = task["attempts"] >= task["max_attempts"]
        # A task cut off part-way may already have done things outside the repository
        # (sent a message, called an API, deployed). With on_interrupted=hold it waits for
        # a person instead of being run again by itself.
        held = not exhausted and util.effective_config(q.dir).get("on_interrupted") == "hold"
        stamp = util.now_iso()
        error = reason
        if held:
            error = ("%s. Held for review (on_interrupted=hold): it may have done part of its "
                     "work. `moa7amed-queue reconcile` shows what it left; `moa7amed-queue retry "
                     "%d` runs it again." % (reason, task["id"]))
        q.conn.execute("UPDATE runs SET finished_at=?, outcome='interrupted', error=? WHERE id=?",
                       (stamp, reason, run["id"]))
        q.conn.execute(
            "UPDATE tasks SET status=?, not_before=NULL, last_error=?, updated_at=?, "
            "finished_at=? WHERE id=? AND status='running'",
            ("failed" if exhausted or held else "pending", error, stamp,
             stamp if exhausted or held else None, task["id"]))
        q.log_event(task["id"], run["id"], "interrupted-held" if held else "interrupted",
                    "%s; task is %s" % (reason, "failed (no attempts left)" if exhausted
                                        else "held for review" if held else "pending again"))
        q.conn.execute("DELETE FROM process_tokens WHERE kind='run' AND ref=?", (str(run["id"]),))
        status = "failed" if exhausted else "held" if held else "pending"
        recovered.append({"task_id": task["id"], "run_id": run["id"], "status": status,
                          "reconciled": False,
                          "message": "recovered interrupted task #%d -> %s" % (task["id"], status)
                                     + ("; it waits for review: `moa7amed-queue reconcile`"
                                        if held else "")})
    # A task marked running with no open run cannot make progress: requeue it.
    for task in q.conn.execute(
            "SELECT id FROM tasks WHERE status='running' AND id NOT IN "
            "(SELECT task_id FROM runs WHERE finished_at IS NULL)").fetchall():
        q.conn.execute("UPDATE tasks SET status='pending', updated_at=? WHERE id=?",
                       (util.now_iso(), task["id"]))
        q.log_event(task["id"], None, "interrupted", "found running without a run; pending again")
        recovered.append({"task_id": task["id"], "run_id": None, "status": "pending",
                          "reconciled": False,
                          "message": "recovered interrupted task #%d -> pending" % task["id"]})
    return recovered, to_reconcile


def _reconcile(q, runner_id, run_ids):
    """Close runs that had finished before their runner died, using the result on disk."""
    reconciled = []
    for run_id in run_ids:
        result = finish(q, runner_id, run_id, reconciled=True)
        message = ("task #%d had already finished before the runner stopped; recorded as %s"
                   % (result["task_id"], result["outcome"]))
        if result["uncommitted"]:
            message += (". Its changes were left as they are, uncommitted, in %s: review and "
                        "commit them (%s)" % (result["workspace"],
                                              ", ".join(result["uncommitted"][:6])))
        reconciled.append({"task_id": result["task_id"], "run_id": run_id,
                           "status": result["status"], "reconciled": True, "message": message})
    return reconciled


def _clean_options(options):
    clean = {}
    for key, value in (options or {}).items():
        if value is None:
            continue
        clean[key] = util.coerce_config_value(key, value)
    return clean


def acquire(q, pid, options=None, kind="python", force=False):
    """Take the runner lock for this project, cleaning up after a crashed runner."""
    clean = _clean_options(options)
    config = util.effective_config(q.dir, clean)
    with q.tx():
        row = lock_row(q)
        if row is not None:
            if not force and _holder_alive(q, row, config["stale_lock_seconds"]):
                raise LockHeld(_held_message(row), info=dict(row))
            q.conn.execute("DELETE FROM runner_lock WHERE id=1")
            q.conn.execute("DELETE FROM process_tokens WHERE kind='runner'")
            q.log_event(None, None, "lock-recovered",
                        "removed the lock of runner %s (pid %s on %s; %s)"
                        % (row["runner_id"], row["pid"], row["host"],
                           "forced" if force else "stale"))
        recovered, to_reconcile = _recover_open_runs(
            q, force, "the runner stopped before the task finished")
        runner_id = uuid.uuid4().hex[:12]
        stamp = util.now_iso()
        q.conn.execute(
            "INSERT INTO runner_lock(id, runner_id, pid, host, kind, started_at, heartbeat_at, "
            "options) VALUES(1,?,?,?,?,?,?,?)",
            (runner_id, int(pid), util.HOST, kind, stamp, stamp, json.dumps(clean)))
        _remember_process(q, "runner", runner_id, pid)
        q.log_event(None, None, "runner-start", "runner %s (%s, pid %s)" % (runner_id, kind, pid))
    # With the lock held, close the runs that had in fact finished (git is involved, so
    # this happens outside the database transaction above).
    recovered += _reconcile(q, runner_id, to_reconcile)
    return {"runner_id": runner_id, "recovered": recovered, "options": config,
            "project": q.root, "data_dir": q.dir}


def require_lock(q, runner_id):
    row = lock_row(q)
    if row is None or row["runner_id"] != runner_id:
        raise LockLost("runner %s no longer holds the lock for this project (it was released "
                       "or taken over); stopping." % runner_id)
    return row


def runner_options(q, row):
    return util.effective_config(q.dir, json.loads(row["options"] or "{}"))


def heartbeat(q, runner_id, run_id=None, child_pid=None):
    with q.tx():
        require_lock(q, runner_id)
        q.conn.execute("UPDATE runner_lock SET heartbeat_at=? WHERE id=1", (util.now_iso(),))
        if run_id is not None and child_pid is not None:
            q.conn.execute("UPDATE runs SET child_pid=? WHERE id=? AND finished_at IS NULL",
                           (int(child_pid), int(run_id)))
            _remember_process(q, "run", run_id, child_pid)
    return {"ok": True}


def release(q, runner_id):
    """Give the lock back. A run this runner left open is treated as interrupted, unless
    Claude had already finished it: then only the bookkeeping is missing, the run stays
    open, and the next runner records its result instead of running the task again."""
    with q.tx():
        row = lock_row(q)
        if row is None or row["runner_id"] != runner_id:
            return {"released": False}
        for run in q.conn.execute(
                "SELECT * FROM runs WHERE finished_at IS NULL AND runner_id=?",
                (runner_id,)).fetchall():
            if _completed_output(run) is not None:
                q.log_event(run["task_id"], run["id"], "result-pending",
                            "the runner stopped before recording the finished task; the next "
                            "runner records it")
                continue
            stamp = util.now_iso()
            q.conn.execute("UPDATE runs SET finished_at=?, outcome='interrupted', error=? "
                           "WHERE id=?", (stamp, "the runner exited during the task", run["id"]))
            q.conn.execute(
                "UPDATE tasks SET status='pending', attempts=MAX(attempts-1, 0), updated_at=? "
                "WHERE id=? AND status='running'", (stamp, run["task_id"]))
            q.log_event(run["task_id"], run["id"], "interrupted",
                        "the runner exited during the task; pending again")
        q.conn.execute("DELETE FROM runner_lock WHERE id=1")
        q.conn.execute("DELETE FROM process_tokens WHERE kind='runner'")
        q.log_event(None, None, "runner-stop",
                    "runner %s ran %d task(s)" % (runner_id, row["tasks_run"]))
    return {"released": True}


def session_cost(q, lock):
    """What this runner session has cost so far, exactly as Claude Code reported it: the
    finished runs of this runner plus the model-review calls made since it started. Runs
    for which the CLI reported no cost add nothing (they are not estimated)."""
    row = q.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS total FROM runs WHERE runner_id=?",
                         (lock["runner_id"],)).fetchone()
    from . import agents
    return float(row["total"] or 0) + routing.review_costs(q, lock["started_at"]) + \
        agents.spend_since(q, lock["started_at"])


_SESSION_WARNING = "this runner session"


def _cost_warnings(q, lock, opts, task, cost, reconciled=False):
    """Warnings a finished run triggers. `cost` is the figure the CLI reported, or None.
    A level of 0 means "not set", as for every other limit. Reporting must never get in
    the way of recording a result, so any failure here simply means no warning."""
    warnings = []
    try:
        per_task, per_session = opts.get("cost_warn_task_usd"), opts.get("cost_warn_run_usd")
        if per_task and cost is not None and cost >= float(per_task):
            warnings.append("task #%d cost $%.4f in this run, at or above the warning level of "
                            "$%.2f (cost_warn_task_usd)" % (task["id"], cost, float(per_task)))
        # A run recorded after a crash belonged to the runner that is gone, not to this session.
        if per_session and not reconciled:
            total = session_cost(q, lock) + (cost or 0)   # this run is not recorded yet
            said = q.conn.execute(
                "SELECT 1 FROM events WHERE type='cost-warning' AND detail LIKE ? AND ts >= ?",
                (_SESSION_WARNING + "%", lock["started_at"])).fetchone()
            if total >= float(per_session) and not said:
                warnings.append("%s has cost $%.4f so far, at or above the warning level of "
                                "$%.2f (cost_warn_run_usd)"
                                % (_SESSION_WARNING, total, float(per_session)))
    except Exception:  # noqa: BLE001 - see the docstring
        pass
    return warnings


# ---- files a task left uncommitted when it was found finished after a crash

def _reconcile_hold(q):
    row = q.conn.execute("SELECT value FROM meta WHERE key='reconcile_hold'").fetchone()
    try:
        hold = json.loads(row["value"]) if row else None
    except ValueError:
        hold = None
    return hold if isinstance(hold, dict) and isinstance(hold.get("paths"), list) else None


def _same_directory(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, TypeError):
        return False


def _still_uncommitted(hold, cwd):
    """The held paths that are still uncommitted (new folders are compared file by file)."""
    try:
        dirty = set(gitops.paths_from_status(gitops.status_lines(cwd, every_file=True)))
    except QueueError:
        return sorted(hold["paths"])
    return sorted(path for path in hold["paths"]
                  if path in dirty or any(item.startswith(path.rstrip("/") + "/")
                                          for item in dirty))


def _owner(cwd, path, started, finished):
    """Who most likely changed a file, judged by when it was last changed: during the run
    ("task"), after Claude had reported ("later": possibly not the task), before the run
    ("before"), or unknown (the file is gone, or the times are not available)."""
    try:
        changed = os.path.getmtime(os.path.join(cwd, path))
    except OSError:
        return "unknown"
    if started is None or finished is None:
        return "unknown"
    if changed < started - 2:
        return "before"
    if changed <= finished + 2:
        return "task"
    return "later"


def _current_owners(hold):
    """The owner recorded for each held file, corrected for what happened since: a file
    changed after the hold was set may now hold someone else's edits as well."""
    owners = dict(hold.get("owners") or {})
    try:
        since = util.parse_iso(hold.get("at")).timestamp()
    except (TypeError, ValueError):
        since = None
    for path in hold["paths"]:
        try:
            changed = os.path.getmtime(os.path.join(hold["cwd"], path))
        except OSError:
            continue
        if since is not None and changed > since + 2 and owners.get(path) == "task":
            owners[path] = "later"
    return owners


def _check_reconcile_hold(q, cwd, dirty, task):
    """The files of a task that was found finished after a crash are the one thing no
    policy may touch: they are complete work that nobody has looked at yet. While any of
    them is still uncommitted, no task starts in that workspace, whatever `on_dirty` says.
    Once they are committed (or the user has dealt with them otherwise) the hold is gone."""
    hold = _reconcile_hold(q)
    if not hold or not _same_directory(hold.get("cwd"), cwd):
        return
    still = _still_uncommitted(hold, cwd)
    if not still:
        q.conn.execute("DELETE FROM meta WHERE key='reconcile_hold'")
        return
    shown = "\n".join("    " + path for path in still[:10])
    more = "\n    ... and %d more" % (len(still) - 10) if len(still) > 10 else ""
    raise WorkspaceStop(
        "reconciled-changes",
        "task #%d was not started (nothing was modified). Task #%s finished before the runner "
        "stopped and its changes were never committed:\n%s%s\n  They are exactly as that task "
        "left them, in %s. Look at them and commit them, or set them aside yourself, then run "
        "the queue again. The queue does not stash or build on a finished task's work, whatever "
        "on_dirty says."
        % (task["id"], hold.get("task_id"), shown, more, cwd))


def _commit_of_run(cwd, run, task, head_after):
    """After a crash: which of the commits made since the run started is the run's own.

    Time has passed, so the newest commit may be the user's. A commit counts as the run's
    if it carries this task's `Task-Queue-Id` (the runner made it) or was made before
    Claude wrote its result (Claude made it). Returns the last such commit, or None."""
    try:
        finished = os.path.getmtime(run["stdout_file"])
    except (OSError, TypeError):
        finished = None
    trailer = "Task-Queue-Id: %d" % task["id"]
    found = None
    for sha, when, message in gitops.commits_between(cwd, run["head_before"], head_after):
        by_runner = trailer in [line.strip() for line in message.splitlines()]
        by_claude = finished is not None and when <= finished + 2
        if not (by_runner or by_claude):
            break
        found = sha
    return found


def recover(q, force=False):
    """Manual recovery: drop a stale lock, requeue interrupted tasks and record tasks that
    had in fact finished. It does what a starting runner does, then lets go again."""
    row = lock_row(q)
    try:
        acquired = acquire(q, os.getpid(), None, kind="recover", force=force)
    except LockHeld as exc:
        raise LockHeld("%s Use --force only if you are sure it is gone." % exc, info=exc.info)
    release(q, acquired["runner_id"])
    return {"lock_removed": row is not None, "recovered": acquired["recovered"]}


# --------------------------------------------------------------------------- workspace

def worktree_path(q):
    return os.path.join(q.dir, "worktree")


def prepare_workspace(q, opts, lock, task):
    """Decide where the task runs and make sure nobody's uncommitted work is at risk."""
    root = q.root
    info = {"cwd": root, "is_git": False, "clean_start": False, "branch": None,
            "head_before": None, "stash_ref": None}
    is_git = gitops.is_repo(root)
    if opts["workspace"] == "worktree":
        if not is_git:
            raise WorkspaceStop("not-git", "worktree mode needs a git repository, but %s is "
                                "not one. Use the default in-place mode." % root)
        path = worktree_path(q)
        error = gitops.ensure_worktree(root, path, opts["worktree_branch"])
        if error:
            raise WorkspaceStop("worktree", error)
        info["cwd"] = path
    if not is_git:
        return info
    cwd = info["cwd"]
    info["is_git"] = True
    busy = gitops.operation_in_progress(cwd)
    if busy:
        raise WorkspaceStop("git-busy", "the repository at %s is in the middle of a git "
                            "operation (%s). Finish or abort it, then run the queue again."
                            % (cwd, busy))
    dirty = gitops.status_lines(cwd)
    _check_reconcile_hold(q, cwd, dirty, task)
    if not dirty:
        info["clean_start"] = True
    elif opts["on_dirty"] == "allow" or (opts["commit"] == "none" and lock["tasks_run"] > 0):
        info["clean_start"] = False
    elif opts["on_dirty"] == "stash":
        sha, error = gitops.stash_all(
            cwd, "moa7amed-queue: uncommitted changes set aside before task #%d (%s)"
            % (task["id"], util.now_iso()))
        if error:
            raise WorkspaceStop("stash-failed", "could not stash uncommitted changes in %s: %s"
                                % (cwd, error))
        info["stash_ref"] = sha
        info["clean_start"] = True
    else:
        shown = "\n".join("    " + line for line in dirty[:10])
        more = "\n    ... and %d more" % (len(dirty) - 10) if len(dirty) > 10 else ""
        raise WorkspaceStop(
            "dirty",
            "the workspace has uncommitted changes, so task #%d was not started (nothing was "
            "modified):\n%s%s\n  Commit or stash them, or re-run the queue with on_dirty=stash "
            "(sets them aside in a git stash), on_dirty=allow (run anyway, no auto-commit), or "
            "workspace=worktree (run in a separate worktree)."
            % (task["id"], shown, more))
    info["branch"] = gitops.branch(cwd)
    info["head_before"] = gitops.head(cwd)
    return info


def task_directory(workspace_root, task):
    """Where Claude starts: the subdirectory the task was queued from, if it still exists."""
    if task["workdir"]:
        candidate = os.path.join(workspace_root, task["workdir"])
        if os.path.isdir(candidate):
            return candidate
    return workspace_root


# --------------------------------------------------------------------------- claim

def compose_prompt(q, task, attempt, workspace_root, cwd, route=None):
    """The text handed to Claude. The user's prompt and criteria are embedded verbatim."""
    extra_context, extra_rules = routing.prompt_sections(q, task, route) if route else ([], [])
    planning = bool(route) and route.get("phase") == "plan"
    parts = [
        "You are running unattended as a worker for a persistent task queue "
        "(moa7amed-queue). This is task #%d, attempt %d of %d. Nobody is available to "
        "answer questions: make reasonable decisions yourself, and if something truly blocks "
        "you, report that instead of guessing."
        % (task["id"], attempt, task["max_attempts"]),
        "",
        "<task>",
        task["prompt"],
        "</task>",
        "",
        "<acceptance_criteria>",
        task["acceptance"] or ("No separate acceptance criteria were given. Treat the task "
                               "description itself as the definition of done."),
        "</acceptance_criteria>",
    ]
    done_deps = [dep for dep in q.deps(task["id"]) if dep["status"] == "done"]
    if done_deps:
        parts += ["", "<completed_prerequisites>",
                  "These queued tasks were completed before this one; their changes are "
                  "already in the workspace."]
        for dep in done_deps:
            line = "- Task #%d: %s" % (dep["id"], dep["title"])
            if dep["result_summary"]:
                line += " -- result: %s" % dep["result_summary"]
            if dep["commit_sha"]:
                line += " (commit %s)" % dep["commit_sha"][:10]
            parts.append(line)
        parts.append("</completed_prerequisites>")
    previous = q.conn.execute(
        "SELECT * FROM runs WHERE task_id=? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
        (task["id"],)).fetchone()
    if previous is not None and previous["outcome"] != "done":
        parts += ["", "<previous_attempt>",
                  "An earlier attempt at this task ended as '%s': %s"
                  % (previous["outcome"], previous["error"] or "no details recorded"),
                  "Any changes it left behind were set aside; start from the workspace as it "
                  "is now.", "</previous_attempt>"]
    parts += extra_context
    try:
        from . import agents
        parts += agents.prompt_block(q, task)
    except Exception:  # noqa: BLE001 - the prompt must always be produced
        pass
    try:
        from . import workflow
        config = util.effective_config(q.dir)
        parts += workflow.prompt_block(q, task, route, config)
        verification = workflow.verification_rule(config, (route or {}).get("phase"))
    except Exception:  # noqa: BLE001 - the prompt must always be produced
        verification = []
    try:
        from . import challenge
        parts += challenge.prompt_block(q, task, route, util.effective_config(q.dir))
    except Exception:  # noqa: BLE001 - the prompt must always be produced
        pass
    parts += ["", "Rules:",
              "- Work only inside this project: %s (you start in %s)." % (workspace_root, cwd)]
    if not planning:
        parts += [
            "- Do the whole task, then check every acceptance criterion before you finish.",
            "- Do not start work that the task does not ask for.",
            "- Unless the task explicitly asks for it, do not commit, stash, reset, switch "
            "branches or push. The queue runner records your changes when you finish.",
        ]
    parts += extra_rules
    parts += verification
    parts += [
        "- End your final message with exactly one status line, on a line of its own:",
        "  MQ_RESULT: DONE - <one-line summary of what you did>",
        "  or, if the task could not be completed or a criterion is not met:",
        "  MQ_RESULT: FAILED - <the reason>",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- reconcile

_OWNER_TEXT = {"task": "changed while the task ran",
               "later": "changed after the task had finished: possibly not the task's",
               "before": "changed before the task started: not the task's",
               "unknown": "cannot tell (deleted, or no times recorded)"}


def reconcile_report(q):
    """Everything a person needs to decide what to do after a crash or an interruption.
    Reads only; changes nothing."""
    info = lock_info(q)
    report = {"project": q.root, "runner": info, "open_runs": [], "hold": None,
              "held_tasks": [], "other_changes": [], "blocked": None, "actions": []}
    for run in q.conn.execute("SELECT * FROM runs WHERE finished_at IS NULL ORDER BY id"):
        report["open_runs"].append({"task_id": run["task_id"], "run_id": run["id"],
                                    "finished_by_claude": _completed_output(run) is not None})
    hold = _reconcile_hold(q)
    config = util.effective_config(q.dir)
    report["on_interrupted"] = config["on_interrupted"]
    in_worktree = config["workspace"] == "worktree" and os.path.isdir(worktree_path(q))
    workspace = hold["cwd"] if hold else (worktree_path(q) if in_worktree else q.root)
    dirty = []
    if gitops.is_repo(workspace):
        try:
            dirty = gitops.paths_from_status(gitops.status_lines(workspace, every_file=True))
        except QueueError:
            dirty = []
    if hold:
        still = set(_still_uncommitted(hold, hold["cwd"]))
        owners = _current_owners(hold)
        files = [{"path": path, "owner": owners.get(path, "unknown"),
                  "why": _OWNER_TEXT[owners.get(path, "unknown")],
                  "uncommitted": path in still} for path in hold["paths"]]
        report["hold"] = {"task_id": hold.get("task_id"), "run_id": hold.get("run_id"),
                          "workspace": hold["cwd"], "since": hold.get("at"), "files": files}
        held_paths = set(hold["paths"])
        report["other_changes"] = [path for path in dirty if path not in held_paths]
    else:
        report["other_changes"] = dirty
    for event in q.conn.execute(
            "SELECT DISTINCT task_id FROM events WHERE type='interrupted-held'"):
        task = q.get(event["task_id"])
        if task is not None and task["status"] == "failed":
            report["held_tasks"].append({"task_id": task["id"], "title": task["title"],
                                         "error": task["last_error"]})
    # The single most important reason nothing runs, in the order a runner meets them.
    if info and info["alive"]:
        report["blocked"] = "a runner is working on this project (pid %s)" % info["pid"]
    elif any(run["finished_by_claude"] for run in report["open_runs"]):
        report["blocked"] = ("a task finished but its runner stopped before recording it; the "
                             "next runner (or `moa7amed-queue recover`) records it")
    elif report["hold"] and any(item["uncommitted"] for item in report["hold"]["files"]):
        report["blocked"] = ("task #%s finished after a crash and its changes are not committed; "
                             "no task starts in %s until they are"
                             % (report["hold"]["task_id"], report["hold"]["workspace"]))
    elif report["other_changes"] and config["on_dirty"] == "stop":
        report["blocked"] = ("the workspace has uncommitted changes that do not belong to a "
                             "queued task; with on_dirty=stop no task starts until they are "
                             "committed or stashed")
    elif report["held_tasks"]:
        report["blocked"] = ("%d task(s) were cut off by a crash and wait for review "
                             "(on_interrupted=hold); tasks that depend on them wait too"
                             % len(report["held_tasks"]))
    if report["hold"]:
        ours = [item["path"] for item in report["hold"]["files"]
                if item["uncommitted"] and item["owner"] == "task"]
        if ours:
            report["actions"].append("moa7amed-queue reconcile --commit --yes   commit the %d "
                                     "file(s) changed while the task ran, as task #%s"
                                     % (len(ours), report["hold"]["task_id"]))
        report["actions"].append("moa7amed-queue reconcile --stash --yes    set all of the "
                                 "task's uncommitted files aside in a git stash")
        report["actions"].append("moa7amed-queue reconcile --release --yes  you have dealt "
                                 "with the files yourself: let the queue continue")
    for task in report["held_tasks"]:
        report["actions"].append("moa7amed-queue retry %d   run task #%d again (it was cut off "
                                 "part-way)" % (task["task_id"], task["task_id"]))
    return report


def reconcile_resolve(q, action):
    """Apply one explicit decision about the files of a task found finished after a crash.
    `action` is "commit" (only the files changed while the task ran), "stash" (every held
    file that is still uncommitted) or "release" (leave the files, lift the hold). Holds
    the runner lock while it works, so no runner can start in between."""
    acquired = acquire(q, os.getpid(), None, kind="reconcile")
    try:
        hold = _reconcile_hold(q)
        if not hold:
            return {"action": action, "done": False, "message": "nothing is held: there is no "
                    "finished task with uncommitted changes"}
        cwd = hold["cwd"]
        still = _still_uncommitted(hold, cwd)
        owners = _current_owners(hold)
        result = {"action": action, "done": True, "task_id": hold.get("task_id"),
                  "workspace": cwd, "recovered": acquired["recovered"]}
        if action == "commit":
            ours = [path for path in still if owners.get(path) == "task"]
            left = [path for path in still if path not in ours]
            if not ours:
                return dict(result, done=False, message=(
                    "none of the held files was changed while the task ran, so none is "
                    "committed as the task's. Commit them yourself if they are, or use "
                    "--stash or --release."))
            task = q.get(hold["task_id"])
            message = ("%s (task #%d)\n\nFinished by task #%d before its runner stopped; "
                       "committed with `moa7amed-queue reconcile --commit` after review.\n\n"
                       "Task-Queue-Id: %d\n" % (task["title"], task["id"], task["id"],
                                                  task["id"]))
            sha, error = gitops.commit_paths(cwd, ours, message)
            if error:
                raise QueueError("could not commit the task's files: %s" % error)
            with q.tx():
                q.conn.execute("UPDATE tasks SET commit_sha=COALESCE(commit_sha, ?) WHERE id=?",
                               (sha, task["id"]))
                q.conn.execute("UPDATE runs SET commit_sha=COALESCE(commit_sha, ?) WHERE id=?",
                               (sha, hold.get("run_id")))
                remaining = [path for path in hold["paths"] if path in left]
                _set_hold(q, hold, remaining)
                q.log_event(task["id"], hold.get("run_id"), "reconcile-commit",
                            "committed %d file(s) as the task's: %s [commit %s]%s"
                            % (len(ours), ", ".join(ours[:10]), sha[:10],
                               "; still held: %s" % ", ".join(left[:10]) if left else ""))
            return dict(result, commit=sha, committed=ours, still_held=left,
                        message="committed %d file(s) as task #%d (commit %s)%s"
                                % (len(ours), task["id"], sha[:10],
                                   "; %d file(s) whose owner is unclear are still held: commit "
                                   "them yourself, or --stash / --release" % len(left)
                                   if left else ""))
        if action == "stash":
            if not still:
                with q.tx():
                    _set_hold(q, hold, [])
                return dict(result, message="the held files are already committed; the hold "
                                            "is lifted")
            sha, error = gitops.stash_paths(
                cwd, still, "moa7amed-queue: uncommitted files of task #%s, set aside by "
                            "reconcile" % hold.get("task_id"))
            if error:
                raise QueueError("could not set the files aside: %s" % error)
            with q.tx():
                _set_hold(q, hold, [])
                q.log_event(hold.get("task_id"), hold.get("run_id"), "reconcile-stash",
                            "set aside in git stash %s: %s" % (sha[:10], ", ".join(still[:10])))
            return dict(result, stash=sha, stashed=still,
                        message="set %d file(s) aside in git stash %s (git stash apply %s "
                                "brings them back)" % (len(still), sha[:10], sha[:10]))
        if action == "release":
            with q.tx():
                _set_hold(q, hold, [])
                q.log_event(hold.get("task_id"), hold.get("run_id"), "reconcile-release",
                            "hold lifted by the user; files left as they are: %s"
                            % ", ".join(still[:10]))
            return dict(result, left=still,
                        message="the hold is lifted; %d file(s) were left exactly as they are"
                                % len(still))
        raise QueueError("unknown action %r" % action)
    finally:
        release(q, acquired["runner_id"])


def _set_hold(q, hold, paths):
    if paths:
        new = dict(hold, paths=sorted(paths))
        q.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('reconcile_hold', ?)",
                       (json.dumps(new),))
    else:
        q.conn.execute("DELETE FROM meta WHERE key='reconcile_hold'")


def _replica_dirs(q, task):
    """A task of a replica project may read that project's reference files (screenshots,
    captures, comparisons), which live in the queue's data folder: --add-dir for it."""
    source = str(task["source"] or "")
    if not (source.startswith("plan:") or source.startswith("replica:")):
        return []
    try:
        from . import replica_store
        folder = replica_store.task_folder(q, source.split(":", 1)[1])
    except Exception:  # noqa: BLE001 - never block a task over this
        return []
    return ["--add-dir", folder] if folder else []


def _bid_args(q, task):
    """A task of a bid project may use its bid folder and run the `bid` and `project`
    commands of this plugin (it records its results through them), nothing more."""
    if not str(task["source"] or "").startswith("bid:"):
        return []
    try:
        from . import bid_store
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return bid_store.task_permissions(q, task, root)
    except Exception:  # noqa: BLE001 - never block a task over this
        return []


def build_claude_args(opts, route=None):
    """Arguments for `claude`. The prompt itself is supplied on stdin.

    Every value is its own argument and is never passed through a shell. With a
    `route` the model is the one selected for this task; without one it is the
    model option of the run, if any."""
    args = ["-p", "--output-format", "json", "--permission-mode", opts["permission_mode"]]
    model = route["model_arg"] if route is not None else opts["model"]
    if model:
        args += ["--model", models.validate_model(model)]
    if opts["max_budget_usd"]:
        args += ["--max-budget-usd", "%g" % opts["max_budget_usd"]]
    if opts["allowed_tools"]:
        args += ["--allowedTools"] + list(opts["allowed_tools"])
    disallowed = list(opts["disallowed_tools"])
    disallowed += [tool for tool in (route or {}).get("disallowed", []) if tool not in disallowed]
    if disallowed:
        args += ["--disallowedTools"] + disallowed
    args += list(opts["claude_args"])
    return args


def _idle(q):
    counts = q.counts()
    if counts["pending"] == 0:
        return {"action": "idle", "reason": "empty", "pending": 0, "wait_seconds": 0,
                "message": "the queue has no pending tasks"}
    next_at = q.next_retry_time()
    if next_at:
        wait = max(1, int((util.parse_iso(next_at) - util.now()).total_seconds()))
        return {"action": "idle", "reason": "backoff", "pending": counts["pending"],
                "next_at": next_at, "wait_seconds": wait,
                "message": "waiting %ds before the next retry" % wait}
    blocked = [task["id"] for task in q.tasks(["pending"])]
    approval = [task_id for task_id in blocked
                if str(q.model_block(task_id) or "").startswith("approval:")]
    decision = [task_id for task_id in blocked
                if str(q.model_block(task_id) or "").startswith("decision:")]
    no_model = [task_id for task_id in blocked if q.model_block(task_id)
                and task_id not in approval + decision]
    waiting = [task_id for task_id in blocked if task_id not in no_model + approval + decision]
    causes = []
    if decision:
        causes.append("%s wait for your decision on a concern (`moa7amed-queue challenge`)"
                      % ", ".join("#%d" % i for i in decision))
    if approval:
        causes.append("%s wait for your approval of their plan (`moa7amed-queue workflow "
                      "approve <id>`)" % ", ".join("#%d" % i for i in approval))
    if waiting:
        causes.append("%s blocked by failed or cancelled prerequisites"
                      % ", ".join("#%d" % i for i in waiting))
    if no_model:
        causes.append("%s blocked because no permitted model is available"
                      % ", ".join("#%d" % i for i in no_model))
    return {"action": "idle", "reason": "blocked", "pending": counts["pending"],
            "wait_seconds": 0, "blocked": blocked,
            "message": "%d pending task(s) cannot run: %s"
                       % (len(blocked), "; ".join(causes))}


def claim(q, runner_id):
    """Start the next runnable task, or explain why nothing can start."""
    for _ in range(q.counts()["pending"] + 10):
        lock = require_lock(q, runner_id)
        if lock["current_run_id"]:
            unfinished = q.conn.execute("SELECT task_id FROM runs WHERE id=? AND finished_at "
                                        "IS NULL", (lock["current_run_id"],)).fetchone()
            if unfinished:
                raise QueueError("run %d (task #%d) of this runner has not been finished; its "
                                 "result has to be reported before another task can start"
                                 % (lock["current_run_id"], unfinished["task_id"]))
        opts = runner_options(q, lock)
        from . import challenge
        challenge.release_when_off(q, opts)
        task = q.next_eligible()
        if task is None:
            return _idle(q)
        # An open concern the owner has to decide holds this task; others go on.
        if challenge.gate(q, task, opts):
            continue
        limit = opts.get("cost_limit_run_usd")
        if limit:   # unset or 0: no limit
            spent = session_cost(q, lock)
            if spent >= float(limit):
                message = ("the cost limit for one runner session has been reached: Claude Code "
                           "reported $%.4f so far and cost_limit_run_usd is $%.2f. %d task(s) "
                           "stay pending and nothing was interrupted; start the runner again "
                           "to continue." % (spent, float(limit), q.counts()["pending"]))
                with q.tx():
                    q.log_event(None, None, "cost-limit", message)
                return {"action": "stop", "reason": "cost-limit", "message": message,
                        "exit_code": EXIT_COST_LIMIT, "spent_usd": round(spent, 6),
                        "task_id": task["id"]}
            # Claude Code stops a task once its own estimate of the spend reaches the
            # --max-budget-usd it was given. Give it at most what the session has left.
            remaining = float(limit) - spent
            per_task = opts.get("max_budget_usd")
            opts = dict(opts)
            opts["max_budget_usd"] = round(min(per_task, remaining) if per_task else remaining, 4)
            opts["_budget_from_session"] = not per_task or remaining < per_task
        # The model is chosen here, before the task starts, and stays with this run.
        route = routing.select_for_run(q, task, opts)
        if route["action"] == "blocked":
            continue  # recorded on the task; it no longer counts as runnable
        if route["action"] == "stop":
            return {"action": "stop", "reason": route["reason"], "message": route["message"],
                    "exit_code": EXIT_WORKSPACE_STOP, "task_id": task["id"]}
        try:
            workspace = prepare_workspace(q, opts, lock, task)
        except WorkspaceStop as stop:
            q.log_event(task["id"], None, "workspace-stop", stop.message)
            return {"action": "stop", "reason": stop.reason, "message": stop.message,
                    "exit_code": EXIT_WORKSPACE_STOP, "task_id": task["id"]}
        # Workers first (when the task's work splits safely), then the task's own session.
        group = _agent_group(q, runner_id, task, opts, workspace, route)
        if group is not None and group.get("status") == "blocked":
            if routing.get(q, task["id"]) is not None:
                with q.tx():
                    routing._block(q, task["id"], "agents: " + group["reason"])
                continue
            return {"action": "stop", "reason": "agents-blocked", "message": group["reason"],
                    "exit_code": EXIT_WORKSPACE_STOP, "task_id": task["id"]}
        if group is not None and group.get("status") == "cancelled":
            continue
        if group is not None and group.get("status") == "integrated":
            if workspace.get("is_git"):
                workspace["head_before"] = gitops.head(workspace["cwd"])
            if opts.get("max_budget_usd") and group.get("spent_usd"):
                opts = dict(opts)
                opts["max_budget_usd"] = max(round(float(opts["max_budget_usd"])
                                                   - group["spent_usd"], 4), 0.01)
        with q.tx():
            require_lock(q, runner_id)
            fresh = q.get(task["id"])
            if fresh["status"] != "pending":
                continue  # cancelled while the workspace was being checked
            attempt = fresh["attempts"] + 1
            stamp = util.now_iso()
            directory = task_directory(workspace["cwd"], fresh)
            q.conn.execute(
                "UPDATE tasks SET status='running', attempts=?, not_before=NULL, started_at=?, "
                "finished_at=NULL, updated_at=? WHERE id=?", (attempt, stamp, stamp, task["id"]))
            cursor = q.conn.execute(
                "INSERT INTO runs(task_id, attempt, runner_id, host, started_at, cwd, workdir, "
                "is_git, clean_start, branch, head_before, stash_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (task["id"], attempt, runner_id, util.HOST, stamp, workspace["cwd"], directory,
                 int(workspace["is_git"]), int(workspace["clean_start"]), workspace["branch"],
                 workspace["head_before"], workspace["stash_ref"]))
            run_id = cursor.lastrowid
            base = os.path.join(q.dir, "runs", "task-%04d" % task["id"], "run-%06d" % run_id)
            files = {"prompt_file": base + ".prompt.md", "stdout_file": base + ".stdout.json",
                     "stderr_file": base + ".stderr.log"}
            # Run numbers can repeat after a backup was restored. Output that an earlier run
            # with this number left behind must never be read as this run's result.
            for leftover in (files["stdout_file"], files["stderr_file"]):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            util.write_text_file(
                files["prompt_file"],
                compose_prompt(q, fresh, attempt, workspace["cwd"], directory, route))
            routing.record_start(q, run_id, task["id"], route)
            q.conn.execute("UPDATE runs SET prompt_file=?, stdout_file=?, stderr_file=? WHERE id=?",
                           (files["prompt_file"], files["stdout_file"], files["stderr_file"],
                            run_id))
            q.conn.execute("UPDATE runner_lock SET current_run_id=?, heartbeat_at=? WHERE id=1",
                           (run_id, stamp))
            detail = "attempt %d/%d in %s" % (attempt, fresh["max_attempts"], directory)
            if route["phase"]:
                detail += "; %s phase" % route["phase"]
            detail += "; model %s (%s)" % (route["model_arg"] or "CLI default", route["source"])
            if workspace["stash_ref"]:
                detail += "; uncommitted changes were stashed first as %s" % workspace["stash_ref"]
            q.log_event(task["id"], run_id, "started", detail)
        result = {
            "action": "run", "task_id": task["id"], "title": fresh["title"], "run_id": run_id,
            "attempt": attempt, "max_attempts": fresh["max_attempts"],
            "cwd": directory, "workspace": workspace["cwd"], "stashed": workspace["stash_ref"],
            "claude_bin": opts["claude_bin"] or util.env("CLAUDE_BIN") or "claude",
            "claude_args": build_claude_args(opts, route) + _replica_dirs(q, fresh)
            + _bid_args(q, fresh),
            "model": route["model_arg"], "model_tier": route["tier"],
            "model_source": route["source"], "model_reason": route["reason"],
            "phase": route["phase"], "model_note": _model_note(route),
            "budget_usd": opts.get("max_budget_usd") or None,
            "budget_note": (" [budget $%g: what is left of cost_limit_run_usd]"
                            % opts["max_budget_usd"]) if opts.get("_budget_from_session")
                           else "",
            "timeout_seconds": int(float(opts["timeout_minutes"]) * 60),
            "env": task_environment(task["id"], q.root),
        }
        result.update(files)
        return result
    raise QueueError("could not claim a task: the queue kept changing; try again")


def _agent_group(q, runner_id, task, opts, workspace, route):
    """Evaluate the task for workers and run them; None when it runs single."""
    from . import agents
    if route and route.get("phase") not in (None, "plan"):
        return None             # analysis belongs before planning; later phases use its findings
    try:
        decision = agents.evaluate(q, task, opts)
    except Exception as exc:  # noqa: BLE001 - never block a task over the evaluation
        with q.tx():
            q.log_event(task["id"], None, "agents-error", str(exc)[:300])
        return None
    if decision["strategy"] != "parallel":
        return None
    with q.tx():
        q.log_event(task["id"], None, "agents", "parallel: %s" % decision["reason"])
    return agents.run_group(q, runner_id, task, opts, workspace, route)


def task_environment(task_id, project_root):
    """Variables every task process gets. The CTQ_* names are the ones versions before the
    rebrand used; an older copy of the capture hook still looks for them."""
    values = {"RUNNER": "1", "TASK_ID": str(task_id), "PROJECT": project_root}
    env = {}
    for prefix in (util.ENV_PREFIX, util.LEGACY_ENV_PREFIX):
        for name, value in values.items():
            env[prefix + name] = value
    return env


def _model_note(route):
    """One short phrase for the runner's log line."""
    text = " [model: %s" % (route["model_arg"] or "CLI default")
    if route["phase"]:
        text += ", %s phase" % route["phase"]
    if route["source"] not in ("auto", "default"):
        text += ", %s" % route["source"]
    if route["note"]:
        text += "; %s" % route["note"]
    return text + "]"


# --------------------------------------------------------------------------- finish

def _read_tail(path, limit=2000):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return ""
    return util.decode_bytes(data[-limit:]).strip()


def parse_claude_output(path):
    """Read the result of `claude -p --output-format json` (tolerating other formats)."""
    try:
        with open(path, "rb") as handle:
            raw = util.decode_bytes(handle.read()).strip()
    except OSError:
        return None
    if not raw:
        return None
    obj = None
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            dicts = [item for item in loaded if isinstance(item, dict)]
            results = [item for item in dicts if item.get("type") == "result"]
            obj = (results or dicts or [None])[-1]
        elif isinstance(loaded, dict):
            obj = loaded
    except ValueError:
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("type") == "result":
                obj = item
                break
    if obj is None:
        return {"result": raw, "is_error": False, "subtype": None, "structured": False,
                "complete": False}
    result = obj.get("result")
    return {
        "result": result if isinstance(result, str) else "",
        "is_error": bool(obj.get("is_error")),
        "subtype": obj.get("subtype"),
        "session_id": obj.get("session_id"),
        "cost_usd": obj.get("total_cost_usd"),
        "duration_ms": obj.get("duration_ms"),
        "num_turns": obj.get("num_turns"),
        "denials": len(obj.get("permission_denials") or []),
        "api_error_status": obj.get("api_error_status"),
        "model_usage": obj.get("modelUsage"),
        "usage": obj.get("usage"),
        "errors": [str(item) for item in obj.get("errors") or []
                   if isinstance(obj.get("errors"), list)],
        "structured": True,
        # Claude Code prints this object once, at the very end of a session. A session that
        # was stopped (by its budget, say) ends with one too, without a `result` text.
        "complete": obj.get("type") == "result" and (isinstance(result, str)
                                                     or bool(obj.get("subtype"))),
    }


def _first_line(text, limit=500):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def judge(parsed, exit_code, timed_out, interrupted, stderr_tail, timeout_minutes):
    """Decide the outcome of a run. Returns (outcome, error, summary)."""
    text = parsed["result"] if parsed else ""
    marker = None
    for match in _MARKER_RE.finditer(text or ""):
        marker = match
    summary = (marker.group(2).strip() if marker else "") or _first_line(text)
    if interrupted:
        return "interrupted", "the runner was interrupted while the task was running", summary
    if timed_out:
        return "timeout", "timed out after %g minute(s) and was stopped" % timeout_minutes, summary
    if exit_code != 0:
        detail = (_first_line(text) or "; ".join((parsed or {}).get("errors") or [])
                  or _first_line(stderr_tail) or "no output")
        return "failed", "claude exited with code %s: %s" % (exit_code, detail), summary
    if parsed is None:
        return "failed", "claude exited normally but produced no output", summary
    if parsed["is_error"] or (parsed["subtype"] and parsed["subtype"] != "success"):
        return "failed", "claude reported an error (%s): %s" % (
            parsed["subtype"] or "error", _first_line(text) or "no details"), summary
    if marker is not None and marker.group(1).upper() == "FAILED":
        reason = marker.group(2).strip() or "the task reported failure without a reason"
        if parsed.get("denials"):
            reason += (" [%d tool call(s) were denied by the permission mode; allow the tools "
                       "this task needs or choose another permission mode]" % parsed["denials"])
        return "failed", reason, summary
    return "done", None, summary


def _commit_message(task, attempt):
    return ("%s (task #%d)\n\nQueued task #%d, attempt %d, executed by moa7amed-queue.\n\n"
            "Task-Queue-Id: %d\n" % (task["title"], task["id"], task["id"], attempt, task["id"]))


def finish(q, runner_id, run_id, exit_code=None, timed_out=False, interrupted=False,
           reconciled=False):
    """Record how a run ended, deal with its changes, and schedule what happens next.

    `reconciled` marks a run that finished before its runner died: it is judged from the
    result Claude left on disk, and because time has passed since then, the workspace is
    left exactly as found: nothing is committed or stashed on the run's behalf."""
    lock = require_lock(q, runner_id)
    run = q.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise QueueError("run %s does not exist" % run_id)
    if run["finished_at"]:
        raise QueueError("run %s was already finished (%s)" % (run_id, run["outcome"]))
    task = q.get(run["task_id"])
    opts = runner_options(q, lock)
    parsed = parse_claude_output(run["stdout_file"])
    if interrupted and (parsed or {}).get("complete"):
        # The runner was stopped at the very moment the task had finished. Its work is
        # complete, so it is recorded (and committed) like any other finished task rather
        # than set aside and done again.
        interrupted = False
        exit_code = 1 if parsed.get("is_error") else 0
    if reconciled:
        exit_code = 1 if (parsed or {}).get("is_error") else 0
    stderr_tail = _read_tail(run["stderr_file"])
    outcome, error, summary = judge(parsed, exit_code, timed_out, interrupted,
                                    stderr_tail, opts["timeout_minutes"])

    from . import workflow
    phase_row = q.conn.execute("SELECT phase FROM run_routing WHERE run_id=?", (run_id,)).fetchone()
    run_phase = phase_row["phase"] if phase_row else None
    if outcome == "done" and not reconciled:
        problem = workflow.verification_problem(workflow.get(q, task["id"]), opts, run_phase,
                                                (parsed or {}).get("result"))
        if problem:
            outcome, error = "failed", problem

    head_after, commit_sha, stash_ref, files, uncommitted = None, None, None, [], []
    cwd = run["cwd"]
    if run["is_git"] and cwd and os.path.isdir(cwd):
        try:
            dirty = gitops.status_lines(cwd)
            head_after = gitops.head(cwd)
            changed = set(gitops.paths_from_status(dirty))
            if head_after and head_after != run["head_before"]:
                # Claude committed on its own; or, after a crash, somebody did.
                commit_sha = _commit_of_run(cwd, run, task, head_after) if reconciled \
                    else head_after
                if commit_sha:
                    changed.update(gitops.changed_between(cwd, run["head_before"], commit_sha))
            files = sorted(changed)
            if reconciled:
                uncommitted = gitops.paths_from_status(dirty)
            elif outcome == "done" and dirty and opts["commit"] == "auto" and run["clean_start"]:
                sha, commit_error = gitops.commit_all(cwd, _commit_message(task, run["attempt"]))
                if commit_error:
                    outcome = "failed"
                    error = ("the task finished, but its changes could not be committed: %s"
                             % commit_error)
                elif sha:
                    commit_sha = head_after = sha
                    # The commit itself is the exact record of what the task changed
                    # (git status only names a new folder, not the files in it).
                    committed = gitops.changed_between(cwd, run["head_before"], sha)
                    files = sorted(set(committed)) or files
            if outcome != "done" and run["clean_start"] and not reconciled \
                    and gitops.status_lines(cwd):
                # The workspace was clean when the task started, so what is left belongs to
                # this run. Set it aside (never discard it) so the next task starts clean.
                stash_ref, stash_error = gitops.stash_all(
                    cwd, "moa7amed-queue: unfinished changes of task #%d (run %d, %s)"
                    % (task["id"], run_id, outcome))
                if stash_error:
                    error = "%s | leftover changes could not be stashed: %s" % (error, stash_error)
        except QueueError as exc:
            error = "%s | %s" % (error, exc) if error else str(exc)
    if stash_ref:
        error = "%s [leftover changes saved as git stash %s; restore with: git stash apply %s]" % (
            error, stash_ref[:10], stash_ref[:10])

    # A task that came from an approved plan is checked against the plan's architecture.
    compliance_block = False
    if outcome == "done" and commit_sha and str(task["source"]).startswith("plan:") \
            and not reconciled:
        try:
            from . import plan_store
            blocking, found = plan_store.check_run(q, task, run, cwd, run["head_before"],
                                                   commit_sha, opts.get("plan_compliance", "block"))
            if blocking:
                compliance_block = True
                outcome = "failed"
                error = ("the change does not comply with the approved plan: %s. The commit %s "
                         "is kept; nothing was reverted. Review it, then revise the plan "
                         "(`moa7amed-queue project revise`) or undo the change yourself and "
                         "`moa7amed-queue retry %d`."
                         % ("; ".join(item["message"] for item in blocking), commit_sha[:10],
                            task["id"]))
        except Exception as exc:  # noqa: BLE001 - a broken check must not lose the result
            with q.tx():
                q.log_event(task["id"], run_id, "plan-check-error", str(exc)[:300])

    # A session that stopped, before changing anything, to ask the owner about a concern.
    if not reconciled and outcome in ("done", "failed"):
        try:
            from . import challenge
            waiting = challenge.after_run(q, task, run, (parsed or {}).get("result"),
                                          bool(files or commit_sha or stash_ref), run_phase,
                                          opts, outcome)
        except Exception as exc:  # noqa: BLE001 - never lose a result over bookkeeping
            waiting = None
            with q.tx():
                q.log_event(task["id"], run_id, "challenge-error", str(exc)[:300])
        if waiting:
            outcome, error = "decision", waiting

    stamp = util.now_iso()
    attempts = task["attempts"]
    will_retry = False
    if outcome == "done":
        status, not_before = "done", None
    elif outcome == "decision":
        status, not_before = "pending", None
        attempts = max(0, attempts - 1)  # asking the owner does not use up an attempt
    elif outcome == "interrupted":
        status, not_before = "pending", None
        attempts = max(0, attempts - 1)  # stopping the runner does not use up an attempt
    elif compliance_block:
        status, not_before = "failed", None   # retrying would repeat the deviation
    elif (parsed or {}).get("subtype") == "error_max_budget_usd":
        # Running it again would spend the same again. It waits for you instead.
        status, not_before = "failed", None
        error = ("stopped by Claude Code because the task reached its budget "
                 "(max_budget_usd, or what was left of cost_limit_run_usd). It was not retried "
                 "automatically: raise the budget and `moa7amed-queue retry %d`. %s"
                 % (task["id"], error or ""))
    elif attempts < task["max_attempts"]:
        status, will_retry = "pending", True
        delay = int(opts["retry_backoff_seconds"]) * attempts
        not_before = util.iso(util.now() + datetime.timedelta(seconds=delay)) if delay else None
    else:
        status, not_before = "failed", None

    # Model routing may read the result differently: a refused model is not a failed
    # attempt, a finished phase is not a finished task, an escalation keeps a task alive.
    verdict = routing.settle(q, {
        "task": task, "run": run, "parsed": parsed, "config": opts, "exit_code": exit_code,
        "stderr_tail": stderr_tail, "outcome": outcome, "error": error, "status": status,
        "attempts": attempts, "not_before": not_before, "will_retry": will_retry,
        "summary": summary, "files": files, "stash_ref": stash_ref, "commit_sha": commit_sha})
    outcome, error, status = verdict["outcome"], verdict["error"], verdict["status"]
    attempts, not_before, will_retry = (verdict["attempts"], verdict["not_before"],
                                        verdict["will_retry"])
    task_done = verdict["task_done"] and status == "done"
    cost_warnings = _cost_warnings(q, lock, opts, task, (parsed or {}).get("cost_usd"),
                                   reconciled)

    with q.tx():
        require_lock(q, runner_id)
        verdict["apply"](q)
        try:
            workflow_note = workflow.after_run(
                q, task, run, outcome, (parsed or {}).get("result"), files, opts,
                stopped_by="budget" if str((parsed or {}).get("subtype") or "").startswith(
                    "error_max_budget") else None)
        except Exception as exc:  # noqa: BLE001 - never lose a result over bookkeeping
            workflow_note = None
            q.log_event(task["id"], run_id, "workflow-error", str(exc)[:300])
        if workflow_note:
            q.log_event(task["id"], run_id, "workflow", workflow_note)
        if outcome == "done":
            from . import agents
            agents.take_over(q, task["id"])
        for warning in cost_warnings:
            q.log_event(task["id"], run_id, "cost-warning", warning)
        q.conn.execute(
            "UPDATE runs SET finished_at=?, outcome=?, exit_code=?, error=?, head_after=?, "
            "commit_sha=?, stash_ref=COALESCE(?, stash_ref), files_changed=?, session_id=?, "
            "cost_usd=?, duration_ms=?, num_turns=?, result_text=? WHERE id=?",
            (stamp, outcome, exit_code, error, head_after, commit_sha, stash_ref,
             json.dumps(files), (parsed or {}).get("session_id"), (parsed or {}).get("cost_usd"),
             (parsed or {}).get("duration_ms"), (parsed or {}).get("num_turns"),
             ((parsed or {}).get("result") or "")[:20000], run_id))
        q.conn.execute(
            "UPDATE tasks SET status=?, attempts=?, max_attempts=?, not_before=?, updated_at=?, "
            "finished_at=?, last_error=?, result_summary=COALESCE(?, result_summary), "
            "commit_sha=COALESCE(?, commit_sha) WHERE id=?",
            (status, attempts, verdict["max_attempts"], not_before, stamp,
             stamp if status in ("done", "failed") else None,
             error, summary if task_done else None,
             commit_sha if outcome == "done" else None, task["id"]))
        q.conn.execute("UPDATE runner_lock SET tasks_run=tasks_run+?, current_run_id=NULL, "
                       "heartbeat_at=? WHERE id=1", (0 if reconciled else 1, stamp))
        q.conn.execute("DELETE FROM process_tokens WHERE kind='run' AND ref=?", (str(run_id),))
        if reconciled and uncommitted:
            # See _check_reconcile_hold: nothing starts on top of these files.
            earlier = _reconcile_hold(q) or {}
            if not _same_directory(earlier.get("cwd"), cwd):
                earlier = {}
            try:
                every = gitops.paths_from_status(gitops.status_lines(cwd, every_file=True))
            except QueueError:
                every = list(uncommitted)
            try:
                started = util.parse_iso(run["started_at"]).timestamp()
                finished = os.path.getmtime(run["stdout_file"])
            except (OSError, TypeError, ValueError):
                started = finished = None
            owners = dict(earlier.get("owners") or {})
            owners.update({path: _owner(cwd, path, started, finished) for path in every})
            paths = sorted(set(every) | set(earlier.get("paths", [])))
            q.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('reconcile_hold', ?)",
                           (json.dumps({"task_id": task["id"], "run_id": run_id, "cwd": cwd,
                                        "paths": paths, "owners": owners, "at": stamp}),))
        if reconciled:
            q.log_event(task["id"], run_id, "reconciled",
                        "the run had finished before its runner stopped; recorded from the "
                        "result on disk%s" % ("; changes left uncommitted: %s"
                                              % ", ".join(uncommitted[:10]) if uncommitted else ""))
        if outcome == "done":
            detail = summary or "completed"
            if commit_sha:
                detail += " [commit %s]" % commit_sha[:10]
        else:
            detail = "%s%s" % (error, "; will retry" if will_retry else "")
        if verdict["note"]:
            detail += " [%s]" % verdict["note"]
        if verdict["actual_model"]:
            detail += " [ran on %s]" % verdict["actual_model"]
        q.log_event(task["id"], run_id, outcome, detail)
    return {"task_id": task["id"], "run_id": run_id, "title": task["title"], "outcome": outcome,
            "status": status, "will_retry": will_retry, "retry_at": not_before,
            "attempt": run["attempt"], "max_attempts": verdict["max_attempts"],
            "summary": summary, "error": error, "commit": commit_sha, "stash": stash_ref,
            "files_changed": files, "cost_usd": (parsed or {}).get("cost_usd"),
            "session_id": (parsed or {}).get("session_id"),
            "model": verdict["model"], "actual_model": verdict["actual_model"],
            "log_note": verdict["note"], "failure": verdict["failure"], "task_done": task_done,
            "uncommitted": uncommitted, "workspace": cwd, "cost_warnings": cost_warnings}
