"""Auto Queue: a project that opted in gets a runner when a task is saved.

The runner is the ordinary portable runner (watch mode with an idle timeout); these tests
start it for real, as a detached process, with the fake Claude CLI.
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest

from helpers import IS_WINDOWS, MQ, POWERSHELL, RUNNER_PS1, QueueTestCase, kill_tree


class AutoQueueCase(QueueTestCase):

    def setUp(self):
        QueueTestCase.setUp(self)
        self.set_config(auto_queue_idle_timeout_seconds=2)

    def tearDown(self):
        self.stop_runners()
        QueueTestCase.tearDown(self)

    # ------------------------------------------------------------------ helpers

    def stop_runners(self, project=None):
        """Detached runners are not children of the test: stop them by their recorded pid."""
        from mqlib import autoqueue, engine
        try:
            q = self.queue(project)
        except Exception:
            return
        info = engine.lock_info(q)
        pids = [info["pid"]] if info and info["alive"] else []
        reservation = autoqueue._meta(q, "auto_launch") or {}
        if reservation.get("pid"):
            pids.append(reservation["pid"])
        for pid in pids:
            if pid != os.getpid():      # a test may hold the lock itself
                kill_tree(pid)
        deadline = time.time() + 15
        while time.time() < deadline and engine.lock_info(q) and engine.lock_info(q)["alive"]:
            time.sleep(0.2)

    def enable(self, project=None, *extra):
        return self.mq("auto", "enable", "--yes", *extra, project=project)

    def lock(self, project=None):
        from mqlib import engine
        return engine.lock_info(self.queue(project))

    def runner_alive(self, project=None):
        info = self.lock(project)
        return bool(info and info["alive"])

    def wait_status(self, task_id, status, timeout=90, project=None):
        self.wait_for(lambda: self.queue(project).get(task_id)["status"] == status, timeout,
                      "task #%d to be %s" % (task_id, status))

    def wait_idle_exit(self, project=None, timeout=60):
        self.wait_for(lambda: not self.runner_alive(project) and not self.starting(project),
                      timeout, "the runner to exit")

    def starting(self, project=None):
        from mqlib import autoqueue
        return autoqueue.runner_state(self.queue(project))["runner"] == "starting"

    def event_types(self, kind, project=None):
        return [row for row in self.queue(project).events(limit=2000) if row["type"] == kind]

    def hook(self, prompt, session="s1", prompt_id=None, project=None):
        payload = {"prompt": prompt, "session_id": session, "cwd": project or self.project}
        if prompt_id:
            payload["prompt_id"] = prompt_id
        result = subprocess.run([sys.executable, MQ, "hook-prompt"],
                                input=json.dumps(payload).encode("utf-8"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, CLAUDE_PROJECT_DIR=project or self.project))
        return json.loads(result.stdout.decode("utf-8"))


class Consent(AutoQueueCase):

    def test_off_by_default_and_a_global_setting_cannot_turn_it_on(self):
        self.set_config(auto_queue_enabled=True)      # written into the global file by hand
        code, out, err = self.mq("config", "set", "auto_queue_enabled", "true", "--global",
                                 check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("one project only", err)
        _, out, _ = self.mq("add", "Fix the typo in the README heading")
        self.assertNotIn("Runner started", out)
        time.sleep(1)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.statuses(), {1: "pending"})
        self.assertEqual(self.event_types("auto-launch"), [])

    def test_enable_shows_what_it_means_and_changes_nothing_without_yes(self):
        _, out, _ = self.mq("auto", "enable")
        for text in ("Runner permissions: --permission-mode acceptEdits", "Workspace:",
                     "Cost:", "waits up to 2 s", "Pause:", "Nothing changed yet"):
            self.assertIn(text, out)
        from mqlib import util
        self.assertFalse(util.effective_config(self.queue().dir)["auto_queue_enabled"])

    def test_enabling_one_project_leaves_the_others_manual(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.init_git(other)
        self.enable()
        _, out, _ = self.mq("add", "Fix the typo in the README heading", project=other)
        self.assertNotIn("Runner started", out)
        self.assertFalse(self.runner_alive(other))
        _, out, _ = self.mq("add", "Fix the typo in the README heading")
        self.assertIn("Task #1 saved. Runner started.", out)
        self.wait_status(1, "done")
        self.assertEqual(self.queue(other).get(1)["status"], "pending")

    def test_a_queued_task_cannot_turn_auto_queue_on(self):
        env = dict(os.environ, MOA7AMED_QUEUE_RUNNER="1")
        result = subprocess.run([sys.executable, MQ, "--project", self.project, "auto",
                                 "enable", "--yes"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"only the owner", result.stderr)


class Submission(AutoQueueCase):

    def test_the_task_is_committed_before_the_runner_is_started(self):
        from mqlib import autoqueue, pyrunner, store, util
        self.enable()
        q = self.queue()
        seen = {}

        def fake_launch(queue, options, flags, hidden=False, engine_kind=None):
            other = store.Queue(queue.root)       # another connection sees only committed data
            seen["status"] = other.get(task_id)["status"]
            seen["in_transaction"] = queue.conn.in_transaction
            seen["flags"] = flags
            other.close()
            raise OSError("no process today")

        task_id = q.add("Fix the typo in the README heading")
        original = pyrunner.launch
        pyrunner.launch = fake_launch
        try:
            result = autoqueue.ensure_runner(q, util.effective_config(q.dir), task_id)
        finally:
            pyrunner.launch = original
        self.assertEqual(seen["status"], "pending")
        self.assertFalse(seen["in_transaction"])
        self.assertEqual(seen["flags"]["idle_timeout"], 2)
        self.assertTrue(seen["flags"]["watch"] and seen["flags"]["auto"])
        # A failed launch keeps the task, records why, and never claims that it started.
        self.assertEqual(result["state"], "failed")
        self.assertIn("Runner launch failed: no process today", result["message"])
        self.assertNotIn("started", result["message"].replace("start it", ""))
        self.assertEqual(q.get(task_id)["status"], "pending")
        self.assertEqual(len(self.event_types("auto-launch-failed")), 1)

    def test_failed_launches_stop_at_the_limit_until_resumed(self):
        from mqlib import autoqueue, pyrunner, util
        self.enable()
        self.set_config(auto_queue_launch_retry_limit=2)
        q = self.queue()
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            raise OSError("cannot start")

        original = pyrunner.launch
        pyrunner.launch = failing
        try:
            states = []
            for number in range(4):
                task_id = q.add("Fix typo number %d in the README" % number)
                states.append(autoqueue.ensure_runner(q, util.effective_config(q.dir),
                                                      task_id)["state"])
        finally:
            pyrunner.launch = original
        self.assertEqual(states, ["failed", "failed", "stopped", "stopped"])
        self.assertEqual(len(calls), 2)                    # no endless retries
        self.assertEqual(self.statuses(), {1: "pending", 2: "pending", 3: "pending",
                                           4: "pending"})
        _, out, _ = self.mq("auto", "resume")
        self.assertIn("Runner started", out)
        for task_id in (1, 2, 3, 4):
            self.wait_status(task_id, "done")

    def test_identical_requests_are_separate_tasks_unless_they_carry_the_same_id(self):
        self.mq("add", "--no-auto", "Update the changelog")
        self.mq("add", "--no-auto", "Update the changelog")
        self.assertEqual(len(self.queue().tasks()), 2)
        _, out, _ = self.mq("add", "--no-auto", "--request-id", "r-1", "Update the changelog")
        _, again, _ = self.mq("add", "--no-auto", "--request-id", "r-1", "Update the changelog")
        self.assertIn("Already saved as task #3", again)
        self.assertEqual(len(self.queue().tasks()), 3)

    def test_the_hook_saves_one_prompt_once_and_starts_the_runner(self):
        self.enable()
        first = self.hook("queue: Fix the typo in the README heading", prompt_id="p-1")
        self.assertEqual(first["decision"], "block")
        self.assertIn("Task #1 saved", first["reason"])
        self.assertIn("Runner started.", first["reason"])
        # The same prompt seen again (same prompt id) is not saved twice. From the same
        # session the existing repeat check answers first; the prompt id covers the rest.
        again = self.hook("queue: Fix the typo in the README heading", prompt_id="p-1")
        self.assertIn("NOT queued again", again["reason"])
        again = self.hook("queue: Fix the typo in the README heading", session="s9",
                          prompt_id="p-1")
        self.assertIn("Already saved as task #1", again["reason"])
        # The same words from another session, with another prompt id, are a new request.
        other = self.hook("queue: Fix the typo in the README heading", session="s2",
                          prompt_id="p-2")
        self.assertIn("Task #2 saved", other["reason"])
        self.wait_status(1, "done")
        self.wait_status(2, "done")
        self.assertEqual(sorted(task for task, _ in self.starts()), [1, 2])

    def test_concurrent_submissions_start_one_runner_and_lose_nothing(self):
        self.enable()
        for number in range(1, 7):
            self.script(number, "sleep 0.5")
        processes = [subprocess.Popen([sys.executable, MQ, "--project", self.project, "add",
                                       "Fix typo number %d in the README" % number],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for number in range(1, 7)]
        outputs = [process.communicate()[0].decode("utf-8") for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), outputs)
        for task_id in range(1, 7):
            self.wait_status(task_id, "done", timeout=120)
        starts = [row for row in self.event_types("runner-start") if "(auto," in row["detail"]]
        self.assertEqual(len(starts), 1, [row["detail"] for row in starts])
        self.assertEqual(sorted(task for task, _ in self.starts()), [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.event_types("auto-launch-failed"), [])

    def test_runner_does_not_carry_the_interactive_session_variables(self):
        self.enable()
        env = dict(os.environ, CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="interactive-session")
        subprocess.run([sys.executable, MQ, "--project", self.project, "add",
                        "Fix the typo in the README heading"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        self.wait_status(1, "done")
        start = self.events_of("start")[0]
        self.assertIsNone(start["session_env"])
        self.assertEqual(start["runner_env"], "1")


class Lifecycle(AutoQueueCase):

    def test_a_task_saved_while_another_runs_waits_and_nothing_is_interrupted(self):
        self.enable()
        self.script(1, "sleep 3", "write a.txt first")
        self.mq("add", "Create a.txt with the word first")
        self.wait_status(1, "running")
        _, out, _ = self.mq("add", "Create b.txt with the word second")
        self.assertIn("Task #2 saved. Waiting behind task #1.", out)
        self.wait_status(2, "done")
        task1 = self.task(1)
        self.assertEqual(task1["status"], "done")
        self.assertEqual(len(task1["runs"]), 1)               # one run, never cut off
        starts = [event for event in self.fake_events() if event["event"] in ("start", "end")]
        order = [(event["event"], event.get("task")) for event in starts]
        self.assertLess(order.index(("end", 1)), order.index(("start", 2)))

    def test_dependencies_are_kept(self):
        self.enable()
        self.script(1, "sleep 1")
        self.mq("add", "--no-auto", "Write the parser")
        self.mq("add", "--after", "1", "Document the parser")
        self.wait_status(2, "done")
        self.assertEqual([task for task, _ in self.starts()], [1, 2])

    def test_idle_runner_exits_and_the_next_task_starts_a_new_one(self):
        self.enable()
        self.mq("add", "Fix the typo in the README heading")
        self.wait_status(1, "done")
        self.wait_idle_exit()
        self.assertTrue(self.event_types("runner-stop"))
        _, out, _ = self.mq("add", "Fix the typo in the CONTRIBUTING heading")
        self.assertIn("Runner started.", out)
        self.wait_status(2, "done")
        starts = [row for row in self.event_types("runner-start") if "(auto," in row["detail"]]
        self.assertEqual(len(starts), 2)

    def test_a_task_saved_during_the_idle_exit_is_never_stranded(self):
        """The runner releases its lock only in the transaction that sees nothing runnable."""
        from mqlib import autoqueue, engine, pyrunner, util
        self.enable()
        q = self.queue()
        acquired = engine.acquire(q, os.getpid(), None, kind=autoqueue.RUNNER_KIND)
        runner_id = acquired["runner_id"]
        q.add("Saved just before the runner gives up")         # committed first
        self.assertFalse(engine.release_if_idle(q, runner_id))  # so the runner stays
        self.assertTrue(self.lock()["alive"])
        claim = engine.claim(q, runner_id)
        self.assertEqual(claim["action"], "run")
        engine.finish(q, runner_id, claim["run_id"], exit_code=1)
        with q.tx():
            q.conn.execute("UPDATE tasks SET status='cancelled' WHERE id=1")
        self.assertTrue(engine.release_if_idle(q, runner_id))   # nothing runnable: released
        # The other order: the lock is gone first, so the submission starts a runner.
        launched = []
        original = pyrunner.launch
        pyrunner.launch = lambda *args, **kwargs: launched.append(1) or {"pid": os.getpid()}
        try:
            task_id = q.add("Saved just after the runner gave up")
            state = autoqueue.ensure_runner(q, util.effective_config(q.dir), task_id)
        finally:
            pyrunner.launch = original
        self.assertEqual((state["state"], launched), ("started", [1]))

    def test_pause_resume_disable_and_stop_after_the_current_task(self):
        self.enable()
        self.script(1, "sleep 3")
        self.mq("add", "Fix the typo in the README heading")
        self.wait_status(1, "running")
        self.mq("auto", "pause")
        _, out, _ = self.mq("add", "Fix the typo in the CONTRIBUTING heading")
        self.assertIn("Task #2 saved. Automatic execution is paused.", out)
        self.wait_status(1, "done")                          # the active task finishes
        self.wait_idle_exit()
        self.assertEqual(self.queue().get(2)["status"], "pending")
        self.assertNotIn(2, [task for task, _ in self.starts()])
        # A pause survives restarts: a manual runner does not start task 2 either.
        code, output = self.run_manual()
        self.assertIn("paused", output)
        self.assertEqual(self.queue().get(2)["status"], "pending")
        self.mq("auto", "resume")
        self.wait_status(2, "done")
        self.assertEqual([task for task, _ in self.starts()].count(2), 1)
        # Stop after the current task: task 3 finishes, task 4 waits.
        self.wait_idle_exit()
        self.script(3, "sleep 3")
        self.mq("add", "Fix the typo in the LICENSE heading")
        self.wait_status(3, "running")
        self.mq("add", "--no-auto", "Fix the typo in the NOTICE heading")
        _, out, _ = self.mq("auto", "stop")
        self.assertIn("stops after task #3", out)
        self.wait_status(3, "done")
        self.wait_idle_exit()
        self.assertEqual(self.queue().get(4)["status"], "pending")
        # Disable keeps the tasks and starts nothing.
        self.mq("auto", "disable")
        _, out, _ = self.mq("add", "Fix the typo in the AUTHORS heading")
        self.assertNotIn("Runner", out)
        time.sleep(1)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.queue().get(4)["status"], "pending")
        self.assertEqual(self.queue().get(5)["status"], "pending")

    def run_manual(self):
        result = subprocess.run([sys.executable, MQ, "--project", self.project, "run"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        return result.returncode, result.stdout.decode("utf-8", errors="replace")

    def test_stop_now_holds_the_task_and_keeps_its_changes(self):
        self.enable()
        self.script(1, "write partial.txt half-done", "sleep 60")
        self.mq("add", "Create partial.txt")
        self.wait_status(1, "running")
        self.wait_for(lambda: os.path.exists(os.path.join(self.project, "partial.txt")),
                      30, "the task to write its file")
        code, _, err = self.mq("auto", "stop", "--now", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("--yes", err)
        _, out, _ = self.mq("auto", "stop", "--now", "--yes")
        self.assertIn("Stopped", out)
        task = self.task(1)
        self.assertEqual(task["status"], "failed")
        self.assertIn("stopped by the owner", task["last_error"])
        self.assertTrue(os.path.exists(os.path.join(self.project, "partial.txt")))
        self.assertFalse(self.runner_alive())
        _, out, _ = self.mq("auto")
        self.assertIn("paused", out)


class Recovery(AutoQueueCase):

    def test_a_killed_runner_is_recovered_by_the_next_submission(self):
        self.enable()
        self.script(1, "sleep-once 60")
        self.mq("add", "Fix the typo in the README heading")
        self.wait_status(1, "running")
        info = self.lock()
        from mqlib import engine
        run = self.queue().conn.execute("SELECT child_pid FROM runs WHERE task_id=1").fetchone()
        kill_tree(info["pid"])
        if run["child_pid"]:
            kill_tree(run["child_pid"])
        self.wait_for(lambda: not self.runner_alive(), 30, "the runner to be gone")
        # Nothing is pending (task 1 is still marked running): starting must still happen,
        # because only a starting runner recovers the interrupted task.
        _, out, _ = self.mq("auto", "start")
        self.assertIn("Runner started", out)
        self.wait_status(1, "done")
        self.assertTrue(self.event_types("interrupted"))
        _, out, _ = self.mq("add", "Fix the typo in the CONTRIBUTING heading")
        self.wait_status(2, "done")

    def test_partial_changes_of_an_interrupted_task_are_not_run_over(self):
        self.enable()
        self.script(1, "write partial.txt half-done", "sleep-once 60")
        self.mq("add", "Create partial.txt")
        self.wait_for(lambda: os.path.exists(os.path.join(self.project, "partial.txt")),
                      60, "the task to write its file")
        info = self.lock()
        run = self.queue().conn.execute("SELECT child_pid FROM runs WHERE task_id=1").fetchone()
        kill_tree(info["pid"])
        if run["child_pid"]:
            kill_tree(run["child_pid"])
        self.wait_for(lambda: not self.runner_alive(), 30, "the runner to be gone")
        _, out, _ = self.mq("auto", "start")
        self.assertIn("Runner started", out)
        self.wait_for(lambda: self.event_types("interrupted"), 60, "the recovery")
        self.wait_idle_exit()
        self.assertEqual(self.queue().get(1)["status"], "pending")      # recovered, not lost
        self.assertEqual(self.read("partial.txt"), "half-done\n")        # left as it was
        _, out, _ = self.mq("auto", "blockers")
        self.assertIn("uncommitted changes", out)
        self.assertIn("reconcile", out)
        # Once the owner has dealt with the leftovers, the task runs again.
        self.git("stash", "-u")
        self.mq("auto", "start")
        self.wait_status(1, "done")

    def test_a_reused_process_id_is_not_mistaken_for_the_runner(self):
        from mqlib import autoqueue, engine
        self.enable()
        q = self.queue()
        acquired = engine.acquire(q, os.getpid(), None, kind=autoqueue.RUNNER_KIND)
        with q.tx():   # the recorded start time belongs to another process
            q.conn.execute("UPDATE process_tokens SET token='not-this-process' WHERE kind='runner'")
        self.assertFalse(self.lock()["alive"])
        _, out, _ = self.mq("add", "Fix the typo in the README heading")
        self.assertIn("Runner started.", out)
        self.wait_status(1, "done")
        self.assertTrue(self.event_types("lock-recovered"))
        self.assertTrue(util_alive(os.getpid()))            # the test process was not touched

    def test_a_runner_that_never_starts_counts_as_a_failed_launch(self):
        from mqlib import autoqueue, util
        self.enable()
        q = self.queue()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with q.tx():
            autoqueue._set_meta(q, "auto_launch", {"pid": dead.pid, "at": util.now_iso(),
                                                   "token": "gone"})
        task_id = q.add("Fix the typo in the README heading")
        state = autoqueue.ensure_runner(q, util.effective_config(q.dir), task_id)
        self.assertEqual(state["state"], "started")
        self.assertEqual(len(self.event_types("auto-launch-failed")), 1)
        self.wait_status(task_id, "done")
        self.assertIsNone(autoqueue._meta(self.queue(), "auto_failures"))  # reset on success


class Gates(AutoQueueCase):

    def test_blocked_work_does_not_start_runners(self):
        from mqlib import routing
        self.enable()
        q = self.queue()
        task_id = q.add("Design the new billing architecture", model="sonnet")
        with q.tx():
            routing._block(q, task_id, "approval: plan waits for the owner")
        _, out, _ = self.mq("add", "--after", str(task_id), "Implement the billing plan")
        self.assertIn("Blocked:", out)
        self.assertIn("approval", out)
        time.sleep(1)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.event_types("auto-launch"), [])
        _, out, _ = self.mq("auto", "blockers")
        self.assertIn("approval", out)

    def test_uncommitted_changes_in_place_are_reported_not_run_over(self):
        self.enable()
        self.write("notes.txt", "my work in progress\n")
        _, out, _ = self.mq("add", "Fix the typo in the README heading")
        self.assertIn("uncommitted changes", out)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.read("notes.txt"), "my work in progress\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "my work")
        _, out, _ = self.mq("auto", "start")
        self.assertIn("Runner started", out)
        self.wait_status(1, "done")

    def test_the_allowance_survives_restarts_and_must_be_renewed(self):
        self.mq("auto", "enable", "--yes", "--budget", "0.02")
        self.mq("add", "Fix the typo in the README heading")        # reports $0.0123
        self.wait_status(1, "done")
        self.wait_idle_exit()
        self.mq("add", "Fix the typo in the CONTRIBUTING heading")  # a new runner
        self.wait_status(2, "done")
        start = [event for event in self.events_of("start") if event["task"] == 2][0]
        budget = start["argv"][start["argv"].index("--max-budget-usd") + 1]
        self.assertAlmostEqual(float(budget), 0.02 - 0.0123, places=4)   # what was left
        self.wait_idle_exit()
        _, out, _ = self.mq("add", "Fix the typo in the LICENSE heading")
        self.assertIn("allowance of $0.02 is used up", out)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.queue().get(3)["status"], "pending")
        # Neither resuming, nor disabling and enabling again, renews it.
        self.mq("auto", "resume")
        self.mq("auto", "disable")
        _, out, _ = self.mq("auto", "enable", "--yes", "--budget", "0.02")
        self.assertIn("does not renew it", out)
        self.assertIn("used up", out)
        time.sleep(1)
        self.assertFalse(self.runner_alive())
        self.assertEqual(self.queue().get(3)["status"], "pending")
        _, out, _ = self.mq("auto", "budget", "--renew")
        self.wait_status(3, "done")


class HookFaults(AutoQueueCase):
    """Claude Code cancels a prompt hook after 30 s and then sends the prompt to Claude.
    The hook must answer well before that, whatever goes wrong."""

    WRAPPER = (
        "import os, sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from mqlib import autoqueue, hook, pyrunner\n"
        "fault = os.environ['FAULT']\n"
        "if fault == 'launch-hangs':\n"
        "    pyrunner.launch = lambda *a, **k: time.sleep(300)\n"
        "if fault == 'stuck-after-save':\n"
        "    autoqueue.ensure_runner = lambda *a, **k: time.sleep(300)\n"
        "    hook.START_WAIT_SECONDS = 300\n"
        "sys.exit(hook.main())\n")

    def run_hook(self, fault, prompt, prompt_id, session="s1", timeout=120):
        wrapper = os.path.join(self.tmp, "hook_wrapper.py")
        with open(wrapper, "w") as handle:
            handle.write(self.WRAPPER % os.path.join(os.path.dirname(MQ)))
        payload = json.dumps({"prompt": prompt, "session_id": session, "cwd": self.project,
                              "prompt_id": prompt_id}).encode("utf-8")
        env = dict(os.environ, FAULT=fault, CLAUDE_PROJECT_DIR=self.project)
        # Its own process group: kill_tree stops a whole group on Linux and macOS, and the
        # test runner must not be in it.
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS
                 else {"start_new_session": True})
        return subprocess.Popen([sys.executable, wrapper], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                **group), payload

    def test_a_locked_queue_is_reported_in_time_and_the_prompt_is_still_blocked(self):
        self.enable()
        from mqlib import store, util
        holder = store.Queue(util.resolve_project_root(self.project))
        holder.conn.execute("BEGIN IMMEDIATE")          # another process holds the queue
        try:
            started = time.time()
            reply = self.hook("queue: Fix the typo in the README heading", prompt_id="p-1")
            elapsed = time.time() - started
        finally:
            holder.conn.execute("ROLLBACK")
            holder.close()
        self.assertLess(elapsed, 20)                     # far below Claude Code's 30 s
        self.assertEqual(reply["decision"], "block")     # not sent to Claude either
        self.assertIn("could NOT save", reply["reason"])
        self.assertEqual(self.queue().tasks(), [])

    def test_a_hanging_runner_start_does_not_hold_up_the_answer(self):
        self.enable()
        process, payload = self.run_hook("launch-hangs", "queue: Fix the typo in the README",
                                         "p-1")
        started = time.time()
        out, err = process.communicate(payload, timeout=60)
        elapsed = time.time() - started
        reply = json.loads(out.decode("utf-8"))
        self.assertLess(elapsed, 25, err)
        self.assertEqual(reply["decision"], "block")
        self.assertIn("Task #1 saved", reply["reason"])
        self.assertIn("did not finish within 12 s", reply["reason"])
        self.assertEqual(len(self.queue().tasks()), 1)

    def test_a_hook_cut_off_after_saving_does_not_save_the_prompt_twice(self):
        self.enable()
        process, payload = self.run_hook("stuck-after-save", "queue: Fix the typo in the README",
                                         "p-7")
        process.stdin.write(payload)
        process.stdin.close()
        self.wait_for(lambda: len(self.queue().tasks()) == 1, 30, "the task to be saved")
        kill_tree(process.pid)                           # as Claude Code cancels a hook
        process.wait()
        self.assertEqual(process.stdout.read(), b"")     # no answer was delivered
        process.stdout.close()
        process.stderr.close()
        # Submitted again: the same prompt id, or the same text from the same session, is
        # recognised; only an explicit new request saves another task.
        again = self.hook("queue: Fix the typo in the README", session="s2", prompt_id="p-7")
        self.assertIn("Already saved as task #1", again["reason"])
        again = self.hook("queue: Fix the typo in the README", session="s1", prompt_id="p-8")
        self.assertIn("NOT queued again", again["reason"])
        self.assertEqual(len(self.queue().tasks()), 1)
        self.mq("add", "--no-auto", "Fix the typo in the README")
        self.assertEqual(len(self.queue().tasks()), 2)


class Platforms(AutoQueueCase):

    def test_paths_with_spaces_and_arabic_letters(self):
        project = os.path.join(self.tmp, "مشروع تجريبي")
        os.makedirs(project)
        self.init_git(project)
        self.enable(project)
        reply = self.hook("queue: أصلح الخطأ الإملائي في العنوان", project=project)
        self.assertIn("Runner started.", reply["reason"])
        self.wait_status(1, "done", project=project)
        self.stop_runners(project)

    @unittest.skipUnless(IS_WINDOWS and POWERSHELL, "the PowerShell runner is the Windows runner")
    def test_powershell_runner_stops_claiming_while_paused(self):
        self.mq("add", "Fix the typo in the README heading")
        self.mq("auto", "pause")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 RUNNER_PS1, "-Project", self.project, "-Watch"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertIn(b"paused", result.stdout)
        self.assertEqual(self.queue().get(1)["status"], "pending")


def util_alive(pid):
    from mqlib import util
    return util.pid_alive(pid)


if __name__ == "__main__":
    unittest.main()
