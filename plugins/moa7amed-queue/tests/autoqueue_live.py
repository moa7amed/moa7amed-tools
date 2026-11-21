"""Auto Queue end to end with the real Claude Code CLI (costs money; Haiku, capped).

    python tests/autoqueue_live.py [--budget 1.00]

A throwaway git project and queue home are created in a temporary folder. Tasks are
submitted through the real capture-hook command (`hook-prompt`, the command Claude Code
runs for a prompt) and `moa7amed-queue add`; the runner that executes them is started by
Auto Queue itself, hidden, and runs real `claude -p` sessions on Haiku. The `--budget` is
the Auto Queue allowance and therefore the cap for the whole run (as reported by Claude
Code); every task also gets --max-budget-usd 0.20.

Steps: enable once; submit A; a runner starts; submit B while A runs (A is not
interrupted); A completes and passes its criteria; B starts after A; the runner exits when
idle; C starts a new runner; pause, D stays saved; resume, D runs once; a run is cut off
(the runner and its task process are killed) and the next start recovers it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MQ = os.path.join(ROOT, "scripts", "mq.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=1.00)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if not shutil.which("claude"):
        sys.exit("the Claude Code CLI is not installed")
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-auto-live-"))
    project = os.path.join(tmp, "auto project")
    home = os.path.join(tmp, "home")
    os.makedirs(project)
    os.makedirs(home)
    env = dict(os.environ, MOA7AMED_QUEUE_HOME=home, DISABLE_AUTOUPDATER="1",
               GIT_AUTHOR_NAME="Live Test", GIT_AUTHOR_EMAIL="auto-live@example.invalid",
               GIT_COMMITTER_NAME="Live Test", GIT_COMMITTER_EMAIL="auto-live@example.invalid")
    for name in ("MOA7AMED_QUEUE_RUNNER", "MOA7AMED_QUEUE_PROJECT", "CLAUDE_PROJECT_DIR"):
        env.pop(name, None)
    os.environ.clear()
    os.environ.update(env)
    with open(os.path.join(home, "config.json"), "w") as handle:
        json.dump({"model": "haiku", "max_budget_usd": 0.20, "routing_classifier": "off",
                   "workflow_superpowers": "no", "retry_backoff_seconds": 5,
                   "auto_queue_idle_timeout_seconds": 25, "timeout_minutes": 10}, handle)
    git = lambda *a: subprocess.run(["git"] + list(a), cwd=project, check=True,
                                    stdout=subprocess.DEVNULL)
    git("init", "-q", "-b", "main")
    with open(os.path.join(project, "README.md"), "w") as handle:
        handle.write("# Live Auto Queue fixture\n")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")

    from mqlib import engine, store, util
    results = []

    def mq(*argv):
        out = subprocess.run([sys.executable, MQ, "--project", project] + list(argv),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return out.stdout.decode("utf-8", errors="replace")

    def hook(prompt, prompt_id):
        payload = {"prompt": prompt, "session_id": "live-session", "cwd": project,
                   "prompt_id": prompt_id}
        out = subprocess.run([sys.executable, MQ, "hook-prompt"],
                             input=json.dumps(payload).encode("utf-8"), stdout=subprocess.PIPE,
                             env=dict(os.environ, CLAUDE_PROJECT_DIR=project))
        return json.loads(out.stdout.decode("utf-8"))["reason"]

    def queue():
        return store.Queue(util.resolve_project_root(project))

    def task(task_id):
        q = queue()
        try:
            return dict(q.get(task_id))
        finally:
            q.close()

    def lock():
        q = queue()
        try:
            return engine.lock_info(q)
        finally:
            q.close()

    def wait(predicate, timeout, what):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(1)
        raise AssertionError("timed out waiting for " + what)

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print("[%s] %s %s" % ("ok  " if ok else "FAIL", name, detail), flush=True)

    alive = lambda: (lock() or {}).get("alive")
    started = time.time()
    try:
        out = mq("auto", "enable", "--yes", "--budget", "%g" % args.budget)
        check("1 enable once", "Auto Queue is on" in out)

        reply = hook("queue: Create a file named alpha.txt containing exactly the word alpha. "
                     "Acceptance criteria: alpha.txt exists and contains alpha.", "live-a")
        check("2 submit A through the hook", "Task #1 saved" in reply, reply.split(".")[1])
        info = wait(lambda: lock() if alive() else None, 60, "a runner")
        check("3 runner started automatically", info["kind"] == "auto",
              "pid %s, kind %s" % (info["pid"], info["kind"]))

        wait(lambda: task(1)["status"] == "running", 120, "task A to run")
        reply = hook("queue: Create a file named beta.txt containing exactly the word beta. "
                     "Acceptance criteria: beta.txt exists and contains beta.", "live-b")
        check("4 submit B while A runs", "Task #2 saved" in reply and "Waiting behind task #1"
              in reply, reply.split("saved for")[1].split(".")[1].strip())

        wait(lambda: task(1)["status"] in ("done", "failed"), 600, "task A to finish")
        a = task(1)
        content = open(os.path.join(project, "alpha.txt"), encoding="utf-8").read().strip() \
            if os.path.exists(os.path.join(project, "alpha.txt")) else None
        runs_a = len(json.loads(mq("show", "1", "--json"))["runs"])
        check("5 B persisted, A not interrupted", task(2)["status"] in ("pending", "running",
                                                                         "done") and runs_a == 1,
              "A runs: %d" % runs_a)
        check("6 A completed and passed its criteria", a["status"] == "done" and content == "alpha",
              "status %s, alpha.txt=%r, commit %s" % (a["status"], content, a["commit_sha"]))

        wait(lambda: task(2)["status"] in ("done", "failed"), 600, "task B to finish")
        b = task(2)
        check("7 B ran after A", b["status"] == "done" and b["started_at"] >= a["finished_at"],
              "A finished %s, B started %s" % (a["finished_at"], b["started_at"]))

        wait(lambda: not alive(), 120, "the idle runner to exit")
        check("8 runner exited after the idle timeout", True)

        out = mq("add", "Create a file named gamma.txt containing exactly the word gamma. "
                        "Acceptance criteria: gamma.txt exists and contains gamma.")
        check("9 C restarts a runner", "Runner started" in out)
        wait(lambda: task(3)["status"] in ("done", "failed"), 600, "task C")
        check("9 C done", task(3)["status"] == "done")

        mq("auto", "pause")
        out = mq("add", "Create a file named delta.txt containing exactly the word delta. "
                        "Acceptance criteria: delta.txt exists and contains delta.")
        time.sleep(15)
        check("10 paused: D saved, not started", "Automatic execution is paused" in out
              and task(4)["status"] == "pending" and task(4)["attempts"] == 0)
        mq("auto", "resume")
        wait(lambda: task(4)["status"] in ("done", "failed"), 600, "task D")
        check("11 resumed: D executed once", task(4)["status"] == "done"
              and task(4)["attempts"] == 1, "attempts %d" % task(4)["attempts"])

        wait(lambda: not alive(), 120, "the idle runner to exit")
        out = mq("add", "Create five files named e1.txt to e5.txt; each contains its own "
                        "number. Create them one at a time. Acceptance criteria: the five files "
                        "exist with the right numbers.")
        wait(lambda: task(5)["status"] == "running", 120, "task E to run")
        time.sleep(8)
        info = lock()
        q = queue()
        run = q.conn.execute("SELECT child_pid FROM runs WHERE task_id=5 ORDER BY id DESC").fetchone()
        q.close()
        util.kill_tree(info["pid"])            # a crash: the runner and its task process die
        if run and run["child_pid"]:
            util.kill_tree(run["child_pid"])
        wait(lambda: not alive(), 60, "the killed runner to be gone")
        out = mq("auto", "start")       # nothing is pending: task E is still marked running

        def interrupted():
            q = queue()
            try:
                return q.conn.execute("SELECT COUNT(*) AS n FROM events WHERE task_id=5 AND "
                                      "type='interrupted'").fetchone()["n"]
            finally:
                q.close()

        wait(lambda: interrupted() >= 1, 120, "the interrupted run to be recovered")
        wait(lambda: task(5)["status"] in ("done", "failed") or not alive(), 900,
             "task E to finish or the runner to stop")
        e = task(5)
        left = subprocess.run(["git", "status", "--porcelain"], cwd=project,
                              stdout=subprocess.PIPE).stdout.decode().split()
        check("12a interrupted run recovered", interrupted() >= 1 and e["status"] != "running",
              "%s; %s" % ("task E %s after %d attempt(s)" % (e["status"], e["attempts"]),
                          "partial files left and not run over: %s" % " ".join(left[1::2])
                          if e["status"] == "pending" else "no partial changes"))
        if e["status"] == "pending":
            blockers = mq("auto", "blockers")
            check("12b blocker names the leftovers", "uncommitted changes" in blockers)
            git("stash", "-u")           # the owner sets the leftovers aside
            mq("auto", "start")
            wait(lambda: task(5)["status"] in ("done", "failed"), 900, "task E after cleanup")
            e = task(5)
        check("12c task E completed once the workspace was clean", e["status"] == "done",
              "after %d attempt(s)" % e["attempts"])
    finally:
        try:
            info = lock()
            if info and info["alive"]:
                util.kill_tree(info["pid"])
        except Exception:
            pass
        usage = mq("auto", "budget")
        print(usage.strip())
        print("elapsed %.0f s; fixture %s" % (time.time() - started, tmp))
        if not args.keep:
            time.sleep(2)
            shutil.rmtree(tmp, ignore_errors=True)
    failed = [name for name, ok, _ in results if not ok]
    print("%d checks, %d failed" % (len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
