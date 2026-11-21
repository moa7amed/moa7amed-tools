#!/usr/bin/env python3
"""Live validation: the queue driven by the real Claude Code CLI. THIS COSTS MONEY.

    python tests/live_validation.py --budget 1.00
    python tests/live_validation.py --budget 0.50 --only queue
    python tests/live_validation.py --budget 1.00 --json result.json --keep

Every other test in this folder uses a stand-in for `claude`. This script uses the real
one, under your own login, on throwaway projects in a temporary directory with their own
queue data directory. It never touches an existing project or your real queue.

Scenarios

    queue      Task A is running when task B is submitted (through the capture hook, the
               way Claude Code delivers a prompt). B must be stored without disturbing A;
               A must finish and meet its criteria; B must start only afterwards.
    reconcile  The runner is killed while a task runs; the task's Claude process finishes
               on its own. A new runner must record the result and not run it again.
    interrupt  The runner and the task's Claude process are both killed mid-task. A new
               runner must put the task back, keep what it left behind, and complete it.

    python tests/live_validation.py --budget 1.00 --rehearse

--rehearse runs the same scenarios and checks against the test stand-in for `claude`
instead of the real one. It costs nothing and proves the script, not the product.

Budget: --budget is a hard ceiling for the whole script. A scenario starts only if the
ceiling still covers its reserve; otherwise it is reported as NOT VERIFIED. Each task also
runs with Claude Code's own per-task limit (--max-budget-usd) and the queue's session
limit. Costs are the figures Claude Code reports; a run that was killed reports none, so
a fixed reserve is counted for it instead.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MQ = os.path.join(ROOT, "scripts", "mq.py")
HOOK = os.path.join(ROOT, "hooks", "capture-prompt.sh")
FAKE = os.path.join(HERE, "fake_claude.py")
RUNNER_PS1 = os.path.join(ROOT, "runner", "run-queue.ps1")
IS_WINDOWS = os.name == "nt"

PER_TASK_LIMIT = 0.30          # passed to claude as --max-budget-usd
RESERVE = {"queue": 0.35, "reconcile": 0.20, "interrupt": 0.40}
KILLED_RUN_RESERVE = 0.10      # counted for a run that was killed and reported no cost

TASK_A = ("Add a new line with the exact text `alpha ready` at the end of README.md, and create "
          "docs/alpha-notes.md with a three-line note describing that change. Change nothing "
          "else.\nAcceptance criteria: the last line of README.md is `alpha ready`; "
          "docs/alpha-notes.md exists and has at least three lines.")
TASK_B = ("Add a new line with the exact text `beta after alpha` at the end of README.md, "
          "directly after the line `alpha ready`. Change nothing else.\nAcceptance criteria: "
          "the last two lines of README.md are `alpha ready` and `beta after alpha`, in that "
          "order.")
TASK_C = ("Add a new line with the exact text `gamma ready` at the end of README.md. Change "
          "nothing else.\nAcceptance criteria: the last line of README.md is `gamma ready`.")
TASK_C2 = "Add a new line with the exact text `after gamma` at the end of README.md."
TASK_D = ("Add a new line with the exact text `delta ready` at the end of README.md, and create "
          "docs/delta-notes.md with a three-line note describing that change. Change nothing "
          "else.\nAcceptance criteria: the last line of README.md is `delta ready`; "
          "docs/delta-notes.md exists.")


class Stop(Exception):
    """A check failed; the scenario cannot go on."""


class Live(object):
    def __init__(self, args):
        self.args = args
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-live-"))
        self.home = os.path.join(self.tmp, "queue-home")
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(("MOA7AMED_QUEUE_", "CTQ_")) or key == "CLAUDE_PROJECT_DIR":
                del self.env[key]
        self.env["MOA7AMED_QUEUE_HOME"] = self.home
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.claude_bin = None
        if args.rehearse:
            if IS_WINDOWS:
                self.claude_bin = os.path.join(self.tmp, "fake-claude.cmd")
                with open(self.claude_bin, "w", newline="\r\n") as handle:
                    handle.write('@echo off\n"%s" "%s" %%*\n' % (sys.executable, FAKE))
            else:
                self.claude_bin = os.path.join(self.tmp, "fake-claude")
                with open(self.claude_bin, "w") as handle:
                    handle.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE))
                os.chmod(self.claude_bin, 0o755)
            self.env["FAKE_STATE_DIR"] = self.tmp
        self.results = []          # scenario dicts
        self.current = None
        self.processes = []
        self.reserved = 0.0

    # ------------------------------------------------------------- plumbing

    def mq(self, project, *arguments, **kwargs):
        expect = kwargs.pop("expect", 0)
        result = subprocess.run([sys.executable, "-B", MQ, "--project", project] + list(arguments),
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, timeout=300)
        out = result.stdout.decode("utf-8", errors="replace")
        if expect is not None and result.returncode != expect:
            raise Stop("`moa7amed-queue %s` exited %d: %s %s" % (
                " ".join(arguments), result.returncode, out[-300:],
                result.stderr.decode("utf-8", errors="replace")[-300:]))
        return out

    def rehearsal_script(self, directives):
        """Only when rehearsing: tell the stand-in what each task "does" and how long it takes."""
        if self.args.rehearse:
            script = os.path.join(self.tmp, "fake-script-%d.json" % len(self.results))
            with open(script, "w") as handle:
                json.dump(directives, handle)
            self.env["FAKE_SCRIPT"] = script

    def git(self, project, *arguments):
        result = subprocess.run(["git"] + list(arguments), cwd=project, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env)
        return result.stdout.decode("utf-8", errors="replace").strip()

    def new_project(self, name):
        project = os.path.join(self.tmp, name)
        os.makedirs(project)
        with open(os.path.join(project, "README.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# %s\n\nA throwaway project for a live validation run.\n" % name)
        for command in (["init", "-q", "-b", "main"],
                        ["-c", "user.name=Live Validation",
                         "-c", "user.email=live-validation@example.invalid",
                         "add", "-A"],
                        ["-c", "user.name=Live Validation",
                         "-c", "user.email=live-validation@example.invalid",
                         "commit", "-q", "-m", "initial commit"]):
            self.git(project, *command)
        # Commits made by the tasks' runner need an identity too; keep it inside the repo.
        self.git(project, "config", "user.name", "Live Validation")
        self.git(project, "config", "user.email", "live-validation@example.invalid")
        return project

    def database(self, project):
        from urllib.request import pathname2url
        where = json.loads(self.mq(project, "where", "--json"))
        conn = sqlite3.connect("file:%s?mode=ro" % pathname2url(where["database"]), uri=True,
                               timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def rows(self, project, sql, parameters=()):
        conn = self.database(project)
        try:
            return [dict(row) for row in conn.execute(sql, parameters).fetchall()]
        finally:
            conn.close()

    def wait(self, what, predicate, timeout=420, interval=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        raise Stop("timed out after %d s waiting for %s" % (timeout, what))

    def start_runner(self, project, kind, label, options=()):
        log = open(os.path.join(self.tmp, "%s.runner.log" % label), "wb")
        if kind == "powershell":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", RUNNER_PS1,
                       "-Project", project]
        else:
            command = [sys.executable, "-B", MQ, "--project", project, "run"] + list(options)
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS \
            else {"start_new_session": True}
        process = subprocess.Popen(command, env=self.env, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, **kwargs)
        process.log_path = log.name
        process.log_handle = log
        self.processes.append(process)
        return process

    def runner_output(self, process):
        process.log_handle.flush()
        with open(process.log_path, "rb") as handle:
            return handle.read().decode("utf-8", errors="replace")

    def kill(self, pid, tree):
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else []),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import signal
            try:
                if tree:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def alive(self, pid):
        if IS_WINDOWS:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
            return str(pid).encode() in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def submit_through_hook(self, project, prompt):
        """Deliver a prompt the way Claude Code does: JSON on the hook's standard input."""
        payload = json.dumps({"session_id": "live-validation", "cwd": project,
                              "hook_event_name": "UserPromptSubmit",
                              "prompt": "queue: " + prompt}).encode("utf-8")
        sh = shutil.which("sh")
        command = [sh, HOOK] if sh else [sys.executable, "-B", MQ, "hook-prompt"]
        env = dict(self.env)
        env["CLAUDE_PROJECT_DIR"] = project
        result = subprocess.run(command, input=payload, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120)
        return result.stdout.decode("utf-8", errors="replace")

    # ------------------------------------------------------------- bookkeeping

    def spent(self):
        """What Claude Code has reported so far, plus the reserves for killed runs."""
        total = self.reserved
        projects = os.path.join(self.home, "projects")
        if os.path.isdir(projects):
            for key in os.listdir(projects):
                path = os.path.join(projects, key, "queue.db")
                if not os.path.isfile(path):
                    continue
                conn = sqlite3.connect(path, timeout=30)
                try:
                    total += conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM runs").fetchone()[0]
                    for (history,) in conn.execute("SELECT history FROM task_routing"):
                        for entry in json.loads(history or "[]"):
                            if entry.get("type") == "review" and entry.get("cost_usd"):
                                total += entry["cost_usd"]
                except sqlite3.Error:
                    pass
                finally:
                    conn.close()
        return total

    def check(self, condition, what, detail=None):
        entry = {"check": what, "passed": bool(condition)}
        if detail is not None:
            entry["detail"] = detail
        self.current["checks"].append(entry)
        print("    [%s] %s%s" % ("ok  " if condition else "FAIL", what,
                                 "  (%s)" % detail if detail is not None and not condition else ""))
        if not condition:
            raise Stop(what)

    def note(self, key, value):
        self.current["measurements"][key] = value

    def prepare(self, project, prompts):
        """Queue tasks and make sure each will run on the cheapest tier."""
        ids = []
        for prompt in prompts:
            out = json.loads(self.mq(project, "add", prompt, "--json"))
            ids.append(out["id"] if isinstance(out, dict) and "id" in out else out["task"]["id"])
        routed = {}
        for task_id in ids:
            shown = json.loads(self.mq(project, "show", str(task_id), "--json"))
            tier = (shown.get("routing") or {}).get("selectedModel")
            routed[task_id] = tier
            if tier != "haiku":
                self.mq(project, "model", "haiku", str(task_id))
        self.note("router_choice", routed)
        return ids

    # ------------------------------------------------------------- scenarios

    def scenario_queue(self):
        project = self.new_project("project-queue")
        self.rehearsal_script({"1": ["sleep 8", "append README.md alpha ready",
                                     "write docs/alpha-notes.md one"],
                               "2": ["append README.md beta after alpha"]})
        (task_a,) = self.prepare(project, [TASK_A])
        runner = self.start_runner(project, "powershell" if IS_WINDOWS else "python", "queue")
        self.wait("task A to start", lambda: self.rows(
            project, "SELECT 1 FROM runs WHERE task_id=? AND child_pid IS NOT NULL", (task_a,)))
        # --- A is running: submit B the way a prompt arrives while Claude is busy.
        answer = self.submit_through_hook(project, TASK_B)
        submitted = time.time()
        self.check('"decision": "block"' in answer and "Queued as task #2" in answer,
                   "the prompt submitted during A was captured and kept from the session", answer[:200])
        state = {row["id"]: row for row in self.rows(project, "SELECT * FROM tasks")}
        self.check(state[2]["status"] == "pending" and state[2]["prompt"] == TASK_B
                   and state[2]["source"] == "hook",
                   "B is stored verbatim and pending while A runs")
        self.check(state[task_a]["status"] == "running", "A was still running when B was stored")
        self.wait("the queue to finish", lambda: runner.poll() is not None, timeout=600)
        output = self.runner_output(runner)
        self.note("runner_exit_code", runner.returncode)
        runs = self.rows(project, "SELECT r.*, rr.model_arg, rr.actual_model, rr.usage FROM runs r "
                                  "LEFT JOIN run_routing rr ON rr.run_id = r.id ORDER BY r.id")
        tasks = {row["id"]: row for row in self.rows(project, "SELECT * FROM tasks")}
        self.note("runs", [{key: run[key] for key in ("task_id", "attempt", "outcome", "started_at",
                                                      "finished_at", "cost_usd", "num_turns",
                                                      "duration_ms", "model_arg", "actual_model",
                                                      "usage")}
                           for run in runs])
        self.check(runner.returncode == 0, "the runner finished with exit code 0", output[-400:])
        self.check([run["task_id"] for run in runs] == [task_a, 2],
                   "exactly one run per task, A first and B second")
        self.check(all(run["outcome"] == "done" and run["attempt"] == 1 for run in runs),
                   "both tasks succeeded on their first attempt (A was not restarted)")
        self.check(runs[0]["started_at"] < tasks[2]["created_at"] < runs[0]["finished_at"],
                   "B was created between A's start and A's end",
                   "%s < %s < %s" % (runs[0]["started_at"], tasks[2]["created_at"],
                                     runs[0]["finished_at"]))
        self.check(runs[1]["started_at"] >= runs[0]["finished_at"],
                   "B started only after A had finished")
        self.check(len({run["session_id"] for run in runs}) == 2,
                   "each task ran in its own Claude Code session")
        with open(os.path.join(project, "README.md"), encoding="utf-8") as handle:
            lines = [line.strip() for line in handle.read().strip().splitlines()]
        self.check(lines[-2:] == ["alpha ready", "beta after alpha"],
                   "the acceptance criteria of A and B hold in the files", lines[-3:])
        self.check(os.path.isfile(os.path.join(project, "docs", "alpha-notes.md")),
                   "A's second file exists")
        log = self.git(project, "log", "--format=%s%n%b")
        self.check("Task-Queue-Id: 1" in log and "Task-Queue-Id: 2" in log,
                   "each task's changes were committed with its task id")
        self.check(self.git(project, "status", "--porcelain") == "", "the workspace is clean")
        recorded = [sorted(json.loads(run["files_changed"] or "[]")) for run in runs]
        self.check(recorded == [["README.md", "docs/alpha-notes.md"], ["README.md"]],
                   "the files each task changed are recorded by name", recorded)
        self.check(all(run["actual_model"] and "haiku" in run["actual_model"] for run in runs),
                   "each run used the model the queue chose",
                   [run["actual_model"] for run in runs])
        self.note("seconds_between_submit_and_b_start", round(
            time.mktime(time.strptime(runs[1]["started_at"][:19], "%Y-%m-%dT%H:%M:%S"))
            - time.mktime(time.gmtime(submitted)), 1))

    def scenario_reconcile(self):
        project = self.new_project("project-reconcile")
        self.rehearsal_script({"1": ["sleep 8", "append README.md gamma ready"]})
        task_c, task_c2 = self.prepare(project, [TASK_C, TASK_C2])
        runner = self.start_runner(project, "python", "reconcile-1")
        run = self.wait("the task's Claude process to be recorded", lambda: self.rows(
            project, "SELECT * FROM runs WHERE task_id=? AND child_pid IS NOT NULL", (task_c,)))[0]
        lock = self.rows(project, "SELECT * FROM runner_lock")[0]
        self.kill(lock["pid"], tree=False)        # the runner only; Claude keeps working
        self.wait("the killed runner to be gone", lambda: runner.poll() is not None, timeout=60)
        self.check(self.alive(run["child_pid"]) or os.path.getsize(run["stdout_file"]) > 0,
                   "the task's Claude process survived the runner")
        self.wait("Claude to finish on its own", lambda: not self.alive(run["child_pid"]),
                  timeout=600)
        still = self.rows(project, "SELECT status FROM tasks WHERE id=?", (task_c,))[0]["status"]
        self.check(still == "running", "nobody has recorded the result yet (task still 'running')")
        second = self.start_runner(project, "python", "reconcile-2")
        self.wait("the second runner to stop", lambda: second.poll() is not None, timeout=300)
        output = self.runner_output(second)
        runs = self.rows(project, "SELECT * FROM runs ORDER BY id")
        events = [row["type"] for row in self.rows(project, "SELECT type FROM events ORDER BY id")]
        tasks = {row["id"]: row for row in self.rows(project, "SELECT * FROM tasks")}
        self.note("runs", [{key: item[key] for key in ("task_id", "outcome", "cost_usd")}
                           for item in runs])
        self.note("second_runner_exit_code", second.returncode)
        self.check(len(runs) == 1 and runs[0]["task_id"] == task_c,
                   "the task was executed exactly once: no second run exists")
        self.check(runs[0]["outcome"] == "done" and tasks[task_c]["status"] == "done",
                   "its result was recorded as done from what Claude left on disk")
        self.check("reconciled" in events, "the history says the result was reconciled")
        self.check("had already finished before the runner stopped" in output
                   and "commit them (README.md)" in output,
                   "the runner said so and named the file to review", output[-400:])
        with open(os.path.join(project, "README.md"), encoding="utf-8") as handle:
            last = handle.read().strip().splitlines()[-1].strip()
        self.check(last == "gamma ready", "the task's work is in the workspace, untouched", last)
        self.check(tasks[task_c2]["status"] == "pending" and second.returncode == 3,
                   "the next task was not started on top of uncommitted work (exit code 3)",
                   "status %s, exit %s" % (tasks[task_c2]["status"], second.returncode))
        self.check("finished before the runner stopped and its changes were never committed"
                   in output, "the stop message explains whose changes these are", output[-500:])
        self.mq(project, "cancel", str(task_c2))

    def scenario_interrupt(self):
        project = self.new_project("project-interrupt")
        self.rehearsal_script({"1": ["sleep-once 120", "append README.md delta ready",
                                     "write docs/delta-notes.md one"]})
        (task_d,) = self.prepare(project, [TASK_D])
        runner = self.start_runner(project, "powershell" if IS_WINDOWS else "python",
                                   "interrupt-1")
        run = self.wait("the task's Claude process to be recorded", lambda: self.rows(
            project, "SELECT * FROM runs WHERE task_id=? AND child_pid IS NOT NULL", (task_d,)))[0]
        time.sleep(4)                               # let the session get under way
        lock = self.rows(project, "SELECT * FROM runner_lock")[0]
        self.kill(lock["pid"], tree=True)
        self.kill(run["child_pid"], tree=True)
        self.reserved += KILLED_RUN_RESERVE
        self.wait("the killed runner to be gone", lambda: runner.poll() is not None, timeout=60)
        self.wait("the killed Claude process to be gone", lambda: not self.alive(run["child_pid"]),
                  timeout=60)
        before = self.rows(project, "SELECT status, attempts FROM tasks WHERE id=?", (task_d,))[0]
        self.check(before["status"] == "running",
                   "after the kill the task is still marked running (nothing cleaned up yet)")
        # Whatever the killed run left half-done is set aside in a stash, not built upon
        # (with the default on_dirty=stop the runner would stop here and list the files).
        second = self.start_runner(project, "python", "interrupt-2", ["--on-dirty=stash"])
        self.wait("the second runner to finish", lambda: second.poll() is not None, timeout=600)
        output = self.runner_output(second)
        runs = self.rows(project, "SELECT * FROM runs ORDER BY id")
        task = self.rows(project, "SELECT * FROM tasks WHERE id=?", (task_d,))[0]
        self.note("runs", [{key: item[key] for key in ("task_id", "attempt", "outcome", "cost_usd",
                                                       "stash_ref")} for item in runs])
        self.note("second_runner_exit_code", second.returncode)
        self.check([item["outcome"] for item in runs] == ["interrupted", "done"],
                   "first run recorded as interrupted, second as done",
                   [item["outcome"] for item in runs])
        self.check(task["status"] == "done" and task["attempts"] == 2,
                   "the task is done; the crashed run counted as its first attempt",
                   "status %s, attempts %s" % (task["status"], task["attempts"]))
        self.check(second.returncode == 0, "the second runner finished with exit code 0",
                   output[-400:])
        with open(os.path.join(project, "README.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.check(text.strip().splitlines()[-1].strip() == "delta ready"
                   and text.count("delta ready") == 1,
                   "the acceptance criterion holds and the line was not added twice")
        self.check(os.path.isfile(os.path.join(project, "docs", "delta-notes.md")),
                   "the task's second file exists")
        self.check(self.git(project, "status", "--porcelain") == "", "the workspace is clean")
        stashes = self.git(project, "stash", "list")
        self.note("leftovers_of_the_killed_run", "stashed: %s" % stashes if stashes
                  else "the killed run had not changed any file yet")
        self.check("Task-Queue-Id: %d" % task_d in self.git(project, "log", "--format=%b"),
                   "the work was committed once, by the run that completed")

    # ------------------------------------------------------------- driver

    def run(self):
        claude = self.claude_bin or shutil.which("claude")
        if not claude:
            print("live_validation: the `claude` command was not found")
            return 2
        version = subprocess.run([claude, "--version"], stdout=subprocess.PIPE).stdout.decode().strip()
        print("%sClaude Code %s; budget $%.2f; data in %s"
              % ("REHEARSAL with the stand-in CLI, nothing is spent. " if self.args.rehearse else "",
                 version, self.args.budget, self.tmp))
        os.makedirs(self.home)
        settings = {"max_budget_usd": PER_TASK_LIMIT, "cost_limit_run_usd": self.args.budget,
                    "cost_warn_task_usd": 0.15, "routing_classifier": "off",
                    "timeout_minutes": 8, "max_attempts": 2, "retry_backoff_seconds": 5}
        if self.claude_bin:
            settings["claude_bin"] = self.claude_bin
        elif not self.args.full_environment:
            # Task sessions run without this machine's personal instructions, other plugins,
            # hooks and MCP servers, so nothing unrelated reacts to the throwaway sessions.
            settings["claude_args"] = ["--safe-mode"]
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            json.dump(settings, handle)
        scenarios = [name for name in ("queue", "reconcile", "interrupt")
                     if not self.args.only or name in self.args.only.split(",")]
        try:
            for name in scenarios:
                self.current = {"scenario": name, "checks": [], "measurements": {}}
                self.results.append(self.current)
                remaining = self.args.budget - self.spent()
                if remaining < RESERVE[name]:
                    self.current["status"] = "not verified"
                    self.current["reason"] = ("budget: $%.2f left, this scenario reserves $%.2f"
                                              % (remaining, RESERVE[name]))
                    print("\n%s: NOT VERIFIED (%s)" % (name, self.current["reason"]))
                    continue
                print("\n%s" % name)
                before, started = self.spent(), time.time()
                try:
                    getattr(self, "scenario_" + name)()
                    self.current["status"] = "passed"
                except Stop as exc:
                    self.current["status"] = "failed"
                    self.current["reason"] = str(exc)
                except Exception as exc:  # report, do not lose the cost figures
                    self.current["status"] = "failed"
                    self.current["reason"] = "%s: %s" % (type(exc).__name__, exc)
                self.current["cost_usd"] = round(self.spent() - before, 4)
                self.current["seconds"] = round(time.time() - started, 1)
                print("  -> %s, $%.4f, %.0f s" % (self.current["status"], self.current["cost_usd"],
                                                self.current["seconds"]))
        finally:
            for process in self.processes:
                if process.poll() is None:
                    self.kill(process.pid, tree=True)
                process.log_handle.close()
        summary = {"claude_code": version, "rehearsal": bool(self.args.rehearse),
                   "task_sessions": "full environment" if self.args.full_environment
                   else "claude --safe-mode",
                   "budget_usd": self.args.budget,
                   "spent_usd": round(self.spent(), 4),
                   "of_which_reserve_for_killed_runs_usd": round(self.reserved, 4),
                   "measured_at": time.strftime("%Y-%m-%d %H:%M"), "scenarios": self.results}
        print("\nSpent $%.4f of $%.2f (includes $%.2f reserve for killed runs, whose cost Claude "
              "Code did not report)" % (summary["spent_usd"], self.args.budget, self.reserved))
        if self.args.json:
            with open(self.args.json, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)
        if self.args.keep:
            print("kept: %s" % self.tmp)
        else:
            shutil.rmtree(self.tmp, onerror=lambda function, path, _info: (
                os.chmod(path, stat.S_IWRITE), function(path)))
        return 0 if all(item["status"] == "passed" for item in self.results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    parser.add_argument("--budget", type=float, required=True,
                        help="hard ceiling in US dollars for the whole script")
    parser.add_argument("--only", help="comma-separated scenarios: queue,reconcile,interrupt")
    parser.add_argument("--json", metavar="FILE", help="write the full result to this file")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--full-environment", action="store_true",
                        help="run task sessions with your CLAUDE.md, plugins, hooks and MCP "
                             "servers (default: claude --safe-mode)")
    parser.add_argument("--rehearse", action="store_true",
                        help="use the test stand-in instead of the real CLI (free; checks "
                             "this script, not the product)")
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    return Live(args).run()


if __name__ == "__main__":
    sys.exit(main())
