"""Queue integrity: what is stored, in which order it runs, and what can never change."""
import json
import os
import sqlite3
import subprocess
import sys
import unittest

from helpers import MQ, QueueTestCase

PROMPT = (
    "Refactor the parser so it handles \"quoted\" values, $VARS, 100% of `backticks` & <tags>.\n"
    "\n"
    "    keep this indentation\n"
    "Unicode must survive: café, مرحبا, 日本語, emoji \U0001F680\n"
    "\n"
    "Acceptance criteria:\n"
    "- all parser tests pass\n"
    "- no public function is renamed"
)


class StoredTasks(QueueTestCase):

    def test_prompt_and_acceptance_are_preserved_exactly(self):
        code, out, _ = self.mq("add", "--stdin", "--priority", "high",
                                stdin=PROMPT.encode("utf-8"))
        self.assertIn("Queued task #1 [high]", out)
        task = self.mq_json("show", "1")
        self.assertEqual(task["prompt"], PROMPT)
        self.assertEqual(task["acceptance"],
                         "- all parser tests pass\n- no public function is renamed")
        self.assertEqual(task["state"], "pending")
        self.assertEqual(task["source"], "cli")

    def test_prompt_file_and_separate_acceptance_file(self):
        prompt_file = self.write("../prompt.txt", "Add a health endpoint.\nReturn JSON.\n")
        criteria_file = self.write("../criteria.txt", "GET /health answers 200\n")
        self.mq("add", "--prompt-file", prompt_file, "--acceptance-file", criteria_file,
                 "--title", "Health endpoint")
        task = self.task(1)
        self.assertEqual(task["prompt"], "Add a health endpoint.\nReturn JSON.")
        self.assertEqual(task["acceptance"], "GET /health answers 200")
        self.assertEqual(task["title"], "Health endpoint")

    def test_prompt_given_as_arguments(self):
        self.mq("add", "Fix", "the", "typo", "--acceptance", "README has no typo")
        task = self.task(1)
        self.assertEqual(task["prompt"], "Fix the typo")
        self.assertEqual(task["acceptance"], "README has no typo")

    def test_empty_prompt_is_rejected(self):
        code, _, err = self.mq("add", "--stdin", stdin=b"  \n\n", check=False)
        self.assertEqual(code, 1)
        self.assertIn("empty prompt", err)
        self.assertEqual(self.queue().tasks(), [])

    def test_original_prompt_cannot_be_rewritten_or_deleted(self):
        q = self.queue()
        task_id = q.add("Original request", acceptance="Original criteria")
        for statement in ("UPDATE tasks SET prompt='tampered' WHERE id=?",
                          "UPDATE tasks SET acceptance='tampered' WHERE id=?",
                          "DELETE FROM tasks WHERE id=?"):
            with self.assertRaises(sqlite3.DatabaseError):
                q.conn.execute(statement, (task_id,))
        # Normal lifecycle changes leave the original untouched.
        q.set_priority([task_id], 20)
        q.cancel([task_id])
        q.retry([task_id])
        task = q.get(task_id)
        self.assertEqual((task["prompt"], task["acceptance"]),
                         ("Original request", "Original criteria"))

    def test_history_records_every_change(self):
        q = self.queue()
        task_id = q.add("Track me")
        q.set_priority([task_id], 10)
        q.cancel([task_id])
        q.retry([task_id])
        kinds = [event["type"] for event in q.events(task_id)]
        self.assertEqual(kinds, ["added", "priority", "cancelled", "retry"])


class Ordering(QueueTestCase):

    def order(self):
        return [task["id"] for task in self.queue().execution_order()]

    def test_higher_priority_first_then_first_come_first_served(self):
        q = self.queue()
        q.add("normal one")                 # 1
        q.add("low", priority=-10)          # 2
        q.add("urgent", priority=20)        # 3
        q.add("normal two")                 # 4
        q.add("high", priority=10)          # 5
        self.assertEqual(self.order(), [3, 5, 1, 4, 2])

    def test_changing_priority_reorders_the_queue(self):
        q = self.queue()
        for name in ("a", "b", "c"):
            q.add(name)
        self.mq("priority", "3", "urgent")
        self.assertEqual(self.order(), [3, 1, 2])
        self.mq("priority", "1,2", "-5")
        self.assertEqual(self.order(), [3, 1, 2])
        self.mq("priority", "2", "low")
        self.assertEqual(self.order(), [3, 1, 2])
        code, _, err = self.mq("priority", "1", "sometime", check=False)
        self.assertEqual(code, 1)
        self.assertIn("invalid priority", err)

    def test_task_waits_for_its_prerequisites(self):
        q = self.queue()
        first = q.add("build the schema")
        second = q.add("migrate the data", after=[first], priority=20)
        self.assertEqual(self.order(), [first])  # urgent, but its prerequisite is not done
        self.assertEqual(q.state_of(q.get(second)), "waiting")

    def test_dependents_are_blocked_when_a_prerequisite_is_cancelled(self):
        q = self.queue()
        first = q.add("first")
        second = q.add("second", after=[first])
        q.cancel([first])
        self.assertEqual(q.state_of(q.get(second)), "blocked")
        self.assertEqual(self.order(), [])
        q.retry([first])
        self.assertEqual(q.state_of(q.get(second)), "waiting")

    def test_dependency_cycles_are_rejected(self):
        q = self.queue()
        a, b, c = q.add("a"), q.add("b"), q.add("c")
        q.add_deps(b, [a])
        q.add_deps(c, [b])
        code, _, err = self.mq("depend", str(a), "--on", str(c), check=False)
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)
        code, _, err = self.mq("depend", str(a), "--on", str(a), check=False)
        self.assertEqual(code, 1)
        self.assertEqual([dep["id"] for dep in self.queue().deps(a)], [])

    def test_unknown_prerequisite_is_rejected_and_nothing_is_stored(self):
        code, _, err = self.mq("add", "orphan", "--after", "99", check=False)
        self.assertEqual(code, 1)
        self.assertIn("#99 does not exist", err)
        self.assertEqual(self.queue().tasks(), [])

    def test_only_failed_or_cancelled_tasks_can_be_retried(self):
        q = self.queue()
        task_id = q.add("still pending")
        code, _, err = self.mq("retry", str(task_id), check=False)
        self.assertEqual(code, 1)
        self.assertIn("only failed or cancelled", err)


class Concurrency(QueueTestCase):

    def test_simultaneous_adds_from_many_processes_lose_nothing(self):
        writers = 12
        processes = [
            subprocess.Popen([sys.executable, MQ, "--project", self.project, "add",
                              "task from writer %d" % index],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for index in range(writers)]
        for process in processes:
            _, err = process.communicate(timeout=180)
            self.assertEqual(process.returncode, 0, err.decode("utf-8", errors="replace"))
        q = self.queue()
        tasks = q.tasks()
        self.assertEqual(sorted(task["id"] for task in tasks), list(range(1, writers + 1)))
        self.assertEqual(sorted(task["prompt"] for task in tasks),
                         sorted("task from writer %d" % index for index in range(writers)))
        self.assertEqual(q.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_database_stays_consistent_when_a_writer_is_killed(self):
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from mqlib import store, util\n"
            "q = store.Queue(util.resolve_project_root(%r))\n"
            "print('ready', flush=True)\n"
            "i = 0\n"
            "while True:\n"
            "    i += 1\n"
            "    q.add('bulk task %%d' %% i, after=[1] if i > 1 else None)\n"
        ) % (os.path.dirname(MQ), self.project)
        process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
        self.assertEqual(process.stdout.readline().strip(), b"ready")
        self.wait_for(lambda: len(self.queue().tasks()) >= 25, message="some tasks to be written")
        process.kill()
        process.wait()
        process.stdout.close()
        q = self.queue()
        self.assertEqual(q.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        tasks = q.tasks()
        self.assertEqual([task["id"] for task in tasks], list(range(1, len(tasks) + 1)))
        # Every task was written together with its dependency and its history entry, or not at all.
        for task in tasks[1:]:
            self.assertEqual([dep["id"] for dep in q.deps(task["id"])], [1])
            self.assertEqual(len(q.events(task["id"])), 1)
        q.add("still writable after the crash")


class ProjectIsolation(QueueTestCase):

    def test_each_project_has_its_own_queue(self):
        other = os.path.join(self.tmp, "other-project")
        os.makedirs(other)
        self.init_git(other)
        self.mq("add", "task for the first project")
        self.mq("add", "task for the second project", project=other)
        self.mq("add", "another for the second project", project=other)
        self.assertEqual([t["prompt"] for t in self.queue().tasks()],
                         ["task for the first project"])
        self.assertEqual([t["prompt"] for t in self.queue(other).tasks()],
                         ["task for the second project", "another for the second project"])
        roots = {entry["root"] for entry in self.mq_json("projects")}
        self.assertEqual(roots, {os.path.realpath(self.project), os.path.realpath(other)})

    def test_subdirectories_of_a_repository_share_its_queue(self):
        nested = os.path.join(self.project, "src", "deep")
        os.makedirs(nested)
        self.mq("add", "queued from a subdirectory", project=nested)
        self.assertEqual([t["prompt"] for t in self.queue().tasks()],
                         ["queued from a subdirectory"])

    def test_nothing_is_written_inside_the_project(self):
        self.mq("add", "leave my working tree alone")
        self.mq("status")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(sorted(os.listdir(self.project)), [".git", "README.md"])


class Settings(QueueTestCase):

    def test_project_settings_override_global_ones(self):
        self.mq("config", "set", "max_attempts", "5", "--global")
        self.mq("config", "set", "max_attempts", "3")
        self.mq("config", "set", "allowed_tools", "Read,Bash(git log *)")
        config = self.mq_json("config")
        self.assertEqual(config["max_attempts"], 3)
        self.assertEqual(config["allowed_tools"], ["Read", "Bash(git log *)"])
        self.mq("config", "unset", "max_attempts")
        self.assertEqual(self.mq_json("config")["max_attempts"], 5)
        self.assertEqual(self.task(self.add("uses the default"))["max_attempts"], 5)

    def test_capture_prefix_is_a_global_setting(self):
        code, _, err = self.mq("config", "set", "capture_prefix", "todo:", check=False)
        self.assertEqual(code, 1)
        self.assertIn("--global", err)
        self.mq("config", "set", "capture_prefix", "todo:", "--global")
        self.assertEqual(self.mq_json("config")["capture_prefix"], "todo:")

    def test_invalid_settings_are_rejected(self):
        for key, value in (("on_dirty", "discard"), ("nonsense", "1"), ("max_attempts", "-2")):
            code, _, err = self.mq("config", "set", key, value, check=False)
            self.assertEqual(code, 1, key)
        plan = self.mq_json("plan", "--model", "sonnet", "--allowed-tools", "Read,Edit")
        self.assertEqual(plan["command"][-5:], ["--model", "sonnet", "--allowedTools", "Read", "Edit"])


class SettingsWithDashes(QueueTestCase):
    """Extra arguments for `claude` begin with dashes, and so may other values."""

    def test_value_that_begins_with_a_dash_can_be_set(self):
        self.mq("config", "set", "claude_args", "--safe-mode", "--global")
        self.assertEqual(self.mq_json("config", "show")["claude_args"], ["--safe-mode"])
        self.mq("config", "set", "claude_args", "--safe-mode", "--strict-mcp-config")
        self.assertEqual(self.mq_json("config", "show")["claude_args"],
                         ["--safe-mode", "--strict-mcp-config"])
        self.mq("config", "set", "claude_args", "--safe-mode,--verbose")
        self.assertEqual(self.mq_json("config", "show")["claude_args"], ["--safe-mode", "--verbose"])
        self.mq("config", "unset", "claude_args")
        self.assertEqual(self.mq_json("config", "show")["claude_args"], ["--safe-mode"])

    def test_the_arguments_reach_claude(self):
        self.mq("config", "set", "claude_args", "--safe-mode")
        self.add("some work")
        self.mq("run")
        self.assertIn("--safe-mode", self.events_of("start")[0]["argv"])

    def test_arguments_for_claude_keep_their_order_and_their_values(self):
        self.mq("config", "set", "claude_args", "--append-system-prompt", "be brief", "--verbose")
        self.assertEqual(self.mq_json("config", "show")["claude_args"],
                         ["--append-system-prompt", "be brief", "--verbose"])
        # Options of this command are still recognised around them, written in full.
        self.mq("config", "set", "claude_args", "--pro", "zzz", "--global")
        with open(os.path.join(self.home, "config.json")) as handle:
            self.assertEqual(json.load(handle)["claude_args"], ["--pro", "zzz"])

    def test_unknown_arguments_are_still_refused_everywhere_else(self):
        for command in (["status", "--no-such-option"], ["add", "a task", "--bogus"],
                        ["config", "show", "--bogus"], ["config", "unset", "model", "--bogus"],
                        ["config", "set", "model", "opus", "--globl"],
                        ["config", "set", "allowed_tools", "Read", "--globl"]):
            code, _, err = self.mq(*command, check=False)
            self.assertEqual(code, 2, command)
            self.assertIn("unrecognized arguments", err, command)
        self.assertEqual(self.queue().tasks(), [])
        self.assertIsNone(self.mq_json("config", "show")["model"])
        self.assertEqual(self.mq_json("config", "show")["allowed_tools"], [])


if __name__ == "__main__":
    unittest.main()
