"""Compatibility with versions 0.1.0 and 0.2.0, which were published as "claude-task-queue"
with the command `ctq`: their data, settings, environment variables, command name and
result markers must keep working, and moving the data must be safe."""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import IS_WINDOWS, MQ, ROOT, SH, QueueTestCase

DOCS = "Fix the typo in the README title."
HARD = "Fix the deadlock in the payment worker."


class LegacyHomeCase(QueueTestCase):
    """No data directory is configured: the tool has to find it under the user's home.
    The home directory is a temporary folder, so the real one is never touched."""

    def setUp(self):
        super(LegacyHomeCase, self).setUp()
        self.user = os.path.join(self.tmp, "user")
        os.makedirs(self.user)
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.user
        del os.environ["MOA7AMED_QUEUE_HOME"]
        self.legacy = os.path.join(self.user, ".claude-task-queue")
        self.current = os.path.join(self.user, ".moa7amed-queue")

    def make_legacy_data(self):
        """A data directory as an earlier version left it: settings, the model evidence and
        a queue with routed, overridden and finished tasks."""
        shutil.copytree(self.home, self.legacy)  # settings + the fake CLI's capabilities
        self.home = self.legacy
        self.mq("add", DOCS)
        self.mq("add", HARD, "--priority", "high")
        self.mq("add", "Add a search box to the customers page.", "--after", "1")
        self.mq("model", "sonnet", "3")
        self.mq("config", "set", "on_dirty", "stash")
        self.script(1, "write README.md fixed")
        code, out, _ = self.mq("run", "--max-tasks", "1")
        self.assertEqual(code, 0, out)
        return self.snapshot()

    def snapshot(self):
        """Everything a user would call "my queue", as the tool reports it."""
        tasks = self.mq_json("list")
        for task in tasks:
            task.pop("state", None)
        return {"tasks": tasks, "config": self.mq_json("config"),
                "history": self.mq_json("history", "--limit", "500")}


class DataOfEarlierVersions(LegacyHomeCase):

    def test_new_installation_uses_the_new_directory(self):
        self.mq("add", DOCS)
        self.assertTrue(os.path.isfile(os.path.join(self.current, "projects",
                                                    os.listdir(os.path.join(self.current, "projects"))[0],
                                                    "queue.db")))
        self.assertFalse(os.path.exists(self.legacy))
        self.assertEqual(self.mq_json("where")["home_origin"], "default location")

    def test_existing_data_is_used_where_it_is(self):
        before = self.make_legacy_data()
        self.assertFalse(os.path.exists(self.current))  # nothing was created next to it
        where = self.mq_json("where")
        self.assertEqual(os.path.realpath(where["home"]), os.path.realpath(self.legacy))
        self.assertEqual(where["home_origin"], "directory of an earlier version, used in place")
        self.assertIn("from before 0.3.0, still fully supported", self.mq("status")[1])
        # Tasks, priorities, dependencies, routing decisions, overrides and settings are intact.
        routing = {task["id"]: task["routing"] for task in before["tasks"]}
        # (the high-priority task has already run once in the old location)
        self.assertEqual(routing[2]["actualModel"], "claude-opus-test-1")
        self.assertEqual((routing[1]["selectedModel"], routing[1]["modelSelectionMode"]),
                         ("haiku", "auto"))
        self.assertEqual((routing[3]["selectedModel"], routing[3]["manualOverride"]),
                         ("sonnet", True))
        self.assertEqual(before["config"]["on_dirty"], "stash")
        # And the queue simply continues.
        code, out, _ = self.mq("run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.starts(), [(2, "opus"), (1, "haiku"), (3, "sonnet")])
        self.assertFalse(os.path.exists(self.current))

    def test_capture_hook_writes_to_the_existing_data(self):
        self.make_legacy_data()
        payload = json.dumps({"cwd": self.project, "prompt": "queue: Update the docs."})
        subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload.encode(),
                       stdout=subprocess.PIPE, check=True)
        self.assertEqual(self.task(4)["prompt"], "Update the docs.")
        self.assertFalse(os.path.exists(self.current))


class Migration(LegacyHomeCase):

    def test_dry_run_changes_nothing(self):
        before = self.make_legacy_data()
        code, out, _ = self.mq("migrate", "--dry-run")
        self.assertIn("Would move", out)
        self.assertIn("Nothing was changed", out)
        self.assertTrue(os.path.isdir(self.legacy))
        self.assertFalse(os.path.exists(self.current))
        self.assertEqual(self.snapshot(), before)

    def test_migrate_moves_everything_in_one_step(self):
        before = self.make_legacy_data()
        files = sorted(os.path.relpath(os.path.join(base, name), self.legacy)
                       for base, _dirs, names in os.walk(self.legacy) for name in names)
        report = self.mq_json("migrate")
        self.assertTrue(report["moved"])
        self.assertEqual(report["projects"], 1)
        self.assertFalse(os.path.exists(self.legacy))
        self.home = self.current
        moved = sorted(os.path.relpath(os.path.join(base, name), self.current)
                       for base, _dirs, names in os.walk(self.current) for name in names)
        self.assertEqual(moved, sorted(files + ["migration.json"]))
        self.assertEqual(self.snapshot(), before)  # identical queue, settings and history
        self.assertEqual(self.mq_json("where")["home_origin"], "default location")
        self.assertNotIn("from before 0.3.0", self.mq("status")[1])
        # Work continues from the new place; a second migrate has nothing to do.
        code, out, _ = self.mq("run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.starts(), [(2, "opus"), (1, "haiku"), (3, "sonnet")])
        self.assertIn("Nothing to migrate", self.mq("migrate")[1])

    def test_migrate_waits_for_an_active_runner(self):
        self.make_legacy_data()
        code, out, _ = self.mq("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        runner = json.loads(out)["runner_id"]
        code, _, err = self.mq("migrate", check=False)
        self.assertEqual(code, 1)
        self.assertIn("a runner is active", err)
        self.assertIn("Nothing was changed", err)
        self.assertTrue(os.path.isdir(self.legacy))
        self.assertFalse(os.path.exists(self.current))
        self.mq("runner-release", "--runner-id", runner)
        self.assertTrue(self.mq_json("migrate")["moved"])

    def test_migrate_never_merges_two_directories(self):
        before = self.make_legacy_data()
        os.makedirs(os.path.join(self.current, "projects"))
        with open(os.path.join(self.current, "config.json"), "w") as handle:
            handle.write("{}")
        code, out, _ = self.mq("migrate", check=False)
        self.assertEqual(code, 1)
        self.assertIn("Both directories exist, so nothing was moved", out)
        self.assertTrue(os.path.isfile(os.path.join(self.legacy, "config.json")))
        self.assertEqual(os.listdir(os.path.join(self.current, "projects")), [])
        # The old data is still readable when pointed at explicitly.
        os.environ["CTQ_HOME"] = self.legacy
        self.assertEqual(self.snapshot(), before)

    def test_explicit_directory_is_never_migrated(self):
        self.make_legacy_data()
        os.environ["CTQ_HOME"] = self.legacy
        self.assertIn("set explicitly (CTQ_HOME=", self.mq("migrate")[1])
        self.assertTrue(os.path.isdir(self.legacy))

    def test_worktree_keeps_working_after_the_move(self):
        shutil.copytree(self.home, self.legacy)
        self.home = self.legacy
        self.mq("add", "Add a search box to the customers page.")
        self.script(1, "write search.txt one")
        self.assertEqual(self.mq("run", "--workspace", "worktree")[0], 0)
        self.assertIn(".claude-task-queue", self.git("worktree", "list"))
        report = self.mq_json("migrate")
        self.assertEqual([item["ok"] for item in report["repaired"]], [True])
        self.home = self.current
        listing = self.git("worktree", "list")
        self.assertIn(".moa7amed-queue", listing)
        self.assertNotIn(".claude-task-queue", listing)
        self.mq("add", "Add pagination to the customers page.")
        self.script(2, "write pages.txt two")
        code, out, _ = self.mq("run", "--workspace", "worktree")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git("show", "task-queue:pages.txt"), "two")
        self.assertEqual(self.git("show", "task-queue:search.txt"), "one")


class LegacyNames(QueueTestCase):

    def test_legacy_data_directory_variable_is_honoured(self):
        other = os.path.join(self.tmp, "other-home")
        shutil.copytree(self.home, other)
        del os.environ["MOA7AMED_QUEUE_HOME"]
        os.environ["CTQ_HOME"] = other
        self.mq("add", DOCS)
        self.assertTrue(os.path.isdir(os.path.join(other, "projects")))
        self.assertFalse(os.path.isdir(os.path.join(self.home, "projects")))
        self.assertEqual(self.mq_json("where")["home_origin"],
                         "set by CTQ_HOME (name used before 0.3.0)")
        # The current name wins when both are set.
        os.environ["MOA7AMED_QUEUE_HOME"] = self.home
        self.mq("add", HARD)
        self.assertTrue(os.path.isdir(os.path.join(self.home, "projects")))

    def test_legacy_settings_variables_are_honoured(self):
        fake = os.environ.pop("MOA7AMED_QUEUE_CLAUDE_BIN")
        os.environ["CTQ_CLAUDE_BIN"] = fake
        self.add(DOCS)
        self.script(1, "write README.md fixed")
        code, out, _ = self.mq("run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.task(1)["status"], "done")

        def hook(prompt, **env):
            full = dict(os.environ)
            full.update(env)
            payload = json.dumps({"cwd": self.project, "prompt": prompt}).encode()
            return subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload, env=full,
                                  stdout=subprocess.PIPE).stdout.decode().strip()

        self.assertIn("Queued as task #2", hook("later: tidy up", CTQ_CAPTURE_PREFIX="later:"))
        self.assertEqual(hook("queue: must stay out", CTQ_RUNNER="1"), "")
        self.assertEqual(len(self.queue().tasks()), 2)

    def test_tasks_see_the_current_and_the_legacy_variables(self):
        self.add(DOCS)
        self.assertEqual(self.mq("run")[0], 0)
        start = self.events_of("start")[0]
        self.assertEqual(start["runner_env"], "1")
        self.assertEqual(os.path.realpath(start["project_env"]), os.path.realpath(self.project))
        self.assertEqual((start["legacy_env"]["CTQ_RUNNER"], start["legacy_env"]["CTQ_TASK_ID"]),
                         ("1", "1"))
        self.assertEqual(os.path.realpath(start["legacy_env"]["CTQ_PROJECT"]),
                         os.path.realpath(self.project))

    def test_result_markers_of_earlier_versions_are_understood(self):
        from mqlib import engine, routing
        for marker in ("MQ_RESULT", "CTQ_RESULT"):
            done = {"result": "All good.\n%s: DONE - added the endpoint" % marker,
                    "is_error": False, "subtype": "success"}
            self.assertEqual(engine.judge(done, 0, False, False, "", 60),
                             ("done", None, "added the endpoint"))
            failed = {"result": "%s: FAILED - tests are red" % marker, "is_error": False,
                      "subtype": "success", "denials": 0}
            self.assertEqual(engine.judge(failed, 0, False, False, "", 60)[:2],
                             ("failed", "tests are red"))
        for marker in ("MQ_ESCALATE", "CTQ_ESCALATE"):
            match = routing._ESCALATE_RE.search("%s: needs a redesign\nMQ_RESULT: FAILED - x" % marker)
            self.assertEqual(match.group(1), "needs a redesign")
        # New tasks are asked for the new marker.
        self.add(DOCS)
        self.mq("run")
        prompt = self.events_of("start")[0]["prompt"]
        self.assertIn("MQ_RESULT: DONE", prompt)
        self.assertNotIn("CTQ_", prompt)
        self.assertIn("(moa7amed-queue)", prompt)


class CommandNames(QueueTestCase):

    def launcher(self, *command):
        result = subprocess.run(list(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        return result.returncode, result.stdout.decode("utf-8", errors="replace").strip()

    @unittest.skipUnless(SH, "needs a POSIX shell (Git Bash on Windows)")
    def test_ctq_is_an_alias_of_the_command(self):
        new = os.path.join(ROOT, "bin", "moa7amed-queue")
        old = os.path.join(ROOT, "bin", "ctq")
        self.assertRegex(self.launcher(SH, new, "--version")[1], r"^moa7amed-queue \d")
        self.assertEqual(self.launcher(SH, old, "--version"), self.launcher(SH, new, "--version"))
        # Arguments and exit codes pass through unchanged.
        self.assertEqual(self.launcher(SH, old, "--project", self.project, "add", "via the alias")[0], 0)
        self.assertEqual(self.task(1)["prompt"], "via the alias")
        self.assertEqual(self.launcher(SH, old, "--project", self.project, "show", "99")[0], 1)
        # The interpreter can be forced under the current and under the legacy name.
        for variable in ("MOA7AMED_QUEUE_PYTHON", "CTQ_PYTHON"):
            os.environ[variable] = sys.executable
            self.assertEqual(self.launcher(SH, new, "--version")[0], 0, variable)
            del os.environ[variable]

    @unittest.skipUnless(IS_WINDOWS, "Windows batch launchers")
    def test_batch_launchers_on_windows(self):
        new = os.path.join(ROOT, "bin", "moa7amed-queue.cmd")
        old = os.path.join(ROOT, "bin", "ctq.cmd")
        self.assertRegex(self.launcher("cmd", "/c", new, "--version")[1], r"^moa7amed-queue \d")
        self.assertEqual(self.launcher("cmd", "/c", old, "--version"),
                         self.launcher("cmd", "/c", new, "--version"))
        self.assertEqual(self.launcher("cmd", "/c", old, "--project", self.project, "show", "99")[0], 1)

    def test_install_cli_creates_the_command_and_its_aliases(self):
        report = self.mq_json("install-cli")
        folder = report["bin_dir"]
        self.assertEqual(os.path.realpath(folder), os.path.realpath(os.path.join(self.home, "bin")))
        self.assertEqual(report["commands"], ["moa7amed-queue", "ctq", "mq"])
        expected = {"moa7amed-queue", "ctq", "mq"}
        if IS_WINDOWS:
            expected |= {"moa7amed-queue.cmd", "ctq.cmd", "mq.cmd", "moa7amed-queue-run.cmd",
                         "ctq-run.cmd"}
        self.assertEqual(set(os.listdir(folder)), expected)
        if SH:
            for name in ("moa7amed-queue", "ctq", "mq"):
                code, out = self.launcher(SH, os.path.join(folder, name), "--version")
                self.assertRegex(out, r"^moa7amed-queue \d", name)
        if IS_WINDOWS:
            code, out = self.launcher("cmd", "/c", os.path.join(folder, "ctq.cmd"), "--version")
            self.assertRegex(out, r"^moa7amed-queue \d")

    def test_short_alias_never_shadows_another_program(self):
        other = os.path.join(self.tmp, "other-tools")
        os.makedirs(other)
        name = "mq.cmd" if IS_WINDOWS else "mq"
        with open(os.path.join(other, name), "w") as handle:
            handle.write("@echo off\n" if IS_WINDOWS else "#!/bin/sh\n")
        if not IS_WINDOWS:
            os.chmod(os.path.join(other, name), 0o755)
        os.environ["PATH"] = other + os.pathsep + os.environ["PATH"]
        code, out, _ = self.mq("install-cli")
        self.assertIn("Skipped mq: another program already uses that name", out)
        self.assertNotIn("mq", [n.split(".")[0] for n in os.listdir(os.path.join(self.home, "bin"))])
        self.assertIn("ctq", os.listdir(os.path.join(self.home, "bin")))
        shutil.rmtree(os.path.join(self.home, "bin"))
        self.assertEqual(self.mq_json("install-cli", "--no-short")["commands"],
                         ["moa7amed-queue", "ctq"])

    @unittest.skipUnless(SH, "needs a POSIX shell (Git Bash on Windows)")
    def test_shim_created_by_an_earlier_version_still_works(self):
        # What `ctq install-cli` of 0.2.0 wrote: it looks for bin/ctq in the plugin folder.
        folder = os.path.join(self.home, "bin")
        os.makedirs(folder)
        with open(os.path.join(self.home, "plugin-root"), "w", newline="\n") as handle:
            handle.write(ROOT + "\n")
        with open(os.path.join(folder, "ctq"), "w", newline="\n") as handle:
            handle.write('#!/bin/sh\nroot=$(cat "$(dirname "$0")/../plugin-root" 2>/dev/null)\n'
                         'exec sh "$root/bin/ctq" "$@"\n')
        code, out = self.launcher(SH, os.path.join(folder, "ctq"), "--version")
        self.assertRegex(out, r"^moa7amed-queue \d")


if __name__ == "__main__":
    unittest.main()
