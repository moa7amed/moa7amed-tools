"""What a crash leaves behind, and how a person resolves it: the reconcile command, holding
interrupted tasks for review, and budget stops."""
import json
import os
import subprocess
import sys
import time
import unittest

from helpers import QueueTestCase, runner_variants

from mqlib import engine


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class CrashFixture(QueueTestCase):

    def protocol(self, *args, check=True):
        code, out, _ = self.mq(*args, check=check)
        return code, (json.loads(out) if out.strip() else None)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def finished_then_crashed(self, work, result="Done.\nMQ_RESULT: DONE - finished"):
        """A runner starts the task; the task does `work` and reports; the runner dies."""
        _, runner = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        time.sleep(1.1)
        work()
        time.sleep(1.1)
        with open(claim["stdout_file"], "w", encoding="utf-8") as handle:
            json.dump({"type": "result", "subtype": "success", "is_error": False,
                       "result": result, "session_id": "s", "total_cost_usd": 0.05}, handle)
        return claim

    def touch_later(self, relative, text):
        """A change made after the task had reported (by the user, say)."""
        path = self.write(relative, text)
        later = time.time() + 30
        os.utime(path, (later, later))
        return path

    def report(self):
        return self.mq_json("reconcile")


class Reconcile(CrashFixture):

    def test_nothing_to_do_on_a_healthy_queue(self):
        self.add("some work")
        report = self.report()
        self.assertIsNone(report["blocked"])
        self.assertIsNone(report["hold"])
        self.assertEqual(report["actions"], [])
        self.assertIn("nothing: the queue can run", self.mq("reconcile")[1])

    def test_the_report_says_which_files_the_task_changed_and_which_are_unclear(self):
        task_id = self.add("Write the guide.")
        self.add("Another task.")

        def work():
            self.write("docs/guide/intro.md", "intro\n")
            self.write("docs/guide/usage.md", "usage\n")
            with open(os.path.join(self.project, "README.md"), "a") as handle:
                handle.write("see the guide\n")

        self.finished_then_crashed(work)
        self.assertEqual(self.mq("run", check=False)[0], 3)       # recorded, then held
        self.touch_later("notes.txt", "the user's own note, written after the task\n")
        os.utime(os.path.join(self.project, "README.md"), (time.time() + 30,) * 2)
        report = self.report()
        self.assertIn("finished after a crash", report["blocked"])
        owners = {item["path"]: item["owner"] for item in report["hold"]["files"]}
        self.assertEqual(owners["docs/guide/intro.md"], "task")      # file by file, not "docs/"
        self.assertEqual(owners["docs/guide/usage.md"], "task")
        self.assertEqual(owners["README.md"], "later")              # touched again afterwards
        self.assertNotIn("notes.txt", owners)                       # appeared after the crash
        self.assertEqual(report["other_changes"], ["notes.txt"])
        self.assertEqual(report["hold"]["task_id"], task_id)
        text = self.mq("reconcile")[1]
        for expected in ("changed while the task ran", "possibly not the task's",
                         "reconcile --commit --yes", "reconcile --stash --yes",
                         "reconcile --release --yes", "notes.txt"):
            self.assertIn(expected, text)
        self.assertIn("Held    task #%d" % task_id, self.mq("status")[1])

    def test_commit_takes_only_the_tasks_files_and_needs_yes(self):
        task_id = self.add("Write the guide.")
        later = self.add("A later task.")
        self.script(later, "write later.txt done")

        def work():
            self.write("guide.md", "the guide\n")
            self.write("README.md", "# sample project\nsee the guide\n")

        self.finished_then_crashed(work)
        self.assertEqual(self.mq("run", check=False)[0], 3)
        os.utime(os.path.join(self.project, "README.md"), (time.time() + 30,) * 2)
        self.write("unrelated.txt", "work in progress\n")
        self.git("add", "unrelated.txt")                      # even staged, it is not taken
        head = self.git("rev-parse", "HEAD")

        out = self.mq("reconcile", "--commit")[1]
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

        result = self.mq_json("reconcile", "--commit", "--yes")
        self.assertTrue(result["done"])
        self.assertEqual(result["committed"], ["guide.md"])
        self.assertEqual(result["still_held"], ["README.md"])
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD").split(), ["guide.md"])
        self.assertIn("Task-Queue-Id: %d" % task_id, self.git("log", "-1", "--format=%B"))
        self.assertEqual(self.task(task_id)["commit_sha"], result["commit"])
        status = self.git("status", "--porcelain")
        self.assertIn("README.md", status)
        self.assertIn("A  unrelated.txt", status)             # still staged, still uncommitted
        # README.md is still held: no task starts on top of it, whatever on_dirty says.
        self.assertEqual(self.mq("run", "--on-dirty", "allow", check=False)[0], 3)
        self.assertEqual(self.starts(), [])
        self.assertTrue(self.mq_json("reconcile", "--release", "--yes")["done"])
        self.assertIsNone(self.report()["hold"])
        self.assertIn("README.md", self.git("status", "--porcelain"))   # left as it was
        self.assertEqual(self.mq("run", "--on-dirty", "allow")[0], 0)
        self.assertEqual([task for task, _ in self.starts()], [later])

    def test_stash_sets_aside_the_tasks_files_and_nothing_else(self):
        task_id = self.add("Write the guide.")
        self.finished_then_crashed(lambda: self.write("guide.md", "the guide\n"))
        self.mq("run", check=False)          # records it; nothing else is queued
        self.touch_later("mine.txt", "mine\n")
        result = self.mq_json("reconcile", "--stash", "--yes")
        self.assertEqual(result["stashed"], ["guide.md"])
        self.assertFalse(os.path.exists(os.path.join(self.project, "guide.md")))
        self.assertTrue(os.path.exists(os.path.join(self.project, "mine.txt")))
        self.assertIn("guide.md", self.git("show", "--name-only", "--format=", "stash@{0}^3"))
        self.assertIsNone(self.report()["hold"])
        self.assertIn("task #%d" % task_id, self.git("stash", "list"))

    def test_nothing_is_resolved_while_a_runner_is_working(self):
        self.add("Write the guide.")
        self.finished_then_crashed(lambda: self.write("guide.md", "x\n"))
        self.mq("run", check=False)
        engine.acquire(self.queue(), os.getpid(), None, kind="test")
        code, _, err = self.mq("reconcile", "--commit", "--yes", check=False)
        self.assertEqual(code, 2)
        self.assertIn("another runner", err)
        self.assertIn("guide.md", self.git("status", "--porcelain"))

    def test_options_are_exclusive_and_a_missing_hold_is_said_plainly(self):
        self.add("some work")
        code, _, err = self.mq("reconcile", "--commit", "--stash", "--yes", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("choose one", err)
        code, out, _ = self.mq("reconcile", "--release", "--yes", check=False)
        self.assertEqual(code, 1)
        self.assertIn("nothing is held", out)

    def test_reconcile_also_records_a_run_a_dead_runner_left_open(self):
        task_id = self.add("Write the guide.")
        self.finished_then_crashed(lambda: self.write("guide.md", "the guide\n"))
        report = self.report()
        self.assertEqual(report["open_runs"][0]["finished_by_claude"], True)
        self.assertIn("next runner", report["blocked"])
        result = self.mq_json("reconcile", "--commit", "--yes")
        self.assertEqual(result["committed"], ["guide.md"])
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.starts(), [])


class HoldInterrupted(CrashFixture):

    def cut_off(self, work=None):
        _, runner = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        if work:
            work()
        return claim

    def test_by_default_a_cut_off_task_runs_again(self):
        task_id = self.add("Post the release note to the team channel.")
        self.cut_off()
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual([run["outcome"] for run in self.task(task_id)["runs"]],
                         ["interrupted", "done"])

    def test_hold_keeps_a_cut_off_task_and_its_dependents_for_review(self):
        self.set_config(on_interrupted="hold")
        task_id = self.add("Post the release note to the team channel.")
        after = self.add("Archive the release branch.", after=[task_id])
        self.cut_off()
        code, out, err = self.mq("run", check=False)
        self.assertEqual(code, 1, out + err)
        self.assertEqual(self.starts(), [])
        task = self.task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("Held for review", task["last_error"])
        self.assertEqual(self.task(after)["status"], "pending")
        report = self.report()
        self.assertEqual([item["task_id"] for item in report["held_tasks"]], [task_id])
        self.assertIn("moa7amed-queue retry %d" % task_id, "\n".join(report["actions"]))
        self.assertIn("wait for review", report["blocked"])
        # A person decides it is safe: it runs once more, and the dependent after it.
        self.mq("retry", str(task_id))
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual([task for task, _ in self.starts()], [task_id, after])
        self.assertEqual(self.report()["held_tasks"], [])

    def test_hold_does_not_apply_to_a_task_that_had_finished(self):
        self.set_config(on_interrupted="hold")
        task_id = self.add("Write the guide.")
        self.finished_then_crashed(lambda: self.write("guide.md", "x\n"))
        self.mq("run", check=False)
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.report()["held_tasks"], [])


class Budgets(QueueTestCase):

    def budgets_given(self):
        values = []
        for event in self.events_of("start"):
            argv = event["argv"]
            values.append(float(argv[argv.index("--max-budget-usd") + 1])
                          if "--max-budget-usd" in argv else None)
        return values

    def test_task_stopped_by_its_budget_is_not_retried(self):
        task_id = self.add("A big job.", max_attempts=3)
        self.script(task_id, "budget")
        self.set_config(max_budget_usd=0.5)
        code, out, err = self.mq("run", check=False)
        self.assertEqual(code, 1, out + err)
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("failed", 1))
        self.assertIn("reached its budget", task["last_error"])
        self.assertIn("Reached maximum budget", task["last_error"])
        self.assertEqual(len(self.starts()), 1)
        # Its cost is recorded as reported, above the cap: the cap is not exact.
        self.assertAlmostEqual(task["runs"][0]["cost_usd"], 0.685)

    def test_each_task_gets_at_most_what_the_session_has_left(self):
        for index in range(3):
            self.add("task %d" % index)
        self.set_config(cost_limit_run_usd=0.02, max_budget_usd=1.0)
        code, out, err = self.mq("run", check=False)
        self.assertEqual(code, engine.EXIT_COST_LIMIT, out + err)
        given = self.budgets_given()
        self.assertEqual(len(given), 2)
        self.assertEqual(given[0], 0.02)
        self.assertAlmostEqual(given[1], 0.02 - 0.0123, places=4)
        self.assertIn("what is left of cost_limit_run_usd", out + err)

    def test_the_per_task_budget_applies_when_it_is_the_smaller_one(self):
        self.add("one")
        self.set_config(cost_limit_run_usd=5, max_budget_usd=0.2)
        self.mq("run")
        self.assertEqual(self.budgets_given(), [0.2])

    def test_without_limits_no_budget_is_passed(self):
        self.add("one")
        self.mq("run")
        self.assertEqual(self.budgets_given(), [None])

    def test_budget_stop_found_after_a_crash_is_recorded_not_run_again(self):
        task_id = self.add("A big job.")
        _, runner = self.mq_json_protocol("runner-acquire", "--pid", str(dead_pid()))
        _, claim = self.mq_json_protocol("claim", "--runner-id", runner["runner_id"])
        with open(claim["stdout_file"], "w") as handle:
            json.dump({"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                       "total_cost_usd": 0.0137, "errors": ["Reached maximum budget ($0.01)"]},
                      handle)
        self.mq("run", check=False)
        task = self.task(task_id)
        self.assertEqual([run["outcome"] for run in task["runs"]], ["failed"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(self.starts(), [])

    def mq_json_protocol(self, *args):
        code, out, _ = self.mq(*(list(args) + ["--kind", "test"] if args[0] == "runner-acquire"
                                 else list(args)))
        return code, json.loads(out)


class BudgetWithRunners(object):

    def test_runner_logs_the_budget_it_gives(self):
        self.add("one")
        self.add("two")
        self.set_config(cost_limit_run_usd=0.05)
        code, out = self.run_queue()
        self.assertEqual(code, 0, out)
        self.assertIn("[budget $0.05: what is left of cost_limit_run_usd]", out)


globals().update(runner_variants(BudgetWithRunners))


if __name__ == "__main__":
    unittest.main()
