#!/usr/bin/env python3
"""Record the moa7amed-queue demo session: real commands, real Claude Code, synthetic project.

    python docs/media/source/record_demo.py --root G:/mq-demo --budget 2.00

THIS COSTS MONEY (real Claude Code sessions; about $1 when last run). It creates a
small synthetic project (`notes-app`) and a throwaway queue home under --root, runs the
scripted steps below, and writes every command, its exact output and its timing to
docs/media/source/session/session.json. Nothing is edited afterwards: the images and the
video are rendered from that file by render_media.py.

The commands are run as `python scripts/mq.py ...`, the program the `moa7amed-queue`
wrapper starts; they are shown as `moa7amed-queue ...`.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PLUGIN = os.path.join(REPO, "plugins", "moa7amed-queue")
MQ = os.path.join(PLUGIN, "scripts", "mq.py")
HOST = os.path.join(HERE, "demo_host.py")       # reports the host name as demo-pc
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))
from mqlib import util  # noqa: E402

APP = {
    "README.md": "# notes-app\n\nA tiny command-line notebok.\n",
    ".gitignore": "__pycache__/\n*.pyc\nnotes.db\n",
    "notes/__init__.py": "",
    "notes/store.py": (
        "import sqlite3\n\n\n"
        "def connect(path=\"notes.db\"):\n"
        "    db = sqlite3.connect(path)\n"
        "    db.execute(\"CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT, tag TEXT)\")\n"
        "    return db\n\n\n"
        "def add_note(db, text, tag=None):\n"
        "    cur = db.execute(\"INSERT INTO notes (text, tag) VALUES (?, ?)\", (text, tag))\n"
        "    db.commit()\n"
        "    return cur.lastrowid\n\n\n"
        "def list_notes(db):\n"
        "    return db.execute(\"SELECT id, text, tag FROM notes ORDER BY id\").fetchall()\n\n\n"
        "def search(db, term):\n"
        "    query = \"SELECT id, text, tag FROM notes WHERE text LIKE '%%%s%%'\" % term\n"
        "    return db.execute(query).fetchall()\n"),
    "notes/cli.py": (
        "import argparse\n\nfrom notes import store\n\n\n"
        "def main(argv=None):\n"
        "    parser = argparse.ArgumentParser(prog=\"notes\")\n"
        "    sub = parser.add_subparsers(dest=\"command\", required=True)\n"
        "    add = sub.add_parser(\"add\")\n"
        "    add.add_argument(\"text\")\n"
        "    add.add_argument(\"--tag\")\n"
        "    sub.add_parser(\"list\")\n"
        "    find = sub.add_parser(\"search\")\n"
        "    find.add_argument(\"term\")\n"
        "    args = parser.parse_args(argv)\n"
        "    db = store.connect()\n"
        "    if args.command == \"add\":\n"
        "        print(store.add_note(db, args.text, args.tag))\n"
        "    elif args.command == \"list\":\n"
        "        for row in store.list_notes(db):\n"
        "            print(*row)\n"
        "    else:\n"
        "        for row in store.search(db, args.term):\n"
        "            print(*row)\n\n\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"),
    "tests/__init__.py": "",
    "tests/test_store.py": (
        "import unittest\n\nfrom notes import store\n\n\n"
        "class StoreTest(unittest.TestCase):\n"
        "    def test_add_and_list(self):\n"
        "        db = store.connect(\":memory:\")\n"
        "        store.add_note(db, \"buy milk\", \"home\")\n"
        "        self.assertEqual(store.list_notes(db), [(1, \"buy milk\", \"home\")])\n\n\n"
        "if __name__ == \"__main__\":\n"
        "    unittest.main()\n"),
}

T1 = "Fix the typo in README.md: 'notebok' should be 'notebook'."
T2 = "Add a --tag option to `notes list` that shows only the notes with that tag, with unit tests."
T3 = "Fix the SQL injection in the search function in notes/store.py and add regression tests."
T4 = "Add a short docstring to every function in notes/cli.py."
REQUEST_BID = "Prepare the technical proposal for the visitor management system tender."
REQUEST_REPLICA = "Rebuild our order tracking prototype from its HTML pages."

STEPS = [
    ("submit", ["add", T1]),
    ("submit", ["add", T2]),
    ("submit", ["add", T3]),
    ("submit", ["depend", "3", "--on", "2"]),
    ("routing", ["model", "sonnet", "3"]),
    ("submit", ["status"]),
    ("routing", ["route", "1", "2", "3"]),
    ("workflow", ["workflow"]),
    ("agents", ["agents", "evaluate", "3"]),
    ("run", ["run"]),
    ("approval", ["status"]),
    ("approval", ["workflow", "3"]),
    ("approval", ["show", "3"]),
    ("approval", ["workflow", "approve", "3"]),
    ("run", ["run"]),
    ("agents", ["agents", "show", "3"]),
    ("verify", ["show", "3"]),
    ("verify", ["status"]),
    ("verify", ["usage"]),
    ("verify", ["git", "log", "--oneline"]),
    ("verify", ["python", "-m", "unittest"]),
    ("recovery", ["add", T4]),
    ("recovery", "KILL"),
    ("recovery", ["status"]),
    ("recovery", ["run"]),
    ("recovery", ["history", "4"]),
    ("bid", ["bid", "new", "b1", "--request", REQUEST_BID, "--mode", "technical"]),
    ("bid", ["bid", "doc", "b1", "../inputs/rfp-visitor-management.docx", "--type", "rfp"]),
    ("bid", ["bid", "status", "b1"]),
    ("replica", ["replica", "new", "r1", "--request", REQUEST_REPLICA, "--ownership", "own"]),
    ("replica", ["replica", "ref", "r1", "../inputs/order-tracking", "--kind", "source",
                 "--provenance", "our own prototype", "--permission", "own assets"]),
    ("replica", ["replica", "status", "r1"]),
]


SESSION_ID = re.compile(r"(session: |claude --resume )[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def redact(session):
    """Claude Code session ids are account identifiers: they are not published."""
    count = 0
    for item in session["steps"]:
        for index, (moment, text) in enumerate(item["lines"]):
            new, found = SESSION_ID.subn(lambda m: m.group(1) + "(session id removed)", text)
            if found:
                item["lines"][index] = [moment, new]
                count += found
    session["redactions"] = "%d Claude Code session id(s) replaced by '(session id removed)'" % count


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="an empty folder with a neutral path")
    parser.add_argument("--budget", type=float, required=True, help="USD for all paid calls")
    parser.add_argument("--out", default=os.path.join(HERE, "session", "session.json"))
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    if os.path.exists(root) and os.listdir(root):
        raise SystemExit("--root must be empty: %s" % root)
    project, home, inputs = (os.path.join(root, name) for name in ("notes-app", "queue-home", "inputs"))
    for path in (project, home, inputs):
        os.makedirs(path)
    for name, text in APP.items():
        target = os.path.join(project, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", newline="\n") as handle:
            handle.write(text)
    fixtures = os.path.join(PLUGIN, "tests", "fixtures")
    shutil.copy(os.path.join(fixtures, "bid", "rfp-visitor-management.docx"), inputs)
    shutil.copytree(os.path.join(fixtures, "replica_sample"), os.path.join(inputs, "order-tracking"))
    for command in (["init", "-q", "-b", "main"], ["config", "user.name", "Demo User"],
                    ["config", "user.email", "demo@example.invalid"], ["config", "core.autocrlf", "false"],
                    ["add", "-A"], ["commit", "-q", "-m", "notes-app: first version"]):
        subprocess.run(["git"] + command, cwd=project, check=True)
    with open(os.path.join(home, "config.json"), "w") as handle:
        json.dump({"claude_args": ["--safe-mode"], "routing_classifier": "off", "max_budget_usd": 0.5,
                   "cost_limit_run_usd": round(args.budget, 2), "timeout_minutes": 15,
                   "allowed_tools": ["Bash(python -m unittest*)", "Bash(py -m unittest*)"],
                   "workflow_superpowers": "no"}, handle)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MOA7AMED_QUEUE_", "CTQ_"))}
    env.update(MOA7AMED_QUEUE_HOME=home, PYTHONIOENCODING="utf-8", DISABLE_AUTOUPDATER="1",
               COLUMNS="100", NO_COLOR="1")
    session = {"recorded": time.strftime("%Y-%m-%d"), "budget_usd": args.budget,
               "claude": subprocess.run([shutil.which("claude"), "--version"], stdout=subprocess.PIPE,
                                        universal_newlines=True, env=env).stdout.strip(),
               "project": "notes-app (synthetic)", "steps": []}
    database = None

    def spent():
        if not database or not os.path.exists(database):
            return 0.0
        with sqlite3.connect(database) as conn:
            total = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs").fetchone()[0]
            try:
                total += conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM agent_subtasks").fetchone()[0]
            except sqlite3.Error:
                pass
        return total

    def run(scene, arguments, kill=False):
        started = time.time()
        raw_command = arguments[0] in ("git", "python")      # a plain shell command, shown as typed
        argv = ([shutil.which("git")] + arguments[1:] if arguments[0] == "git" else
                [sys.executable, "-B"] + arguments[1:] if arguments[0] == "python" else
                [sys.executable, "-B", HOST, MQ] + arguments)
        process = subprocess.Popen(argv, cwd=project, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        lines, killed = [], False
        if kill:
            import threading

            def reader():
                for raw in process.stdout:
                    lines.append((round(time.time() - started, 2), raw.decode("utf-8", "replace").rstrip("\n\r")))
            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            deadline = time.time() + 600
            while time.time() < deadline and process.poll() is None:
                with sqlite3.connect(database) as conn:
                    child = conn.execute("SELECT child_pid FROM runs WHERE task_id=4 AND child_pid IS NOT NULL"
                                         ).fetchone()
                if child:
                    time.sleep(3)          # the task's Claude session has started
                    util.kill_tree(process.pid)
                    killed = True
                    break
                time.sleep(0.5)
            process.wait(timeout=120)
            thread.join(timeout=10)
        else:
            for raw in process.stdout:
                lines.append((round(time.time() - started, 2), raw.decode("utf-8", "replace").rstrip("\n\r")))
            process.wait()
        step = {"scene": scene, "command": ("" if raw_command else "moa7amed-queue ") + " ".join(
                    a if (" " not in a and "`" not in a) else '"%s"' % a for a in arguments),
                "exit": process.returncode, "seconds": round(time.time() - started, 2),
                "killed": killed, "lines": lines, "spent_usd_after": None}
        session["steps"].append(step)
        step["spent_usd_after"] = round(spent(), 4)
        print("[%s] %s -> exit %s, %.1fs, %d lines, spent $%.4f" % (
            scene, step["command"][:70], process.returncode, step["seconds"], len(lines), step["spent_usd_after"]))
        return step

    def task_done(task_id):
        with sqlite3.connect(database) as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        return bool(row and row[0] == "done")

    for scene, arguments in STEPS:
        if database is None:
            run("setup", ["where", "--json"])
            database = json.loads("\n".join(l for _, l in session["steps"].pop()["lines"]))["database"]
        if spent() >= args.budget * 0.9:
            print("stopping: recorded spend $%.4f is close to the budget $%.2f" % (spent(), args.budget))
            session["stopped"] = "budget"
            break
        if arguments == "KILL":
            run(scene, ["run"], kill=True)
        else:
            run(scene, arguments)
        if scene == "recovery" and arguments == ["run"] and not task_done(4):
            # The interrupted session left changes: the owner decides. Here: set them
            # aside in a git stash (nothing is discarded) and run again.
            run(scene, ["status"])
            run(scene, ["run", "--on-dirty", "stash"])
    session["spent_usd"] = round(spent(), 4)
    session["git_log"] = subprocess.run(["git", "log", "--oneline"], cwd=project, stdout=subprocess.PIPE,
                                        universal_newlines=True).stdout.splitlines()
    tests = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    session["tests_after"] = {"exit": tests.returncode, "tail": tests.stdout.strip().splitlines()[-3:]}
    redact(session)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(session, handle, indent=1, ensure_ascii=False)
    print("spent $%.4f of $%.2f; session written to %s" % (session["spent_usd"], args.budget, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
