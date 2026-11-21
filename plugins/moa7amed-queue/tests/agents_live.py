#!/usr/bin/env python3
"""Stage A multi-agent execution with the real Claude Code CLI. THIS COSTS MONEY.

    python tests/agents_live.py --budget 1.50 [--json result.json] [--keep]

Builds a throwaway git project (a small login module with an SQL injection and one
test) and queues the same task in three configurations, each in its own copy:

  parallel  agents chosen automatically (two read-only analysis roles expected), run at
            the same time; the runner is killed once the coordinator has started, and
            restarted: finished agents must not run again;
  serial    the same agents, one at a time (agent_max_parallel 1);
  single    no agents (agent_mode single).

Every model call uses Haiku (manual override) to keep the cost low. The script stops
starting configurations once the recorded spend reaches --budget. It checks overlap
(timestamps, pids), isolation, input revisions, that the live workspace was not touched
by agents, that the coordinator changed the code and reported verification, that the
project's tests pass afterwards, and reports wall time, agents, cost and the size of the
<agents> section the coordinator received.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
MQ = os.path.join(SCRIPTS, "mq.py")
sys.path.insert(0, SCRIPTS)
from mqlib import util  # noqa: E402

TASK = ("Fix the SQL injection in the login function in app/auth.py and add regression tests "
        "for the authentication flow.")
FILES = {
    "app/__init__.py": "",
    "app/auth.py": (
        "import sqlite3\n\n\n"
        "def connect(path=\":memory:\"):\n"
        "    db = sqlite3.connect(path)\n"
        "    db.execute(\"CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, password TEXT)\")\n"
        "    return db\n\n\n"
        "def add_user(db, name, password):\n"
        "    db.execute(\"INSERT INTO users VALUES (?, ?)\", (name, password))\n\n\n"
        "def login(db, name, password):\n"
        "    query = \"SELECT name FROM users WHERE name = '%s' AND password = '%s'\" % (name, password)\n"
        "    return db.execute(query).fetchone() is not None\n"),
    "tests/__init__.py": "",
    "tests/test_auth.py": (
        "import unittest\n\nfrom app import auth\n\n\n"
        "class LoginTest(unittest.TestCase):\n"
        "    def test_valid_login(self):\n"
        "        db = auth.connect()\n"
        "        auth.add_user(db, \"amal\", \"s3cret\")\n"
        "        self.assertTrue(auth.login(db, \"amal\", \"s3cret\"))\n\n\n"
        "if __name__ == \"__main__\":\n"
        "    unittest.main()\n"),
}
CONFIGS = (("parallel", {"agent_max_parallel": 3}, True),
           ("serial", {"agent_max_parallel": 1, "agent_mode": "parallel"}, False),
           ("single", {"agent_mode": "single"}, False))


def stamp(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3] if ts else "-"


def make_project(path):
    for name, text in FILES.items():
        target = os.path.join(path, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", newline="\n") as handle:
            handle.write(text)
    for command in (["init", "-q", "-b", "main"], ["config", "user.name", "Agents Live"],
                    ["config", "user.email", "agents-live@example.invalid"],
                    ["config", "core.autocrlf", "false"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git"] + command, cwd=path, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, required=True, help="total USD for all paid calls")
    parser.add_argument("--json")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--only", choices=[name for name, _, _ in CONFIGS], action="append")
    args = parser.parse_args(argv)
    if shutil.which("claude") is None:
        print("agents_live: the `claude` command was not found")
        return 2
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-agents-live-"))
    home = os.path.join(tmp, "queue-home")
    os.makedirs(home)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MOA7AMED_QUEUE_", "CTQ_"))}
    env.update(MOA7AMED_QUEUE_HOME=home, PYTHONIOENCODING="utf-8", DISABLE_AUTOUPDATER="1")
    checks, results, spent = [], {}, 0.0
    print("live-test budget: $%.2f (Haiku only); data: %s" % (args.budget, tmp))

    def check(ok, what, detail=None):
        checks.append({"check": what, "passed": bool(ok), "detail": detail})
        print("  [%s] %s%s" % ("ok  " if ok else "FAIL", what,
                               "" if detail is None else "  (%s)" % (detail,)))

    for label, extra, interrupt in CONFIGS:
        if args.only and label not in args.only:
            continue
        if spent >= args.budget * 0.85:
            print("\n%s: skipped, recorded spend $%.4f leaves too little of $%.2f" % (label, spent, args.budget))
            results[label] = {"skipped": "budget"}
            continue
        print("\n== %s ==" % label)
        project = os.path.join(tmp, label)
        os.makedirs(project)
        make_project(project)
        config = {"claude_args": ["--safe-mode"], "routing_classifier": "off", "timeout_minutes": 10,
                  "max_budget_usd": round(min(0.45, args.budget - spent), 2),
                  "allowed_tools": ["Bash(python -m unittest*)", "Bash(python3 -m unittest*)",
                                    "Bash(py -m unittest*)"],
                  "workflow_deep_approval": "never", "workflow_superpowers": "no"}
        config.update(extra)
        with open(os.path.join(home, "config.json"), "w") as handle:
            json.dump(config, handle)

        def mq(*arguments, expect=0):
            result = subprocess.run([sys.executable, "-B", MQ, "--project", project] + list(arguments),
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, timeout=1800)
            out = result.stdout.decode("utf-8", "replace")
            if expect is not None and result.returncode != expect:
                raise SystemExit("`%s` exited %d: %s %s" % (" ".join(arguments[:2]), result.returncode,
                                                            out[-600:], result.stderr.decode()[-600:]))
            return out

        task_id = json.loads(mq("add", "--model", "haiku", "--json", TASK))["id"]
        check(json.loads(mq("show", str(task_id), "--json"))["status"] == "pending",
              "%s: task persisted before running" % label)
        decision = json.loads(mq("agents", "evaluate", str(task_id), "--json"))
        print("  decision: %s - %s" % (decision["strategy"], decision["reason"]))
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, stdout=subprocess.PIPE,
                                     universal_newlines=True).stdout.strip()
        db_path = json.loads(mq("where", "--json"))["database"]
        started = time.time()
        runner = subprocess.Popen([sys.executable, "-B", MQ, "--project", project, "run"], env=env,
                                  stdin=subprocess.DEVNULL)
        interrupted = None
        if interrupt:
            deadline = time.time() + 900
            while time.time() < deadline and runner.poll() is None:
                try:
                    with sqlite3.connect(db_path, timeout=5) as conn:
                        row = conn.execute("SELECT COUNT(*) FROM runs WHERE task_id=?", (task_id,)).fetchone()
                except sqlite3.Error:
                    row = (0,)
                if row[0] > 0:
                    break
                time.sleep(0.3)
            if runner.poll() is None:
                before = json.loads(mq("agents", "show", str(task_id), "--json"))[0]["subtasks"]
                util.kill_tree(runner.pid)
                runner.wait(timeout=60)
                interrupted = {s["key"]: (s["status"], s["attempt"], s["started_ts"]) for s in before}
                print("  runner killed after the coordinator started; agents then: %s" % (
                    {k: v[0] for k, v in interrupted.items()},))
                dirty = subprocess.run(["git", "status", "--porcelain"], cwd=project, stdout=subprocess.PIPE,
                                       universal_newlines=True).stdout.strip()
                if dirty:
                    print("  workspace after the kill:\n    " + dirty.replace("\n", "\n    "))
                runner = subprocess.Popen([sys.executable, "-B", MQ, "--project", project, "run"], env=env,
                                          stdin=subprocess.DEVNULL)
        code = runner.wait(timeout=1800)
        wall = time.time() - started
        task = json.loads(mq("show", str(task_id), "--json"))
        info = (json.loads(mq("agents", "show", str(task_id), "--json")) or [{}])[0]
        subs = info.get("subtasks") or []
        summary = info.get("summary") or {}
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            runs = [dict(r) for r in conn.execute("SELECT id, outcome, cost_usd, prompt_file, result_text "
                                                  "FROM runs WHERE task_id=? ORDER BY id", (task_id,))]
            verified = conn.execute("SELECT verified FROM task_workflow WHERE task_id=?", (task_id,)).fetchone()
            stored = {r["key"]: json.loads(r["result"] or "null") for r in conn.execute(
                "SELECT key, result FROM agent_subtasks WHERE task_id=?", (task_id,))}
            agent_starts = conn.execute("SELECT COUNT(*) FROM agent_events WHERE task_id=? AND kind='started'",
                                        (task_id,)).fetchone()[0]
        prompt = ""
        if runs and runs[-1]["prompt_file"] and os.path.exists(runs[-1]["prompt_file"]):
            with open(runs[-1]["prompt_file"], encoding="utf-8", errors="replace") as handle:
                prompt = handle.read()
        block = prompt[prompt.find("<agents>"):prompt.find("</agents>") + 9] if "<agents>" in prompt else ""
        coordinator_cost = sum(r["cost_usd"] or 0 for r in runs)
        agents_cost = sum(s["cost_usd"] or 0 for s in subs)
        spent += coordinator_cost + agents_cost
        tests = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                               cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True)
        with open(os.path.join(project, "app", "auth.py")) as handle:
            code_after = handle.read()
        worktrees = subprocess.run(["git", "worktree", "list"], cwd=project, stdout=subprocess.PIPE,
                                   universal_newlines=True).stdout
        for s in subs:
            print("    %-20s pid %-6s %s -> %s  %-9s attempt %s  rev %s  $%s" % (
                s["key"], s["pid"], stamp(s["started_ts"]), stamp(s["finished_ts"]), s["status"], s["attempt"],
                s["input_fingerprint"], s["cost_usd"]))
        results[label] = {
            "task": task_id, "decision": decision, "runner_exit": code, "status": task["status"],
            "wall_s": round(wall, 1), "agents_wall_s": summary.get("wall_s"), "agents_time_s": summary.get("worker_s"),
            "max_concurrency": summary.get("max_concurrency"), "agents": len(subs),
            "agent_starts": agent_starts, "coordinator_runs": len(runs),
            "coordinator_cost_usd": round(coordinator_cost, 4), "agents_cost_usd": round(agents_cost, 4),
            "agents_usage_unknown": summary.get("usage_unknown"), "verified": verified[0] if verified else None,
            "agents_block_chars": len(block), "tests_after": tests.stdout.strip().splitlines()[-1:],
            "subtasks": [{k: s[k] for k in ("key", "role", "status", "pid", "attempt", "started_ts", "finished_ts",
                                            "isolation", "input_fingerprint", "cost_usd", "model_used", "summary")}
                         for s in subs],
            "interrupted": interrupted}
        print("  status %s, wall %.1fs, agents %d (max %s at once, agents wall %s s), coordinator runs %d, "
              "cost coordinator $%.4f + agents $%.4f; cumulative $%.4f" % (
                  task["status"], wall, len(subs), summary.get("max_concurrency"), summary.get("wall_s"),
                  len(runs), coordinator_cost, agents_cost, spent))
        print("  verified: %s" % (verified[0] if verified else None))
        print("  <agents> section: %d characters (~%d tokens)" % (len(block), len(block) // 4))

        check(task["status"] == "done", "%s: the parent task completed" % label, task["status"])
        check(bool(verified and verified[0]), "%s: the coordinator reported verification" % label)
        check(tests.returncode == 0, "%s: the project's tests pass afterwards" % label, tests.stdout[-300:])
        check("'%s'" not in code_after and "?" in code_after.split("def login", 1)[-1],
              "%s: the coordinator (only writer) parameterised the query" % label)
        check("agent-snapshots" not in worktrees, "%s: no agent snapshot left behind" % label)
        if label == "single":
            check(not subs and agent_starts == 0, "single: no agent ran")
            continue
        roles = sorted(s["role"] for s in subs)
        check(len(subs) == 2 and roles == ["security_review", "test_gap_analysis"],
              "%s: two justified analysis roles" % label, roles)
        check(all(s["status"] == "reported" for s in subs), "%s: every agent reported" % label,
              [s["status"] for s in subs])
        check(all((s["isolation"] or "").startswith("snapshot of HEAD (%s)" % head_before[:10]) for s in subs),
              "%s: agents ran on a snapshot of the starting commit" % label)
        check(all((stored.get(s["key"]) or {}).get("input_revision") == s["input_fingerprint"] for s in subs),
              "%s: results echo their input revision" % label)
        check(len({s["pid"] for s in subs}) == len(subs), "%s: one process per agent" % label)
        check("blocked" not in json.dumps(info.get("events") or []).lower() or task["status"] == "done",
              "%s: the live workspace was not changed by agents" % label)
        check(bool(block), "%s: the coordinator received the agents' findings" % label)
        if label == "parallel":
            check(summary.get("max_concurrency") == 2 and
                  max(s["started_ts"] for s in subs) < min(s["finished_ts"] for s in subs),
                  "parallel: the agents overlapped", summary.get("intervals"))
        else:
            check(summary.get("max_concurrency") == 1, "serial: one agent at a time")
        if interrupted:
            same = all(interrupted[s["key"]][1:] == (s["attempt"], s["started_ts"]) for s in subs)
            check(same and agent_starts == len(subs),
                  "parallel: after the restart no finished agent ran again", agent_starts)
            check(len(runs) >= 2, "parallel: the coordinator ran again after the restart", len(runs))

    print("\nrecorded spend: $%.4f of the $%.2f live-test budget" % (spent, args.budget))
    summary = {"budget_usd": args.budget, "spent_usd": round(spent, 4), "results": results, "checks": checks,
               "data": tmp, "claude": subprocess.run([shutil.which("claude"), "--version"], stdout=subprocess.PIPE,
                                                     universal_newlines=True, env=env).stdout.strip()}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
