"""The UserPromptSubmit capture hook: `queue: ...` prompts become tasks, everything else passes."""
import json
import os
import subprocess
import sys
import unittest

from helpers import MQ, ROOT, SH, QueueTestCase

HOOK_SCRIPT = os.path.join(ROOT, "hooks", "capture-prompt.sh")


class CaptureHook(QueueTestCase):

    def payload(self, prompt, cwd=None, session="session-123"):
        return json.dumps({"session_id": session, "transcript_path": "/tmp/t.jsonl",
                           "cwd": cwd or self.project, "permission_mode": "default",
                           "hook_event_name": "UserPromptSubmit", "prompt": prompt}).encode("utf-8")

    def hook(self, prompt, cwd=None, env=None, session="session-123"):
        """Run the hook the way Claude Code does: JSON on stdin, decision on stdout."""
        full_env = dict(os.environ)
        full_env.update(env or {})
        result = subprocess.run([sys.executable, MQ, "hook-prompt"],
                                input=self.payload(prompt, cwd, session),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env,
                                cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def test_prefixed_prompt_is_queued_and_kept_from_claude(self):
        decision = self.hook("queue: Add a retry to the uploader\nAcceptance criteria: upload "
                             "survives one dropped connection")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("Queued as task #1", decision["reason"])
        task = self.task(1)
        self.assertEqual(task["prompt"], "Add a retry to the uploader\nAcceptance criteria: "
                                         "upload survives one dropped connection")
        self.assertEqual(task["acceptance"], "upload survives one dropped connection")
        self.assertEqual((task["source"], task["session_id"], task["status"]),
                         ("hook", "session-123", "pending"))

    def test_prefix_is_case_insensitive_and_unicode_safe(self):
        self.hook("  QUEUE:   أضف اختبارا "
                  "جديدا — café")
        self.assertEqual(self.task(1)["prompt"],
                         "أضف اختبارا "
                         "جديدا — café")

    def test_ordinary_prompts_pass_through_untouched(self):
        for prompt in ("Fix the failing test", "what does queue: mean in this file?",
                       "/moa7amed-queue:status", ""):
            self.assertIsNone(self.hook(prompt), prompt)
        self.assertFalse(os.path.exists(os.path.join(self.home, "projects")))

    def test_each_capture_is_its_own_task(self):
        for index in range(3):
            self.hook("queue: follow-up number %d" % index)
        self.assertEqual([task["prompt"] for task in self.queue().tasks()],
                         ["follow-up number 0", "follow-up number 1", "follow-up number 2"])

    def test_task_remembers_the_session_directory(self):
        nested = os.path.join(self.project, "docs")
        os.makedirs(nested)
        self.hook("queue: tidy the docs", cwd=nested, env={"CLAUDE_PROJECT_DIR": self.project})
        self.assertEqual(self.task(1)["workdir"], "docs")

    def test_sessions_started_by_the_runner_are_never_captured(self):
        self.assertIsNone(self.hook("queue: must not be queued again", env={"MOA7AMED_QUEUE_RUNNER": "1"}))
        self.assertFalse(os.path.exists(os.path.join(self.home, "projects")))

    def test_prefix_can_be_changed_or_switched_off(self):
        self.assertIsNone(self.hook("queue: ignored now", env={"MOA7AMED_QUEUE_CAPTURE_PREFIX": "later>"}))
        decision = self.hook("later> clean up imports", env={"MOA7AMED_QUEUE_CAPTURE_PREFIX": "later>"})
        self.assertIn("Queued as task #1", decision["reason"])
        self.assertIsNone(self.hook("queue: off means off", env={"MOA7AMED_QUEUE_CAPTURE_PREFIX": "off"}))
        self.mq("config", "set", "capture_prefix", "todo:", "--global")
        self.assertIsNone(self.hook("queue: the settings file changed the prefix"))
        self.assertIn("Queued as task #2", self.hook("todo: from the settings file")["reason"])

    def test_empty_capture_is_blocked_with_an_explanation(self):
        decision = self.hook("queue:   ")
        self.assertEqual(decision["decision"], "block")
        self.assertIn("could NOT save", decision["reason"])
        self.assertEqual(self.queue().tasks(), [])

    @unittest.skipUnless(SH, "needs a POSIX shell (Git Bash on Windows)")
    def test_shell_wrapper_used_by_claude_code(self):
        def run(prompt, env=None):
            full_env = dict(os.environ)
            full_env.update(env or {})
            result = subprocess.run([SH, HOOK_SCRIPT], input=self.payload(prompt),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.decode("utf-8").strip()

        os.remove(os.path.join(self.home, "config.json"))  # exercise the no-Python fast path
        self.assertEqual(run("just a normal prompt"), "")
        self.assertEqual(run("queue: skipped inside the runner", {"MOA7AMED_QUEUE_RUNNER": "1"}), "")
        self.assertEqual(self.queue().tasks(), [])
        decision = json.loads(run("Queue: captured through the shell wrapper"))
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(self.task(1)["prompt"], "captured through the shell wrapper")
        self.assertIn("Queued as task #2",
                      run("later: custom prefix", {"MOA7AMED_QUEUE_CAPTURE_PREFIX": "later:"}))

    @unittest.skipUnless(SH, "needs a POSIX shell (Git Bash on Windows)")
    def test_saved_settings_do_not_cost_an_interpreter_start_on_every_prompt(self):
        # Stand-in for Python: it only leaves a mark, so the test can tell whether the
        # hook started an interpreter for a prompt it had no reason to look at.
        marker = os.path.join(self.tmp, "interpreter-was-started").replace("\\", "/")
        sentinel = self.write("sentinel.sh",
                              "\n".join(["#!/bin/sh", 'echo started > "%s"' % marker,
                                         "cat > /dev/null", ""]), root=self.tmp)
        os.chmod(sentinel, 0o755)
        env = dict(os.environ, MOA7AMED_QUEUE_PYTHON=sentinel.replace("\\", "/"))

        def run(prompt):
            result = subprocess.run([SH, HOOK_SCRIPT], input=self.payload(prompt), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)

        self.set_config(max_attempts=3, cost_warn_task_usd=1.0)   # unrelated to the prefix
        run("just a normal prompt")
        self.assertFalse(os.path.exists(marker))
        run("queue: this one is ours")
        self.assertTrue(os.path.exists(marker))
        os.remove(marker)
        self.set_config(capture_prefix="later:")   # now only Python can compare the prefix
        run("just a normal prompt")
        self.assertTrue(os.path.exists(marker))

    def test_the_restored_prompt_is_not_queued_twice(self):
        # Claude Code (2.1.329) puts a blocked prompt back into the input box. Enter again
        # resubmits the same text; typing on appends to it.
        first = self.hook("queue: write the release notes")
        self.assertIn("Queued as task #1", first["reason"])
        self.assertIn("press Esc twice", first["reason"])
        again = self.hook("queue: write the release notes")
        self.assertEqual(again["decision"], "block")
        self.assertIn("NOT queued again", again["reason"])
        self.assertIn("task #1", again["reason"])
        extended = self.hook("queue: write the release notesAlso fix the typo")
        self.assertEqual(extended["decision"], "block")
        self.assertIn("NOT queued", extended["reason"])
        self.assertIn('"Also fix the typo"', extended["reason"])
        self.assertEqual([task["prompt"] for task in self.queue().tasks()],
                         ["write the release notes"])
        # After clearing the input box, new text is queued as usual.
        self.assertIn("Queued as task #2", self.hook("queue: also fix the typo")["reason"])

    def test_the_same_request_from_another_session_is_queued(self):
        self.hook("queue: write the release notes")
        other = self.hook("queue: write the release notes", session="session-456")
        self.assertIn("Queued as task #2", other["reason"])
        self.assertEqual(len(self.queue().tasks()), 2)


if __name__ == "__main__":
    unittest.main()
