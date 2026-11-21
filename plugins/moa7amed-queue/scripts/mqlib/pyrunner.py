"""The portable runner (`moa7amed-queue run`) and the detached launcher (`moa7amed-queue launch`).

On Windows the native runner is runner/run-queue.ps1; it follows the same
protocol through the `moa7amed-queue runner-*`, `claim` and `finish` commands.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from . import engine, models, routing, util
from .util import QueueError

EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_INTERRUPTED = 130
HEARTBEAT_SECONDS = 30

# Runner settings: name -> PowerShell parameter. Shared by `moa7amed-queue run` and `moa7amed-queue launch`.
RUNNER_FLAGS = {"max_tasks": "MaxTasks", "poll_seconds": "PollSeconds", "watch": "Watch",
                "stop_on_failure": "StopOnFailure", "force": "Force"}


resolve_claude = models.resolve_claude


class RunnerLog(object):
    def __init__(self, q, quiet=False):
        self.path = os.path.join(q.dir, "runner.log")
        self.quiet = quiet

    def __call__(self, message):
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        if not self.quiet:
            print(line, flush=True)
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def _spawn(claim):
    """Start Claude for one task: prompt on stdin, output captured to the run's files."""
    env = dict(os.environ)
    env.update(claim["env"])
    command = [resolve_claude(claim["claude_bin"])] + list(claim["claude_args"])
    kwargs = {}
    if util.IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with open(claim["prompt_file"], "rb") as stdin, \
            open(claim["stdout_file"], "wb") as stdout, \
            open(claim["stderr_file"], "wb") as stderr:
        return subprocess.Popen(command, cwd=claim["cwd"], env=env, stdin=stdin, stdout=stdout,
                                stderr=stderr, **kwargs)


def _execute(q, runner_id, claim, log):
    """Run one claimed task to completion. Returns the `finish` summary."""
    try:
        process = _spawn(claim)
    except (OSError, QueueError) as exc:
        util.write_text_file(claim["stderr_file"], "could not start claude: %s\n" % exc)
        return engine.finish(q, runner_id, claim["run_id"], exit_code=127)
    engine.heartbeat(q, runner_id, claim["run_id"], process.pid)
    started = last_beat = time.time()
    timeout = claim["timeout_seconds"]
    timed_out = False
    try:
        while True:
            try:
                process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                pass
            current = time.time()
            if timeout and current - started > timeout:
                timed_out = True
                log("task #%d exceeded its time limit of %d s; stopping it"
                    % (claim["task_id"], timeout))
                util.kill_tree(process.pid)
                process.wait()
                break
            if current - last_beat >= HEARTBEAT_SECONDS:
                engine.heartbeat(q, runner_id)
                last_beat = current
    except KeyboardInterrupt:
        log("interrupted; stopping task #%d" % claim["task_id"])
        util.kill_tree(process.pid)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        engine.finish(q, runner_id, claim["run_id"], exit_code=process.returncode,
                      interrupted=True)
        raise
    return engine.finish(q, runner_id, claim["run_id"], exit_code=process.returncode,
                         timed_out=timed_out)


def describe_result(result):
    head = "task #%d %s" % (result["task_id"], result["outcome"].upper())
    if result["outcome"] == "done":
        text = "%s: %s" % (head, result["summary"] or "completed")
        if result["commit"]:
            text += " [commit %s, %d file(s)]" % (result["commit"][:10],
                                                   len(result["files_changed"]))
    else:
        text = "%s (attempt %d/%d): %s" % (head, result["attempt"], result["max_attempts"],
                                           result["error"])
        if result["will_retry"]:
            text += " -- will retry"
    if result.get("actual_model"):
        text += " [ran on %s]" % result["actual_model"]
    if result.get("log_note"):
        text += " -- %s" % result["log_note"]
    return text


def plan(q, options):
    """What a runner would do right now, without starting anything."""
    config = util.effective_config(q.dir, engine._clean_options(options))
    order = []
    for task in q.execution_order():
        row = routing.get(q, task["id"])
        if config["model"] and not (row is not None and row["mode"] == "manual"):
            model, why = config["model"], "set for the whole run"
        elif row is not None and (config["routing_enabled"] or row["mode"] == "manual"):
            model = "phased (opus plan, %s implement)" % row["selected_model"] if row["phased"] \
                else row["selected_model"]
            why = "manual override" if row["mode"] == "manual" else row["reason"]
        elif config["routing_enabled"]:
            model, why = "(classified when it starts)", ""
        else:
            model, why = "CLI default", "routing is off"
        order.append({"id": task["id"], "title": task["title"], "priority": task["priority"],
                      "model": model, "model_reason": why})
    runnable = {task["id"] for task in order}
    later = [{"id": task["id"], "title": task["title"], "state": q.state_of(task)}
             for task in q.tasks(["pending"]) if task["id"] not in runnable]
    return {"project": q.root, "next_tasks": order, "later_tasks": later, "counts": q.counts(),
            "lock": engine.lock_info(q),
            "command": [config["claude_bin"] or util.env("CLAUDE_BIN") or "claude"]
                       + engine.build_claude_args(config),
            "options": config}


def run(q, options=None, max_tasks=0, watch=False, poll_seconds=15, stop_on_failure=False,
        force=False, quiet=False):
    """Execute queued tasks one after another until the queue has nothing runnable."""
    log = RunnerLog(q, quiet)
    try:
        acquired = engine.acquire(q, os.getpid(), options, kind="python", force=force)
    except QueueError as exc:
        log("cannot start: %s" % exc)
        return exc.code
    runner_id = acquired["runner_id"]
    log("runner %s started for %s" % (runner_id, q.root))
    for item in acquired["recovered"]:
        log(item["message"])
    executed, failures, exit_code = 0, 0, EXIT_OK
    try:
        while True:
            if max_tasks and executed >= max_tasks:
                log("reached the limit of %d task(s)" % max_tasks)
                break
            claim = engine.claim(q, runner_id)
            if claim["action"] == "stop":
                log("STOPPED: %s" % claim["message"])
                exit_code = claim.get("exit_code") or engine.EXIT_WORKSPACE_STOP
                break
            if claim["action"] == "idle":
                if claim["reason"] == "backoff":
                    log(claim["message"])
                    time.sleep(min(claim["wait_seconds"], max(1, poll_seconds)))
                    engine.heartbeat(q, runner_id)
                    continue
                if watch:
                    time.sleep(max(1, poll_seconds))
                    engine.heartbeat(q, runner_id)
                    continue
                log("nothing more to run: %s" % claim["message"])
                if claim["reason"] == "blocked":
                    exit_code = EXIT_TASK_FAILED
                break
            log("task #%d started (attempt %d/%d)%s%s: %s"
                % (claim["task_id"], claim["attempt"], claim["max_attempts"],
                   claim["model_note"], claim.get("budget_note") or "", claim["title"]))
            if claim["stashed"]:
                log("uncommitted changes were set aside first in git stash %s" % claim["stashed"])
            result = _execute(q, runner_id, claim, log)
            executed += 1
            log(describe_result(result))
            for warning in result.get("cost_warnings") or []:
                log("COST WARNING: %s" % warning)
            if result["failure"]:
                if result["status"] == "failed":
                    failures += 1
                if stop_on_failure:
                    log("stopping because a task failed (stop_on_failure)")
                    exit_code = EXIT_TASK_FAILED
                    break
    except KeyboardInterrupt:
        exit_code = EXIT_INTERRUPTED
    except engine.LockLost as exc:
        log(str(exc))
        return exc.code
    finally:
        try:
            engine.release(q, runner_id)
        except Exception as exc:  # the lock is recovered by the next runner if this fails
            log("could not release the lock cleanly: %s" % exc)
    if failures and exit_code == EXIT_OK:
        exit_code = EXIT_TASK_FAILED
    log("runner %s finished: %d run(s), %d task(s) failed, exit code %d"
        % (runner_id, executed, failures, exit_code))
    return exit_code


def launch(q, options=None, runner_flags=None, hidden=False, engine_kind=None):
    """Start a runner as a separate, detached process and return immediately."""
    info = engine.lock_info(q)
    if info and info["alive"]:
        raise QueueError("a runner is already working on this project (pid %s, started %s)"
                         % (info["pid"], util.local_time(info["started_at"])),
                         code=engine.EXIT_LOCK_HELD)
    options = engine._clean_options(options)
    runner_flags = runner_flags or {}
    root = util.plugin_root()
    log_path = os.path.join(q.dir, "runner.log")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    kind = engine_kind or ("powershell" if util.IS_WINDOWS and powershell else "python")
    if kind == "powershell":
        if not powershell:
            raise QueueError("PowerShell was not found on PATH")
        options_file = os.path.join(q.dir, "launch-options.json")
        util.save_json_atomic(options_file, options)
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   os.path.join(root, "runner", "run-queue.ps1"),
                   "-Project", q.root, "-OptionsFile", options_file]
        for name, parameter in RUNNER_FLAGS.items():
            value = runner_flags.get(name)
            if value is True:
                command.append("-" + parameter)
            elif value not in (None, False, 0):
                command += ["-" + parameter, str(value)]
    else:
        command = [sys.executable, os.path.join(root, "scripts", "mq.py"),
                   "--project", q.root, "run", "--quiet"]
        for key, value in options.items():
            if key == "routing_enabled":
                if value is False:
                    command.append("--no-routing")
                continue
            flag = "--" + key.replace("_", "-")
            for item in (value if isinstance(value, list) else [value]):
                command.append("%s=%s" % (flag, item))  # '=' keeps values that start with '-'
        for name in RUNNER_FLAGS:
            value = runner_flags.get(name)
            if value is True:
                command.append("--" + name.replace("_", "-"))
            elif value not in (None, False, 0):
                command.append("--%s=%s" % (name.replace("_", "-"), value))
    process = _detach(command, q.root, hidden, log_path)
    return {"pid": process.pid, "runner": kind, "log": log_path, "command": command,
            "window": bool(util.IS_WINDOWS and not hidden)}


def _detach(command, cwd, hidden, log_path):
    env = dict(os.environ)
    if not util.IS_WINDOWS:
        out = open(os.path.join(os.path.dirname(log_path), "launch.out"), "ab")
        return subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    new_console, no_window, breakaway = 0x00000010, 0x08000000, 0x01000000
    kwargs = {"cwd": cwd, "env": env, "close_fds": True}
    if hidden:
        out = open(os.path.join(os.path.dirname(log_path), "launch.out"), "ab")
        kwargs.update(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        flags = no_window
    else:
        flags = new_console
    try:
        # Leave the job object of the calling session so the runner outlives it.
        return subprocess.Popen(command, creationflags=flags | breakaway, **kwargs)
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **kwargs)


def to_json(data):
    return json.dumps(data, indent=2, default=str)
