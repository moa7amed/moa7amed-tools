"""Interruption recovery: killed runners, stale locks and half-finished tasks."""
import json
import os
import subprocess
import sys
import unittest

from helpers import QueueTestCase, kill_tree, runner_variants


def dead_pid():
    """The id of a process that has already exited."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class LockProtocol(QueueTestCase):
    """The lock and recovery rules, driven through the commands the runners use."""

    def protocol(self, *args, check=True):
        code, out, _ = self.mq(*args, check=check)
        return code, (json.loads(out) if out.strip() else None)

    def acquire(self, pid, *extra, check=True):
        return self.protocol("runner-acquire", "--pid", str(pid), "--kind", "test", *extra,
                             check=check)

    def test_live_runner_keeps_the_project_to_itself(self):
        self.add("FAKE: write a.txt a")
        code, first = self.acquire(os.getpid())
        self.assertEqual(code, 0)
        code, refused = self.acquire(os.getpid(), check=False)
        self.assertEqual(code, 2)
        self.assertIn("another runner is already working", refused["error"])
        code, _, err = self.mq("recover", check=False)
        self.assertEqual(code, 2)
        self.assertIn("another runner is already working", err)
        status = self.mq_json("status")
        self.assertTrue(status["runner"]["alive"])
        self.assertEqual(status["runner"]["runner_id"], first["runner_id"])
        # Releasing hands the project back.
        self.protocol("runner-release", "--runner-id", first["runner_id"])
        self.assertIsNone(self.mq_json("status")["runner"])
        self.assertEqual(self.acquire(os.getpid())[0], 0)

    def test_lock_of_a_dead_runner_is_taken_over_and_its_task_requeued(self):
        task_id = self.add("FAKE: write a.txt a")
        _, crashed = self.acquire(dead_pid())
        _, claim = self.protocol("claim", "--runner-id", crashed["runner_id"])
        self.assertEqual((claim["action"], claim["task_id"]), ("run", task_id))
        self.assertEqual(self.task(task_id)["status"], "running")
        self.assertFalse(self.mq_json("status")["runner"]["alive"])
        self.assertIn("STALE", self.mq("status")[1])

        code, successor = self.acquire(os.getpid())
        self.assertEqual(code, 0)
        self.assertEqual([(item["task_id"], item["run_id"], item["status"], item["reconciled"])
                          for item in successor["recovered"]],
                         [(task_id, claim["run_id"], "pending", False)])
        task = self.task(task_id)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["runs"][0]["outcome"], "interrupted")
        self.assertIsNotNone(task["runs"][0]["finished_at"])
        # The crashed runner, should it still be around, can no longer touch the queue.
        for command in (["claim"], ["finish", "--run-id", str(claim["run_id"]), "--exit-code", "0"],
                        ["heartbeat"]):
            code, reply = self.protocol(*(command + ["--runner-id", crashed["runner_id"]]),
                                        check=False)
            self.assertEqual(code, 4, command)
            self.assertIn("no longer holds the lock", reply["error"])
        # The successor runs the task as attempt 2.
        _, again = self.protocol("claim", "--runner-id", successor["runner_id"])
        self.assertEqual((again["task_id"], again["attempt"]), (task_id, 2))

    def test_interrupted_task_without_attempts_left_is_marked_failed(self):
        task_id = self.add("FAKE: write a.txt a", max_attempts=1)
        _, crashed = self.acquire(dead_pid())
        self.protocol("claim", "--runner-id", crashed["runner_id"])
        _, successor = self.acquire(os.getpid())
        self.assertEqual(successor["recovered"][0]["status"], "failed")
        task = self.task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertIn("stopped before the task finished", task["last_error"])
        _, idle = self.protocol("claim", "--runner-id", successor["runner_id"])
        self.assertEqual((idle["action"], idle["reason"]), ("idle", "empty"))

    def test_recover_command_clears_a_stale_lock(self):
        task_id = self.add("FAKE: write a.txt a")
        _, crashed = self.acquire(dead_pid())
        self.protocol("claim", "--runner-id", crashed["runner_id"])
        code, out, _ = self.mq("recover")
        self.assertIn("Removed the runner lock", out)
        self.assertIn("Task #%d was interrupted -> pending" % task_id, out)
        self.assertIsNone(self.mq_json("status")["runner"])
        self.assertEqual(self.task(task_id)["status"], "pending")

    def test_recovery_waits_for_a_task_process_that_is_still_alive(self):
        task_id = self.add("FAKE: write a.txt a")
        _, crashed = self.acquire(dead_pid())
        _, claim = self.protocol("claim", "--runner-id", crashed["runner_id"])
        # The runner is gone, but the Claude process it started is still running.
        self.protocol("heartbeat", "--runner-id", crashed["runner_id"],
                      "--run-id", str(claim["run_id"]), "--child-pid", str(os.getpid()))
        code, refused = self.acquire(os.getpid(), check=False)
        self.assertEqual(code, 2)
        self.assertIn("still appears to be running", refused["error"])
        self.assertEqual(self.task(task_id)["status"], "running")  # nothing was changed
        code, forced = self.acquire(os.getpid(), "--force")
        self.assertEqual(code, 0)
        self.assertEqual(forced["recovered"][0]["task_id"], task_id)

    def test_stopping_the_runner_requeues_the_task_and_keeps_its_changes(self):
        # What a runner does on Ctrl+C: report the run as interrupted, then release.
        task_id = self.add("FAKE: write result.txt final")
        _, runner = self.acquire(os.getpid())
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        self.write("result.txt", "partial work\n")
        _, result = self.protocol("finish", "--runner-id", runner["runner_id"],
                                  "--run-id", str(claim["run_id"]), "--interrupted")
        self.assertEqual((result["outcome"], result["status"]), ("interrupted", "pending"))
        task = self.task(task_id)
        self.assertEqual(task["attempts"], 0)  # stopping on purpose does not use up an attempt
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertIn("partial work", self.git("show", "stash@{0}^3:result.txt"))
        self.assertEqual(result["stash"], self.git("rev-parse", "refs/stash"))
        self.protocol("runner-release", "--runner-id", runner["runner_id"])
        self.assertIsNone(self.mq_json("status")["runner"])

    def test_runner_refuses_to_start_during_an_unfinished_git_operation(self):
        task_id = self.add("FAKE: write result.txt final")
        merge_head = os.path.join(self.project, ".git", "MERGE_HEAD")
        with open(merge_head, "w") as handle:
            handle.write(self.git("rev-parse", "HEAD") + "\n")
        code, out, _ = self.mq("run", check=False)
        self.assertEqual(code, 3, out)
        self.assertIn("in the middle of a git operation", out)
        self.assertEqual(self.task(task_id)["status"], "pending")
        self.assertEqual(self.fake_events(), [])
        os.remove(merge_head)
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual(self.task(task_id)["status"], "done")

    def test_changes_left_by_an_interrupted_task_are_never_overwritten(self):
        task_id = self.add("FAKE: write result.txt final")
        _, crashed = self.acquire(dead_pid())
        self.protocol("claim", "--runner-id", crashed["runner_id"])
        self.write("result.txt", "half-written by the interrupted task\n")

        code, out, _ = self.mq("run", check=False)
        self.assertEqual(code, 3, out)
        self.assertIn("recovered interrupted task #%d" % task_id, out)
        self.assertIn("uncommitted changes", out)
        self.assertEqual(self.read("result.txt"), "half-written by the interrupted task\n")
        self.assertEqual(self.task(task_id)["status"], "pending")

        code, out, _ = self.mq("run", "--on-dirty", "stash")
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.read("result.txt"), "final\n")
        self.assertIn("half-written", self.git("show", "stash@{0}^3:result.txt"))


class KilledRunner(object):
    """A real runner process is killed in the middle of a task."""

    def test_killed_runner_is_recovered_by_the_next_run(self):
        slow = self.add("FAKE: sleep-once 300\nFAKE: write slow.txt finished on the second attempt")
        other = self.add("FAKE: write other.txt ok")
        runner = self.start_queue()

        def child_pid():
            runs = self.task(slow)["runs"]
            return runs[0]["child_pid"] if runs and runs[0]["child_pid"] else None

        claude_pid = self.wait_for(child_pid, message="the task to start")
        runner.kill()  # the runner dies; the Claude process it started keeps running
        runner.wait()
        self.assertEqual(self.task(slow)["status"], "running")
        self.assertFalse(self.mq_json("status")["runner"]["alive"])

        # While the orphaned Claude process is alive, nobody else may use the workspace.
        code, output = self.run_queue()
        self.assertEqual(code, 2, output)
        self.assertIn("still appears to be running", output)

        kill_tree(claude_pid)
        self.wait_for(lambda: not self._alive(claude_pid), message="the orphan to exit")
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        self.assertIn("recovered interrupted task #%d" % slow, output)
        task = self.task(slow)
        self.assertEqual(task["status"], "done")
        self.assertEqual([run["outcome"] for run in task["runs"]], ["interrupted", "done"])
        self.assertEqual(self.task(other)["status"], "done")
        self.assertEqual(self.read("slow.txt"), "finished on the second attempt\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        history = [event["type"] for event in self.queue().events(slow)]
        self.assertEqual(history, ["added", "started", "interrupted", "started", "done"])

    @staticmethod
    def _alive(pid):
        from mqlib import util
        return util.pid_alive(pid)


globals().update(runner_variants(KilledRunner))


class Reconciliation(QueueTestCase):
    """The runner can die at the worst moment: after Claude finished but before the result
    was recorded. Work that was completed must not be done a second time."""

    def protocol(self, *args, check=True):
        code, out, _ = self.mq(*args, check=check)
        return code, (json.loads(out) if out.strip() else None)

    def write_result(self, claim, result_text, is_error=False, seconds_ago=0):
        with open(claim["stdout_file"], "w", encoding="utf-8") as handle:
            json.dump({"type": "result", "subtype": "success", "is_error": is_error,
                       "result": result_text, "session_id": "s", "total_cost_usd": 0.05,
                       "modelUsage": {"claude-sonnet-test-1": {"outputTokens": 9}}}, handle)
        if seconds_ago:
            stamp = os.path.getmtime(claim["stdout_file"]) - seconds_ago
            os.utime(claim["stdout_file"], (stamp, stamp))

    def crashed_after_claude_finished(self, result_text, is_error=False, work=None,
                                      seconds_ago=0):
        """A runner claims the task, Claude does its work and writes its complete result,
        the runner dies. `work` is what the task did before it reported."""
        _, runner = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        if work:
            work()
        self.write_result(claim, result_text, is_error, seconds_ago)
        return claim

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_finished_task_is_not_executed_again_after_a_runner_crash(self):
        task_id = self.add("Add a search box to the customers page.")
        other = self.add("Fix the typo in the README title.")
        self.script(other, "write README.md fixed")
        self.crashed_after_claude_finished("Done.\nMQ_RESULT: DONE - search box added")
        self.write("search.txt", "the finished work\n")  # what the task had already written

        code, out, _ = self.mq("run", check=False)
        # The finished task is recognised as finished; Claude is never started for it again.
        self.assertNotIn(task_id, [task for task, _ in self.starts()])
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("done", 1))
        self.assertEqual(task["result_summary"], "search box added")
        self.assertEqual([run["outcome"] for run in task["runs"]], ["done"])
        self.assertEqual(task["runs"][0]["files_changed"], ["search.txt"])
        self.assertEqual(task["routing"]["actualModel"], "claude-sonnet-test-1")
        self.assertIn("reconciled", [event["type"] for event in self.queue().events(task_id)])
        # Its changes are still exactly where it left them: nothing was committed, stashed or
        # removed on its behalf after the crash, and the runner stops before the next task.
        self.assertEqual(self.read("search.txt"), "the finished work\n")
        self.assertEqual(self.git("stash", "list"), "")
        self.assertEqual(len(self.git("log", "--format=%s").splitlines()), 1)
        self.assertEqual(code, 3, out)
        self.assertIn("finished before the runner stopped", out)
        self.assertEqual(self.task(other)["status"], "pending")
        # Once the user has committed the work, the queue continues.
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "search box")
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual(self.starts(), [(other, "haiku")])

    def test_changes_to_existing_files_are_named_correctly_after_a_crash(self):
        task_id = self.add("Add a line to the README.")
        self.crashed_after_claude_finished("Done.\nMQ_RESULT: DONE - line added")
        with open(os.path.join(self.project, "README.md"), "a") as handle:
            handle.write("added by the task\n")
        code, out, _ = self.mq("run", check=False)
        self.assertEqual(self.task(task_id)["runs"][0]["files_changed"], ["README.md"])
        self.assertIn("commit them (README.md)", out)

    def test_finished_task_that_was_already_committed_is_recorded_with_its_commit(self):
        task_id = self.add("Add a search box to the customers page.")

        def work():   # the task committed its own work before it reported
            self.write("search.txt", "x\n")
            self.commit("done by the task itself")

        self.crashed_after_claude_finished("MQ_RESULT: DONE - committed it", work=work)
        self.assertEqual(self.mq("run")[0], 0)
        task = self.task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["commit_sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual(task["runs"][0]["files_changed"], ["search.txt"])
        self.assertEqual(self.starts(), [])

    def test_commit_the_user_made_after_the_crash_is_not_taken_for_the_tasks(self):
        task_id = self.add("Add a search box to the customers page.")
        self.crashed_after_claude_finished("MQ_RESULT: DONE - nothing to change", seconds_ago=120)
        self.write("mine.txt", "the user's own work, committed while the queue was down\n")
        self.commit("my own commit")
        self.assertEqual(self.mq("run")[0], 0)
        task = self.task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["commit_sha"])
        self.assertEqual(task["runs"][0]["files_changed"], [])

    def test_commit_the_runner_made_just_before_it_died_is_recognised(self):
        # The runner had committed the task's work and died before recording the result.
        task_id = self.add("Add a search box to the customers page.")
        self.crashed_after_claude_finished("MQ_RESULT: DONE - search box added", seconds_ago=120)
        self.write("search.txt", "x\n")
        sha = self.commit("Add a search box (task #%d)\n\nTask-Queue-Id: %d" % (task_id, task_id))
        self.write("mine.txt", "and then the user committed something\n")
        self.commit("my own commit")
        self.assertEqual(self.mq("run")[0], 0)
        task = self.task(task_id)
        self.assertEqual((task["status"], task["commit_sha"]), ("done", sha))
        self.assertEqual(task["runs"][0]["files_changed"], ["search.txt"])
        self.assertEqual(self.starts(), [])

    def test_runner_that_stops_after_claude_finished_does_not_requeue_the_task(self):
        # Ctrl+C, or any error, while the result is being recorded: the runner lets go of
        # the project. The task finished, so it must not go back in line.
        task_id = self.add("Add a search box to the customers page.")
        _, runner = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        self.write("search.txt", "the finished work\n")
        self.write_result(claim, "Done.\nMQ_RESULT: DONE - search box added")
        self.protocol("runner-release", "--runner-id", runner["runner_id"])
        task = self.task(task_id)
        self.assertNotEqual(task["status"], "pending")
        self.assertEqual(task["attempts"], 1)
        code, out, _ = self.mq("run", check=False)
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("done", 1))
        self.assertEqual([run["outcome"] for run in task["runs"]], ["done"])
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.read("search.txt"), "the finished work\n")

    def test_runner_interrupted_at_the_moment_the_task_finished_keeps_the_result(self):
        task_id = self.add("Add a search box to the customers page.")
        _, runner = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        self.write("search.txt", "the finished work\n")
        self.write_result(claim, "Done.\nMQ_VERIFIED: ran the page tests, all pass\n"
                                 "MQ_RESULT: DONE - search box added")
        _, result = self.protocol("finish", "--runner-id", runner["runner_id"],
                                  "--run-id", str(claim["run_id"]), "--interrupted")
        self.assertEqual((result["outcome"], result["status"]), ("done", "done"))
        self.assertEqual(self.git("stash", "list"), "")          # the work was not set aside
        self.assertEqual(self.git("status", "--porcelain"), "")   # it was committed
        self.assertIn("Task-Queue-Id: %d" % task_id, self.git("log", "-1", "--format=%b"))
        self.assertEqual(self.task(task_id)["attempts"], 1)

    def files_of_a_finished_task_survive(self, policy):
        task_id = self.add("Add a search box to the customers page.")
        other = self.add("Fix the typo in the README title.")
        self.script(other, "write README.md fixed")
        self.crashed_after_claude_finished("Done.\nMQ_RESULT: DONE - search box added")
        self.write("search.txt", "the finished work\n")
        code, out, err = self.mq("run", "--on-dirty", policy, check=False)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.task(other)["status"], "pending")
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.read("search.txt"), "the finished work\n")
        self.assertEqual(self.git("stash", "list"), "")
        self.assertIn("never committed", out + err)
        # Asking again changes nothing; committing the work lets the queue go on.
        self.assertEqual(self.mq("run", "--on-dirty", policy, check=False)[0], 3)
        self.assertEqual(self.git("stash", "list"), "")
        self.commit("search box")
        self.assertEqual(self.mq("run", "--on-dirty", policy, check=False)[0], 0)
        self.assertEqual(self.task(other)["status"], "done")

    def test_stash_policy_does_not_set_aside_the_files_of_a_task_found_finished(self):
        self.files_of_a_finished_task_survive("stash")

    def test_allow_policy_does_not_build_on_the_files_of_a_task_found_finished(self):
        self.files_of_a_finished_task_survive("allow")

    def test_later_stops_do_not_blame_a_task_that_was_dealt_with_long_ago(self):
        task_id = self.add("Add a search box to the customers page.")
        other = self.add("Fix the typo in the README title.")
        self.script(other, "write README.md fixed")
        self.crashed_after_claude_finished("Done.\nMQ_RESULT: DONE - search box added")
        self.write("search.txt", "the finished work\n")
        self.assertEqual(self.mq("run", check=False)[0], 3)
        self.commit("search box")
        self.write("unrelated.txt", "the user's own work in progress\n")
        code, out, err = self.mq("run", check=False)
        self.assertEqual(code, 3)
        self.assertIn("uncommitted changes", out + err)
        self.assertNotIn("task #%d finished before" % task_id, (out + err).lower())
        self.assertNotIn("never committed", out + err)

    def test_result_file_of_an_earlier_run_with_the_same_number_is_not_this_runs_result(self):
        # After a restore, run numbers start again from an earlier point while the output
        # files of the runs that happened since are still on disk.
        task_id = self.add("Add a search box to the customers page.")
        self.script(task_id, "write search.txt really done")
        stale = os.path.join(self.queue().dir, "runs", "task-%04d" % task_id,
                             "run-000001.stdout.json")
        os.makedirs(os.path.dirname(stale))
        with open(stale, "w") as handle:
            json.dump({"type": "result", "subtype": "success", "is_error": False,
                       "result": "MQ_RESULT: DONE - an older run"}, handle)
        _, runner = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        self.protocol("claim", "--runner-id", runner["runner_id"])   # dies before starting claude
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual([run["outcome"] for run in self.task(task_id)["runs"]],
                         ["interrupted", "done"])
        self.assertEqual(self.read("search.txt"), "really done\n")

    def test_failed_result_found_after_a_crash_counts_as_a_failed_attempt(self):
        task_id = self.add("Add a search box to the customers page.")
        self.script(task_id, "write search.txt second attempt")
        self.crashed_after_claude_finished("MQ_RESULT: FAILED - the page does not exist")
        self.assertEqual(self.mq("run")[0], 0)
        task = self.task(task_id)
        self.assertEqual([run["outcome"] for run in task["runs"]], ["failed", "done"])
        self.assertIn("the page does not exist", task["runs"][0]["error"])
        self.assertEqual(task["attempts"], 2)
        self.assertEqual(len(self.starts()), 1)  # only the retry actually ran

    def test_incomplete_output_still_means_interrupted(self):
        task_id = self.add("Add a search box to the customers page.")
        _, runner = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        with open(claim["stdout_file"], "w") as handle:
            handle.write('{"type": "result", "result": "cut off in the mid')
        self.assertEqual(self.mq("run")[0], 0)
        self.assertEqual([run["outcome"] for run in self.task(task_id)["runs"]],
                         ["interrupted", "done"])

    def test_recover_command_reconciles_too(self):
        task_id = self.add("Add a search box to the customers page.")
        self.crashed_after_claude_finished("MQ_RESULT: DONE - all good")
        code, out, _ = self.mq("recover")
        self.assertIn("had already finished", out)
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertIsNone(self.mq_json("status")["runner"])


class RunnerOwnership(QueueTestCase):

    def protocol(self, *args, check=True):
        code, out, _ = self.mq(*args, check=check)
        return code, (json.loads(out) if out.strip() else None)

    def test_reused_process_id_does_not_keep_a_dead_runners_lock_alive(self):
        # After a reboot the process id of a dead runner is often given to another program.
        self.add("Fix the typo in the README title.")
        _, first = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        code, refused = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test",
                                      check=False)
        self.assertEqual(code, 2)  # same process, really alive: refused
        q = self.queue()
        changed = q.conn.execute("UPDATE process_tokens SET token='started-at-another-time' "
                                 "WHERE kind='runner'").rowcount
        self.assertEqual(changed, 1)
        self.assertFalse(self.mq_json("status")["runner"]["alive"])
        code, second = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        self.assertEqual(code, 0)
        self.assertNotEqual(second["runner_id"], first["runner_id"])

    def test_reused_process_id_of_a_task_does_not_block_recovery(self):
        task_id = self.add("Fix the typo in the README title.")
        _, crashed = self.protocol("runner-acquire", "--pid", str(dead_pid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", crashed["runner_id"])
        self.protocol("heartbeat", "--runner-id", crashed["runner_id"], "--run-id",
                      str(claim["run_id"]), "--child-pid", str(os.getpid()))
        code, refused = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test",
                                      check=False)
        self.assertEqual(code, 2)  # the recorded task process really is this live process
        self.queue().conn.execute("UPDATE process_tokens SET token='other' WHERE kind='run'")
        code, recovered = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        self.assertEqual(code, 0)
        self.assertEqual(recovered["recovered"][0]["task_id"], task_id)

    def test_lock_held_on_another_machine_expires_by_heartbeat(self):
        self.add("Fix the typo in the README title.")
        self.protocol("runner-acquire", "--pid", "4242", "--kind", "test")
        q = self.queue()
        q.conn.execute("UPDATE runner_lock SET host='another-machine'")
        code, refused = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test",
                                      check=False)
        self.assertEqual(code, 2)  # its heartbeat is fresh
        q.conn.execute("UPDATE runner_lock SET heartbeat_at='2020-01-01T00:00:00Z'")
        self.assertEqual(self.protocol("runner-acquire", "--pid", str(os.getpid()),
                                       "--kind", "test")[0], 0)

    def test_runner_cannot_start_a_second_task_before_finishing_the_first(self):
        first, second = self.add("Fix the typo in the README title."), self.add("Update the docs.")
        _, runner = self.protocol("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        _, claim = self.protocol("claim", "--runner-id", runner["runner_id"])
        code, reply = self.protocol("claim", "--runner-id", runner["runner_id"], check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("has not been finished", reply["error"])
        self.assertEqual(self.statuses(), {first: "running", second: "pending"})
        self.protocol("finish", "--runner-id", runner["runner_id"], "--run-id",
                      str(claim["run_id"]), "--interrupted")
        self.assertEqual(self.protocol("claim", "--runner-id", runner["runner_id"])[1]["action"], "run")


class WatchMode(object):

    def test_watching_runner_picks_up_tasks_added_later(self):
        runner = self.start_queue(watch=True, poll_seconds=1)
        self.wait_for(lambda: self.mq_json("status")["runner"] is not None, message="the runner")
        first = self.add("Fix the typo in the README title.")
        self.script(first, "write README.md one")
        self.wait_for(lambda: self.statuses().get(first) == "done", timeout=120,
                      message="the first task")
        self.assertIsNone(runner.poll())  # still watching
        second = self.add("Update the docs.")
        self.wait_for(lambda: self.statuses().get(second) == "done", timeout=120,
                      message="the second task")
        self.assertEqual([task for task, _ in self.starts()], [first, second])
        kill_tree(runner.pid)
        runner.wait()
        # The next runner takes over the stale lock without re-running anything.
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.starts()), 2)


globals().update(runner_variants(WatchMode))

if __name__ == "__main__":
    unittest.main()
