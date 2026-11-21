#!/usr/bin/env python3
"""Upgrade test: data written by an earlier version is taken over by this one.

    python tests/upgrade_check.py --old-root DIR --old-cli scripts/mq.py --old-version 0.3.0

DIR is a checkout of the earlier version's plugin folder (the release check at the
repository root exports one per version listed in release/baselines.json). The scenario:

  1. the OLD version creates a queue: tasks, a dependency, a priority, a cancelled task,
     a model override and settings
  2. THIS version opens it: nothing a user stored may differ, the health check passes,
     and the data was backed up before this version touched it
  3. THIS version runs the queue (with the test stand-in for Claude) and adds a task
  4. the OLD version reads the queue again, as it would after a rollback

Everything happens in a temporary directory with its own data directory and its own
empty Claude Code profile. Exit code 0 means every step held.
"""
import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_ROOT = os.path.dirname(HERE)
NEW_CLI = os.path.join(NEW_ROOT, "scripts", "mq.py")
with open(os.path.join(NEW_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as _handle:
    NEW_VERSION = json.load(_handle)["version"]
FAKE = os.path.join(HERE, "fake_claude.py")

FIRST = u"أضف اختبارا for the parser — café"
ACCEPTANCE = "the parser test passes"

checks = []


def check(condition, what):
    checks.append(what)
    if not condition:
        raise AssertionError(what)


def force_remove(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def run(command, env, cwd=None, expect=0):
    result = subprocess.run(command, env=env, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=300)
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    if expect is not None and result.returncode != expect:
        raise AssertionError("%s exited %d\n%s\n%s" % (" ".join(command[1:]), result.returncode,
                                                         out[-800:], err[-800:]))
    return out


def snapshot(database):
    """What the user stored, read without being able to change it."""
    from urllib.request import pathname2url
    conn = sqlite3.connect("file:%s?mode=ro" % pathname2url(database), uri=True)
    try:
        tasks = conn.execute("SELECT id, title, prompt, acceptance, workdir, priority, status, "
                             "max_attempts, source, created_at FROM tasks ORDER BY id").fetchall()
        deps = conn.execute("SELECT task_id, depends_on FROM task_deps ORDER BY 1, 2").fetchall()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        routing = []
        if "task_routing" in tables:
            routing = conn.execute("SELECT task_id, mode, selected_model, manual_override "
                                   "FROM task_routing ORDER BY task_id").fetchall()
        return {"tasks": tasks, "deps": deps, "routing": routing, "tables": tables}
    finally:
        conn.close()


def find_database(home):
    found = []
    for base, _dirs, files in os.walk(os.path.join(home, "projects")):
        found += [os.path.join(base, name) for name in files if name == "queue.db"]
    check(len(found) == 1, "the earlier version created exactly one queue database")
    return found[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--old-cli", required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    old_cli = os.path.join(os.path.abspath(args.old_root), args.old_cli)
    if not os.path.isfile(old_cli):
        sys.stderr.write("upgrade_check: %s does not exist\n" % old_cli)
        return 2

    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-upgrade-"))
    home, project = os.path.join(tmp, "home"), os.path.join(tmp, "project")
    os.makedirs(project)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("MOA7AMED_QUEUE_", "CTQ_")) or key in ("CLAUDE_PROJECT_DIR", "GIT_DIR",
                                                                 "GIT_WORK_TREE"):
            del env[key]
    env.update({
        # Both spellings, so the old and the new version use the same data directory.
        "MOA7AMED_QUEUE_HOME": home, "CTQ_HOME": home,
        "CLAUDE_CONFIG_DIR": os.path.join(tmp, "claude-config"),
        "GIT_CONFIG_GLOBAL": os.path.join(tmp, "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Upgrade Test", "GIT_AUTHOR_EMAIL": "upgrade-test@example.invalid",
        "GIT_COMMITTER_NAME": "Upgrade Test", "GIT_COMMITTER_EMAIL": "upgrade-test@example.invalid",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "FAKE_CLAUDE_LOG": os.path.join(tmp, "fake-claude.jsonl"), "FAKE_STATE_DIR": tmp,
    })
    try:
        for command in (["git", "init", "-q", "-b", "main"],
                        ["git", "commit", "-q", "--allow-empty", "-m", "initial commit"]):
            run(command, env, cwd=project)

        def old(*arguments, **kwargs):
            return run([sys.executable, "-B", old_cli, "--project", project] + list(arguments),
                       env, **kwargs)

        def new(*arguments, **kwargs):
            return run([sys.executable, "-B", NEW_CLI, "--project", project] + list(arguments),
                       env, **kwargs)

        # 1. The earlier version writes the data.
        old("add", FIRST, "--acceptance", ACCEPTANCE, "--priority", "high")
        old("add", "second task, after the first", "--after", "1")
        old("add", "a task that gets cancelled")
        old("add", "a task with its own model", "--model", "opus")
        old("cancel", "3")
        old("config", "set", "max_attempts", "3")
        old("config", "set", "timeout_minutes", "45", "--global")
        database = find_database(home)
        before = snapshot(database)
        check(len(before["tasks"]) == 4, "four tasks written by %s" % args.old_version)
        state_file = os.path.join(home, "state.json")
        if tuple(int(part) for part in args.old_version.split(".")[:3]) < (0, 3, 1):
            check(not os.path.exists(state_file), "the earlier version kept no state file")
        else:   # 0.3.1 and later record which version last ran
            with open(state_file, encoding="utf-8") as handle:
                check(json.load(handle).get("version") == args.old_version,
                      "the earlier version recorded itself in the state file")

        # 2. This version takes over.
        health = json.loads(new("doctor", "--json", expect=None))
        failed = [item for item in health["checks"] if item["status"] == "fail"]
        check(health["healthy"] and not failed, "the health check passes: %s" % failed)
        backups = os.path.join(home, "backups")
        made = sorted(os.listdir(backups)) if os.path.isdir(backups) else []
        check(len(made) == 1 and "auto-update-from-" in made[0],
              "the data was backed up on the first run of the new version")
        copy = snapshot(find_backup_database(os.path.join(backups, made[0])))
        check(copy["tasks"] == before["tasks"] and copy["deps"] == before["deps"],
              "the backup holds the data exactly as the earlier version left it")
        with open(os.path.join(home, "state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
        check(state.get("update_backup") == made[0], "the state file names that backup")

        listed = json.loads(new("list", "--json"))
        listed = listed["tasks"] if isinstance(listed, dict) else listed
        check(len(listed) == 4, "all four tasks are listed")
        after = snapshot(database)
        check(after["tasks"] == before["tasks"], "no stored task differs after opening the queue")
        check(after["deps"] == before["deps"], "dependencies are unchanged")
        check(after["routing"] == before["routing"], "model choices are unchanged")
        shown = json.loads(new("show", "1", "--json"))
        check(shown["prompt"] == FIRST and shown["acceptance"] == ACCEPTANCE,
              "the original prompt and acceptance criteria are intact, character for character")
        settings = json.loads(new("config", "show", "--json"))
        check(settings["max_attempts"] == 3 and settings["timeout_minutes"] == 45,
              "settings written by the earlier version apply")

        # 3. This version works with the data.
        if os.name == "nt":
            fake = os.path.join(tmp, "fake-claude.cmd")
            with open(fake, "w", newline="\r\n") as handle:
                handle.write('@echo off\n"%s" "%s" %%*\n' % (sys.executable, FAKE))
        else:
            fake = os.path.join(tmp, "fake-claude")
            with open(fake, "w") as handle:
                handle.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE))
            os.chmod(fake, 0o755)
        env["MOA7AMED_QUEUE_CLAUDE_BIN"] = fake
        new("config", "set", "routing_classifier", "off", "--global")
        new("add", "added by the new version")
        new("run")
        final = {row[0]: row[6] for row in snapshot(database)["tasks"]}
        check(final == {1: "done", 2: "done", 3: "cancelled", 4: "done", 5: "done"},
              "the queue ran to completion in the new version: %s" % final)
        with open(env["FAKE_CLAUDE_LOG"], encoding="utf-8") as handle:
            started = [json.loads(line) for line in handle if '"event": "start"' in line]
        order = [event["task"] for event in started]
        check(order.index(1) < order.index(2), "the dependency was respected (1 before 2)")
        check(3 not in order, "the cancelled task was not run")
        check([event["model"] for event in started if event["task"] == 4] == ["opus"],
              "the model override set in the earlier version was honoured")
        check(len(order) == len(set(order)), "no task ran twice")
        end = snapshot(database)
        check([row[:5] for row in end["tasks"][:4]] == [row[:5] for row in before["tasks"]],
              "prompts, acceptance criteria and working directories never changed")

        # 4. Going back: the earlier version still reads the queue.
        rolled = old("status")
        check("5" in rolled or "done" in rolled.lower(), "the earlier version reads the queue again")
        old_list = json.loads(old("list", "--json"))
        old_list = old_list["tasks"] if isinstance(old_list, dict) else old_list
        check(len(old_list) == 5, "the earlier version lists all five tasks after a rollback")
        old("add", "added after going back")
        check(len(snapshot(database)["tasks"]) == 6, "the earlier version can add tasks again")
        new("status")
        names = sorted(os.listdir(backups))
        ours = [name for name in names if "-v%s-" % NEW_VERSION in name]
        # This version backs up once per update from a given version (0.3.2 and later reuse
        # a backup made in the last day). 0.3.1 and later also back up when they find that
        # another version ran in between, which is their own, correct behaviour.
        check(len(ours) == 1, "this version made exactly one automatic backup: %s" % names)
        if tuple(int(part) for part in args.old_version.split(".")[:3]) < (0, 3, 1):
            check(len(names) == 1, "no further automatic backup without a version change")
    except AssertionError as exc:
        sys.stdout.write("upgrade from %s FAILED after %d check(s): %s\n"
                         % (args.old_version, len(checks) - 1, exc))
        return 1
    finally:
        if args.keep:
            sys.stdout.write("kept: %s\n" % tmp)
        else:
            shutil.rmtree(tmp, onerror=force_remove)
    sys.stdout.write("upgrade from %s and back: %d checks passed\n" % (args.old_version,
                                                                      len(checks)))
    return 0


def find_backup_database(folder):
    for base, _dirs, files in os.walk(folder):
        if "queue.db" in files:
            return os.path.join(base, "queue.db")
    raise AssertionError("the backup contains no queue database")


if __name__ == "__main__":
    sys.exit(main())
