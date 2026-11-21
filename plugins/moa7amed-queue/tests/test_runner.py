"""Sequential execution: every test here runs against each available runner
(the portable Python runner and, where PowerShell exists, runner/run-queue.ps1)."""
import os
import unittest

from helpers import IS_WINDOWS, POWERSHELL, QueueTestCase, kill_tree, runner_variants


class SequentialExecution(object):

    def test_tasks_run_one_at_a_time_in_queue_order(self):
        q = self.queue()
        first = q.add("FAKE: sleep 0.5\nFAKE: write one.txt first")
        low = q.add("FAKE: sleep 0.5\nFAKE: write low.txt low", priority=-10)
        urgent = q.add("FAKE: sleep 0.5\nFAKE: write urgent.txt urgent", priority=20)
        after_low = q.add("FAKE: sleep 0.5\nFAKE: write after.txt after", after=[low], priority=20)
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        self.assertEqual(set(self.statuses().values()), {"done"})

        events = self.fake_events()
        starts = [event["task"] for event in events if event["event"] == "start"]
        self.assertEqual(starts, [urgent, first, low, after_low])
        # Strictly sequential: every task ends before the next one starts.
        self.assertEqual([event["event"] for event in events], ["start", "end"] * 4)
        times = [event["time"] for event in events]
        self.assertEqual(times, sorted(times))

        # One commit per task, in execution order, each traceable to its task.
        subjects = self.git("log", "--format=%s", "--reverse").splitlines()[1:]
        self.assertEqual(len(subjects), 4)
        for subject, task_id in zip(subjects, starts):
            self.assertTrue(subject.endswith("(task #%d)" % task_id), subject)
        self.assertIn("Task-Queue-Id: %d" % urgent, self.git("log", "--format=%B"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        task = self.task(urgent)
        self.assertEqual(task["commit_sha"], self.git("rev-list", "--reverse", "HEAD").splitlines()[1])
        self.assertEqual(task["runs"][0]["files_changed"], ["urgent.txt"])
        self.assertEqual(task["result_summary"], "fake result for task %d" % urgent)
        self.assertIsNone(self.queue().conn.execute("SELECT * FROM runner_lock").fetchone())

    def test_claude_receives_the_original_prompt_and_criteria(self):
        prompt = "Rename the config module.\nKeep \"quotes\" & $symbols intact."
        task_id = self.add(prompt, acceptance="imports still resolve")
        dependent = self.add("Update the docs", after=[task_id])
        code, output = self.run_queue(permission_mode="acceptEdits")
        self.assertEqual(code, 0, output)
        starts = [event for event in self.fake_events() if event["event"] == "start"]
        first = starts[0]
        self.assertIn("<task>\n%s\n</task>" % prompt, first["prompt"])
        self.assertIn("<acceptance_criteria>\nimports still resolve\n</acceptance_criteria>",
                      first["prompt"])
        self.assertEqual(first["argv"][:5],
                         ["-p", "--output-format", "json", "--permission-mode", "acceptEdits"])
        self.assertEqual(first["runner_env"], "1")
        self.assertEqual(os.path.realpath(first["cwd"]), os.path.realpath(self.project))
        # The dependent task is told what its prerequisite achieved.
        self.assertIn("Task #%d: Rename the config module." % task_id, starts[1]["prompt"])
        self.assertIn("fake result for task %d" % task_id, starts[1]["prompt"])
        run = self.task(task_id)["runs"][0]
        self.assertEqual(run["session_id"], "fake-session-%d-1" % task_id)
        self.assertAlmostEqual(run["cost_usd"], 0.0123)
        self.assertEqual(self.task(dependent)["status"], "done")

    def test_failed_attempt_is_retried_and_its_leftovers_are_set_aside(self):
        task_id = self.add("FAKE: write draft.txt half-finished\nFAKE: fail-once tests are red")
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        task = self.task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["attempts"], 2)
        self.assertEqual([run["outcome"] for run in task["runs"]], ["failed", "done"])
        self.assertIn("tests are red", task["runs"][0]["error"])
        # The failed attempt's changes were stashed (not lost, not committed)...
        stashes = self.git("stash", "list").splitlines()
        self.assertEqual(len(stashes), 1)
        self.assertIn("unfinished changes of task #%d" % task_id, stashes[0])
        self.assertEqual(task["runs"][0]["stash_ref"], self.git("rev-parse", "refs/stash"))
        # ...and the second attempt started clean and was committed.
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.read("draft.txt"), "half-finished\n")
        second_prompt = [e for e in self.fake_events() if e["event"] == "start"][1]["prompt"]
        self.assertIn("<previous_attempt>", second_prompt)
        self.assertIn("tests are red", second_prompt)

    def test_task_out_of_attempts_fails_and_blocks_its_dependents(self):
        q = self.queue()
        broken = q.add("FAKE: fail cannot be done", max_attempts=2)
        dependent = q.add("FAKE: write never.txt x", after=[broken])
        independent = q.add("FAKE: write fine.txt ok")
        code, output = self.run_queue()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.statuses(), {broken: "failed", dependent: "pending",
                                           independent: "done"})
        # Two attempts, then one more on a stronger model (escalation), then it is failed.
        self.assertEqual(self.task(broken)["attempts"], 3)
        self.assertEqual([model for task, model in self.starts() if task == broken],
                         ["sonnet", "sonnet", "opus"])
        self.assertEqual(self.task(dependent)["state"], "blocked")
        self.assertFalse(os.path.exists(os.path.join(self.project, "never.txt")))
        self.assertIn("blocked by failed or cancelled prerequisites", output)
        # A manual retry gives the task a fresh set of attempts.
        self.mq("retry", "--failed")
        self.assertEqual(self.task(broken)["state"], "pending")
        self.assertEqual(self.task(broken)["attempts"], 0)

    def test_crashing_claude_and_error_results_count_as_failures(self):
        q = self.queue()
        crashed = q.add("FAKE: exit 3", max_attempts=1)
        errored = q.add("FAKE: error", max_attempts=1)
        unmarked = q.add("FAKE: no-marker\nFAKE: write ok.txt fine", max_attempts=1)
        code, output = self.run_queue()
        self.assertEqual(code, 1, output)
        self.assertEqual(self.statuses(), {crashed: "failed", errored: "failed", unmarked: "done"})
        self.assertIn("exited with code 3", self.task(crashed)["last_error"])
        self.assertIn("reported an error", self.task(errored)["last_error"])

    def test_uncommitted_changes_stop_the_runner_and_are_left_alone(self):
        task_id = self.add("FAKE: write README.md overwritten by the task")
        self.write("README.md", "# my unsaved edit\n")
        self.write("notes/todo.txt", "untracked note\n")
        code, output = self.run_queue()
        self.assertEqual(code, 3, output)
        self.assertIn("uncommitted changes", output)
        self.assertEqual(self.read("README.md"), "# my unsaved edit\n")
        self.assertEqual(self.read("notes/todo.txt"), "untracked note\n")
        self.assertEqual(self.task(task_id)["status"], "pending")
        self.assertEqual(self.task(task_id)["attempts"], 0)
        self.assertEqual(self.fake_events(), [])
        self.assertEqual(self.git("stash", "list"), "")

    def test_stash_policy_sets_user_changes_aside_and_keeps_them_recoverable(self):
        task_id = self.add("FAKE: write feature.txt from the queue")
        self.write("README.md", "# my unsaved edit\n")
        self.write("notes/todo.txt", "untracked note\n")
        code, output = self.run_queue(on_dirty="stash")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.read("README.md"), "# sample project\n")  # task ran on a clean tree
        self.assertEqual(self.task(task_id)["runs"][0]["files_changed"], ["feature.txt"])
        self.assertEqual(len(self.git("stash", "list").splitlines()), 1)
        self.git("stash", "pop")
        self.assertEqual(self.read("README.md"), "# my unsaved edit\n")
        self.assertEqual(self.read("notes/todo.txt"), "untracked note\n")

    def test_allow_policy_runs_on_a_dirty_tree_without_committing(self):
        task_id = self.add("FAKE: write feature.txt from the queue")
        self.write("README.md", "# my unsaved edit\n")
        head = self.git("rev-parse", "HEAD")
        code, output = self.run_queue(on_dirty="allow")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read("README.md"), "# my unsaved edit\n")
        self.assertEqual(self.read("feature.txt"), "from the queue\n")
        self.assertIsNone(self.task(task_id)["commit_sha"])

    def test_commit_none_leaves_changes_in_the_working_tree(self):
        q = self.queue()
        q.add("FAKE: write a.txt a")
        q.add("FAKE: write b.txt b")
        head = self.git("rev-parse", "HEAD")
        code, output = self.run_queue(commit="none")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(sorted(self.git("status", "--porcelain").split()), ["??", "??", "a.txt", "b.txt"])

    def test_worktree_mode_never_touches_the_project_directory(self):
        task_id = self.add("FAKE: write feature.txt built in the worktree")
        self.write("README.md", "# work in progress, do not touch\n")
        head = self.git("rev-parse", "HEAD")
        code, output = self.run_queue(workspace="worktree")
        self.assertEqual(code, 0, output)
        # The project directory is exactly as the user left it.
        self.assertEqual(self.read("README.md"), "# work in progress, do not touch\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertFalse(os.path.exists(os.path.join(self.project, "feature.txt")))
        self.assertEqual(self.git("stash", "list"), "")
        # The result is a commit on the queue's branch.
        self.assertEqual(self.git("show", "task-queue:feature.txt"), "built in the worktree")
        self.assertEqual(self.task(task_id)["commit_sha"], self.git("rev-parse", "task-queue"))
        start = [e for e in self.fake_events() if e["event"] == "start"][0]
        self.assertNotEqual(os.path.realpath(start["cwd"]), os.path.realpath(self.project))
        self.assertEqual(os.path.realpath(start["project_env"]), os.path.realpath(self.project))

    def test_commits_made_by_claude_itself_are_recorded(self):
        task_id = self.add("FAKE: write own.txt mine\nFAKE: commit claude committed this")
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        task = self.task(task_id)
        self.assertEqual(task["commit_sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("log", "-1", "--format=%s"), "claude committed this")
        self.assertEqual(task["runs"][0]["files_changed"], ["own.txt"])

    def test_slow_task_is_stopped_at_the_time_limit(self):
        task_id = self.add("FAKE: sleep 120\nFAKE: write late.txt too late", max_attempts=1)
        code, output = self.run_queue(timeout_minutes=0.05)
        self.assertEqual(code, 1, output)
        task = self.task(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["runs"][0]["outcome"], "timeout")
        self.assertFalse(os.path.exists(os.path.join(self.project, "late.txt")))

    def test_task_runs_in_the_subdirectory_it_was_queued_from(self):
        package = os.path.join(self.project, "packages", "app")
        self.write("packages/app/package.json", "{}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add a package")
        self.mq("add", "FAKE: write here.txt made in the package", project=package)
        task = self.task(1)
        self.assertEqual(task["workdir"], "packages/app")
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        start = [e for e in self.fake_events() if e["event"] == "start"][0]
        self.assertEqual(os.path.realpath(start["cwd"]), os.path.realpath(package))
        self.assertEqual(self.read("packages/app/here.txt"), "made in the package\n")
        self.assertEqual(self.task(1)["runs"][0]["files_changed"], ["packages/app/here.txt"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_max_tasks_limits_one_run(self):
        q = self.queue()
        for name in ("a", "b", "c"):
            q.add("FAKE: write %s.txt %s" % (name, name))
        code, output = self.run_queue(max_tasks=2)
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(self.statuses().values()), ["done", "done", "pending"])

    def test_a_second_runner_is_refused_while_one_is_working(self):
        slow = self.add("FAKE: sleep 6\nFAKE: write slow.txt done")
        other = self.add("FAKE: write other.txt done")
        first_runner = self.start_queue()
        self.wait_for(lambda: self.statuses()[slow] == "running", message="the first runner")
        code, output = self.run_queue()
        self.assertEqual(code, 2, output)
        self.assertIn("another runner is already working", output)
        self.assertEqual(first_runner.wait(timeout=180), 0)
        self.assertEqual(self.statuses(), {slow: "done", other: "done"})
        starts = [e["task"] for e in self.fake_events() if e["event"] == "start"]
        self.assertEqual(starts, [slow, other])  # each task ran exactly once


class NonGitProject(object):
    use_git = False

    def test_plain_directory_runs_without_git(self):
        task_id = self.add("FAKE: write out.txt plain")
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.read("out.txt"), "plain\n")
        task = self.task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["commit_sha"])

    def test_folder_below_a_home_directory_repository_is_not_that_repository(self):
        # Some people keep their home directory under git (dotfiles). A plain folder
        # beneath it must get its own queue and the home repository must stay untouched.
        fake_home = os.path.join(self.tmp, "fakehome")
        os.makedirs(fake_home)
        self.init_git(fake_home)
        self.write("dotfile-in-progress", "uncommitted\n", root=fake_home)
        os.environ["HOME"] = os.environ["USERPROFILE"] = fake_home
        self.project = os.path.join(fake_home, "work", "notes")
        os.makedirs(self.project)
        head = self.git("rev-parse", "HEAD", cwd=fake_home)
        self.mq("add", "FAKE: write out.txt plain")
        from mqlib import util
        self.assertEqual(util.resolve_project_root(self.project), os.path.realpath(self.project))
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.read("out.txt"), "plain\n")
        self.assertEqual(self.task(1)["status"], "done")
        self.assertIsNone(self.task(1)["commit_sha"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=fake_home), head)
        self.assertEqual(self.git("stash", "list", cwd=fake_home), "")
        self.assertEqual(self.read("dotfile-in-progress", root=fake_home), "uncommitted\n")


class RecordedChanges(QueueTestCase):
    """Which files a task changed, as recorded with its run. Found by the live validation:
    a task that modified an existing file had the first letter of its name cut off, which
    no earlier test noticed because they all created new files."""

    def test_modified_and_new_files_are_recorded_by_their_full_names(self):
        task_id = self.add("update the documentation")
        self.script(task_id, "append README.md one more line",
                    "write docs/guide/intro.md hello", "write zz-last.txt x")
        self.mq("run")
        run = self.task(task_id)["runs"][0]
        self.assertEqual(run["outcome"], "done")
        self.assertEqual(run["files_changed"], ["README.md", "docs/guide/intro.md", "zz-last.txt"])
        self.assertEqual(self.git("show", "--format=", "--name-only", "HEAD").split(),
                         ["README.md", "docs/guide/intro.md", "zz-last.txt"])

    def test_leftovers_of_a_failed_task_are_named_correctly(self):
        task_id = self.add("change the readme", max_attempts=1)
        self.script(task_id, "append README.md half done", "fail it broke")
        self.mq("run", check=False)
        run = self.task(task_id)["runs"][0]
        self.assertEqual(run["outcome"], "failed")
        self.assertEqual(run["files_changed"], ["README.md"])
        self.assertIn("half done", self.git("show", "stash@{0}:README.md"))

    def test_uncommitted_user_changes_are_listed_by_their_full_names(self):
        self.add("some work")
        with open(os.path.join(self.project, "README.md"), "a") as handle:
            handle.write("the user's own edit\n")
        code, out, err = self.mq("run", check=False)
        self.assertEqual(code, 3)
        self.assertIn(" M README.md", out + err)


class DetachedLaunch(QueueTestCase):
    """`moa7amed-queue launch` starts a runner that outlives the command that started it."""

    def launch(self, *extra):
        result = self.mq_json("launch", "--hidden", *extra)
        self.addCleanup(kill_tree, result["pid"])
        return result

    def finished(self):
        return (set(self.statuses().values()) == {"done"}
                and self.queue().conn.execute("SELECT 1 FROM runner_lock").fetchone() is None)

    def test_default_runner_for_this_platform(self):
        self.add("FAKE: write a.txt a")
        self.add("FAKE: write b.txt b")
        result = self.launch()
        self.assertEqual(result["runner"], "powershell" if IS_WINDOWS and POWERSHELL else "python")
        self.wait_for(self.finished, timeout=180, message="the launched runner to finish")
        self.assertEqual(len(self.git("log", "--format=%s").splitlines()), 3)
        self.assertIn("finished: 2 run(s)", self.mq("log")[1])

    def test_python_runner_with_options(self):
        self.add("FAKE: write a.txt a")
        self.write("README.md", "# dirty\n")
        result = self.launch("--runner", "python", "--on-dirty", "stash", "--max-tasks", "1")
        self.assertEqual(result["runner"], "python")
        self.wait_for(self.finished, timeout=180, message="the launched runner to finish")
        self.assertEqual(len(self.git("stash", "list").splitlines()), 1)

    def test_launch_is_refused_while_a_runner_is_active(self):
        self.add("FAKE: sleep 8")
        self.launch("--runner", "python")
        self.wait_for(lambda: self.statuses()[1] == "running", message="the task to start")
        code, _, err = self.mq("launch", "--hidden", check=False)
        self.assertEqual(code, 2)
        self.assertIn("already working on this project", err)


globals().update(runner_variants(SequentialExecution))
globals().update(runner_variants(NonGitProject))

if __name__ == "__main__":
    unittest.main()
