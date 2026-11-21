"""Challenge, discuss, then execute: evidence-backed concerns, the owner's decisions, and the
limits a decision cannot lift. Everything runs against the fake CLI."""
import json
import os
import subprocess
import sys
import unittest

from helpers import MQ, ROOT, SH, QueueTestCase

from mqlib import challenge, engine, util
from mqlib.util import QueueError

HOOK_SCRIPT = os.path.join(ROOT, "hooks", "capture-prompt.sh")
TYPO = "Fix the typo in the README heading"
FEATURE = "Add a --tag option to the notes list command"
DROP = "Drop the users table and recreate it with an email column"
DEPLOY = "Deploy the new build to production"
SECRET = "Hard-code the API key in config.py so the client starts without setup"
NO_TESTS = "Add a login form without tests"
DEEP = "Redesign the enterprise deployment architecture and move the database to a cluster"
PROCEED = "I know the risk; go ahead with my original plan."
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


class ChallengeCase(QueueTestCase):

    def setUp(self):
        QueueTestCase.setUp(self)
        for name in SESSION_VARS:          # by default a command is the owner's terminal
            os.environ.pop(name, None)
        self.set_config(challenge_mode="adaptive")

    def run_here(self, **options):
        from mqlib import pyrunner
        flags = {key: options.pop(key) for key in list(options)
                 if key in ("max_tasks", "stop_on_failure")}
        return pyrunner.run(self.queue(), options, quiet=True, **flags)

    def concerns(self, task_id, status=None):
        rows = challenge.for_task(self.queue(), task_id)
        return [row for row in rows if status is None or row["status"] == status]

    def hold(self, task_id):
        return self.queue().model_block(task_id) or ""

    def starts(self, task_id=None):
        return [event for event in self.events_of("start")
                if task_id is None or event["task"] == task_id]

    def decide(self, *args, env=None, check=True):
        full = dict(os.environ)
        full.update(env or {})
        command = [sys.executable, MQ, "--project", self.project, "challenge", "decide"] + list(args)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full)
        out, err = result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        if check and result.returncode != 0:
            self.fail("decide failed: %s %s" % (out, err))
        return result.returncode, out, err

    def hook(self, prompt, env=None):
        full = dict(os.environ)
        full.update(env or {})
        payload = json.dumps({"session_id": "s-1", "cwd": self.project, "prompt": prompt,
                              "hook_event_name": "UserPromptSubmit"}).encode("utf-8")
        result = subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full,
                                cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def gate(self, task_id):
        q = self.queue()
        return challenge.gate(q, q.get(task_id), util.effective_config(q.dir))


class EvidenceBacked(ChallengeCase):

    def test_a_concrete_risk_is_raised_with_its_evidence_and_holds_only_that_task(self):
        drop, typo = self.add(DROP), self.add(TYPO)
        self.script(typo, "write README.md fixed")
        self.run_here()
        rows = self.concerns(drop)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["rule"], row["kind"], row["severity"], row["status"]),
                         ("destructive-data", "recommendation", "material", "open"))
        self.assertIn('"Drop the users table"', row["evidence"])     # the task's own words
        self.assertTrue(row["impact"] and row["recommendation"])
        self.assertTrue(self.hold(drop).startswith("decision:"))
        self.assertEqual(self.statuses(), {drop: "pending", typo: "done"})
        self.assertEqual([e["task"] for e in self.starts()], [typo])  # nothing ran for #1
        status = self.mq("status")[1]
        self.assertIn("[decision: data would be deleted", status)
        self.assertNotIn("no usable model", status)

    def test_each_rule_names_a_risk_not_a_preference(self):
        config = util.effective_config(self.queue().dir)
        found = {item["rule"] for text in (DROP, DEPLOY, SECRET, NO_TESTS,
                                           "Disable SSL certificate verification for the API "
                                           "client", "Force-push the cleaned branch",
                                           "Silence the warnings in the build")
                 for item in challenge.check_text(text, config)}
        self.assertEqual(found, {"destructive-data", "production-publish", "secret-in-code",
                                 "skip-verification", "security-off", "history-rewrite",
                                 "workaround"})
        for text in ("Rewrite the parser in Rust because it is nicer", "Use React instead of "
                     "Vue", "Switch the project to tabs", "Refactor the store into classes"):
            self.assertEqual(challenge.check_text(text, config), [], text)

    def test_arabic_requests_are_checked_too(self):
        config = util.effective_config(self.queue().dir)
        found = challenge.check_text(u"احذف كل البيانات من جدول المستخدمين", config)
        self.assertEqual([item["rule"] for item in found], ["destructive-data"])
        found = challenge.check_text(u"خزن كلمات المرور بدون تشفير", config)
        self.assertEqual([item["rule"] for item in found], ["secret-in-code"])

    def test_a_step_the_task_cannot_do_is_a_restriction_not_a_recommendation(self):
        deploy = self.add(DEPLOY)
        self.gate(deploy)
        row = self.concerns(deploy)[0]
        self.assertEqual(row["kind"], "restriction")
        self.assertIn("cannot do this step", row["impact"])
        self.assertIn("acknowledge it", self.hold(deploy))
        # with commands granted by the owner it becomes an ordinary recommendation
        self.set_config(allowed_tools=["Bash(git push:*)"])
        config = util.effective_config(self.queue().dir)
        self.assertEqual(challenge.check_text(DEPLOY, config)[0]["kind"], "recommendation")


class RoutineWork(ChallengeCase):

    def test_routine_tasks_run_without_any_discussion(self):
        typo, feature = self.add(TYPO), self.add(FEATURE)
        self.script(typo, "write README.md fixed")
        self.script(feature, "write cli.py tag")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.statuses(), {typo: "done", feature: "done"})
        self.assertEqual(self.concerns(typo) + self.concerns(feature), [])
        prompt = self.starts(typo)[0]["prompt"]
        self.assertNotIn("<owner_decisions>", prompt)
        self.assertIn("Never stop for style, framework or architecture preferences", prompt)

    def test_a_minor_concern_is_an_advisory_and_the_task_continues(self):
        task_id = self.add(NO_TESTS)
        _, out, _ = self.mq("show", str(task_id))
        self.script(task_id, "write login.html form")
        self.run_here()
        self.assertEqual(self.task(task_id)["status"], "done")
        rows = self.concerns(task_id)
        self.assertEqual([(r["rule"], r["status"]) for r in rows], [("skip-verification", "advisory")])
        self.assertIn("<advisories>", self.starts(task_id)[0]["prompt"])
        self.assertEqual(len(self.starts(task_id)), 1)

    def test_add_reports_concerns_at_once(self):
        _, out, _ = self.mq("add", DROP)
        self.assertIn("concern C1: data would be deleted", out)
        self.assertIn("challenge decide C1 --recommendation", out)
        _, out, _ = self.mq("add", NO_TESTS)
        self.assertIn("advisory: tests or checks would be skipped (no decision needed)", out)
        _, out, _ = self.mq("add", DEPLOY)
        self.assertIn("limit C3", out)
        self.assertIn("the step stays out of the task", out)


class OwnerAuthority(ChallengeCase):

    def test_rejecting_the_recommendation_is_followed_without_substitution(self):
        drop = self.add(DROP)
        self.gate(drop)
        _, out, _ = self.decide(str(drop), "--said", PROCEED)
        self.assertIn("proceed with the original approach", out)
        row = self.concerns(drop)[0]
        self.assertEqual((row["status"], row["decision"], row["decided_by"]),
                         ("decided", "original", "terminal"))
        self.assertEqual(row["response"], PROCEED)
        self.assertTrue(row["tradeoff"])
        self.assertTrue(row["decided_at"])
        self.script(drop, "write migration.sql drop")
        self.run_here()
        self.assertEqual(self.task(drop)["status"], "done")
        prompt = self.starts(drop)[0]["prompt"]
        self.assertIn(DROP, prompt)                         # the request itself, unchanged
        self.assertIn("the owner chose the original approach", prompt)
        self.assertIn("do not raise them again or substitute another approach", prompt)
        self.assertNotIn("the owner chose the recommendation", prompt)

    def test_taking_the_recommendation_is_handed_to_the_task(self):
        drop = self.add(DROP)
        self.gate(drop)
        self.decide("C1", "--recommendation")
        self.run_here()
        prompt = self.starts(drop)[0]["prompt"]
        self.assertIn("the owner chose the recommendation: take a backup", prompt)

    def test_natural_language_decisions(self):
        cases = {
            PROCEED: "original",
            "Go ahead with my approach anyway, I know the trade-offs.": "original",
            "Keep my approach.": "original",
            "Ignore the warning and do it as I asked": "original",
            "OK, go with your recommendation.": "recommendation",
            "Accept the recommendation": "recommendation",
            u"أنا فاهم المخاطر، كمل زي ما قلت": "original",
            u"موافق على التوصية": "recommendation",
            "Should I proceed with the original approach?": None,
            "Don't proceed with the original approach.": None,
            "What does this function do": None,
            "Use the recommendation, or keep my approach, whichever": None,
        }
        for text, expected in cases.items():
            self.assertEqual(challenge.classify_decision(text), expected, text)

    def test_an_accepted_warning_is_not_raised_again(self):
        drop = self.add(DROP)
        self.gate(drop)
        self.decide("C1", "--original")
        self.script(drop, "fail-once flaky", "write migration.sql drop")
        self.run_here()
        self.assertEqual(self.task(drop)["status"], "done")
        self.assertEqual(len(self.starts(drop)), 2)            # retried, never held again
        self.assertEqual(len(self.concerns(drop)), 1)
        for _ in range(3):
            self.assertIsNone(self.gate(drop))
        self.assertEqual(len(self.concerns(drop)), 1)
        raised = self.queue().conn.execute("SELECT COUNT(*) AS n FROM events WHERE task_id=? "
                                           "AND type='challenge'", (drop,)).fetchone()["n"]
        self.assertEqual(raised, 1)                            # asked once, ever

    def test_new_material_evidence_reopens_the_discussion(self):
        task_id = self.add(FEATURE)
        self.script(task_id, "concern-always the export overwrites user files | evidence: "
                             "store.py:40 writes to the target path")
        self.run_here()
        first = self.concerns(task_id, "open")
        self.assertEqual(len(first), 1)
        self.decide("C%d" % first[0]["id"], "--original")
        # the same concern on the same evidence: not asked again (the run itself fails)
        self.run_here(max_tasks=1)
        self.assertEqual(len(self.concerns(task_id)), 1)
        # new evidence for the same concern: the question is open again
        with open(os.environ["FAKE_SCRIPT"], "w") as handle:
            json.dump({str(task_id): ["concern-always the export overwrites user files | "
                                      "evidence: cli.py:12 also deletes the backup copy"]}, handle)
        self.run_here(max_tasks=1)
        reopened = self.concerns(task_id, "open")
        self.assertEqual(len(reopened), 1)
        self.assertEqual(reopened[0]["reopens"], first[0]["id"])
        self.assertTrue(self.hold(task_id).startswith("decision:"))


class OnlyTheOwnerDecides(ChallengeCase):

    def test_queued_tasks_and_agents_cannot_decide(self):
        drop = self.add(DROP)
        self.gate(drop)
        for env in ({"MOA7AMED_QUEUE_RUNNER": "1"}, {"MOA7AMED_QUEUE_TASK_ID": str(drop)},
                    {"MOA7AMED_QUEUE_AGENT": "1"}, {"CTQ_RUNNER": "1"}):
            code, _, err = self.decide("C1", "--original", env=env, check=False)
            self.assertNotEqual(code, 0, env)
            self.assertIn("cannot make decisions for the owner", err)
        self.assertEqual(self.concerns(drop)[0]["status"], "open")

    def test_a_claude_session_needs_the_owners_own_recorded_words(self):
        drop = self.add(DROP)
        self.gate(drop)
        session = {"CLAUDECODE": "1"}
        code, _, err = self.decide("C1", "--original", env=session, check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("cannot speak for the owner on its own", err)
        # words the owner never typed do not count
        code, _, _ = self.decide("C1", "--said", PROCEED, env=session, check=False)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.concerns(drop)[0]["status"], "open")
        # what the owner typed, recorded by the hook, does
        q = self.queue()
        statement = challenge.capture_statement(q, PROCEED, "s-1", "original")
        _, out, _ = self.decide("C1", "--statement", str(statement), env=session)
        row = self.concerns(drop)[0]
        self.assertEqual((row["decision"], row["decided_by"], row["statement_id"]),
                         ("original", "owner-prompt", statement))
        # and it is used once
        self.add(DROP + " again")
        self.gate(2)
        code, _, _ = self.decide("2", "--statement", str(statement), env=session, check=False)
        self.assertNotEqual(code, 0)

    def test_the_session_cannot_contradict_the_owners_words(self):
        drop = self.add(DROP)
        self.gate(drop)
        statement = challenge.capture_statement(self.queue(), PROCEED, "s-1", "original")
        code, _, err = self.decide("C1", "--recommendation", "--statement", str(statement),
                                   env={"CLAUDECODE": "1"}, check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("read as 'original'", err)

    def test_the_owners_prompt_decides_through_the_hook_and_still_reaches_claude(self):
        drop = self.add(DROP)
        self.gate(drop)
        self.assertTrue(os.listdir(os.path.join(self.home, "open-decisions")))
        self.assertIsNone(self.hook("Explain the migration first"))     # not a decision
        answer = self.hook(PROCEED)
        self.assertNotIn("decision", answer)                            # never blocked
        context = answer["hookSpecificOutput"]["additionalContext"]
        self.assertIn("recorded the owner's decision", context)
        self.assertIn("C1, task #%d" % drop, context)
        row = self.concerns(drop)[0]
        self.assertEqual((row["decision"], row["decided_by"]), ("original", "owner-prompt"))
        self.assertEqual(self.hold(drop), "")
        self.assertEqual(os.listdir(os.path.join(self.home, "open-decisions")), [])
        # nothing open any more: the same words are just a message
        self.assertIsNone(self.hook(PROCEED))

    def test_an_ambiguous_prompt_is_recorded_but_decides_nothing(self):
        first, second = self.add(DROP), self.add(SECRET)
        self.gate(first)
        self.gate(second)
        answer = self.hook(PROCEED)
        context = answer["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Ask which task", context)
        self.assertEqual(len(challenge.open_blocking(self.queue())), 2)
        # naming the task decides it
        answer = self.hook("For task #%d: go with your recommendation." % second)
        self.assertIn("follow the recommendation", answer["hookSpecificOutput"]["additionalContext"])
        self.assertEqual([row["task_id"] for row in challenge.open_blocking(self.queue())], [first])

    def test_a_task_session_never_feeds_the_hook(self):
        drop = self.add(DROP)
        self.gate(drop)
        self.assertIsNone(self.hook(PROCEED, env={"MOA7AMED_QUEUE_RUNNER": "1"}))
        self.assertEqual(self.concerns(drop)[0]["status"], "open")

    @unittest.skipUnless(SH, "needs sh")
    def test_the_shell_hook_reads_ordinary_prompts_only_while_a_decision_is_open(self):
        def shell(prompt):
            payload = json.dumps({"session_id": "s", "cwd": self.project,
                                  "prompt": prompt}).encode("utf-8")
            result = subprocess.run([SH, HOOK_SCRIPT], input=payload, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=self.tmp)
            return result.stdout.decode("utf-8").strip()
        drop = self.add(DROP)
        self.assertEqual(shell(PROCEED), "")              # nothing open: not even Python
        self.gate(drop)
        self.assertIn("recorded the owner's decision", shell(PROCEED))


class LimitsStay(ChallengeCase):

    def test_accepting_a_risk_does_not_lift_a_restriction(self):
        deploy = self.add(DEPLOY)
        self.gate(deploy)
        _, out, _ = self.decide("C1", "--said", PROCEED)
        self.assertIn("acknowledged", out)
        row = self.concerns(deploy)[0]
        self.assertEqual(row["decision"], "acknowledged")
        self.run_here()
        start = self.starts(deploy)[0]
        self.assertIn("Do not try this step and do not work around the limit", start["prompt"])
        argv = start["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("bypassPermissions", argv)

    def test_the_plan_approval_of_a_deep_task_still_waits(self):
        deep = self.add(DEEP + ". Drop the legacy sessions table.")
        self.gate(deep)
        self.decide("C1", "--original")
        self.run_here()
        self.assertEqual([(e["phase"]) for e in self.starts(deep)], ["plan"])
        self.assertTrue(self.hold(deep).startswith("approval:"))
        self.mq("workflow", "approve", str(deep))
        self.run_here()
        self.assertEqual(self.task(deep)["status"], "done")
        self.assertIn("the owner chose the original approach", self.starts(deep)[1]["prompt"])

    def test_concerns_found_while_planning_wait_together_with_the_approval(self):
        deep = self.add(DEEP)
        self.script(deep, "concern the cluster move loses the session store | evidence: "
                          "sessions are kept in local memory (server.py:88)")
        self.run_here()
        self.assertTrue(self.hold(deep).startswith("approval:"))
        self.assertEqual(len(self.concerns(deep, "open")), 1)
        self.mq("workflow", "approve", str(deep))
        self.run_here()                                 # approved, but a decision is open
        self.assertTrue(self.hold(deep).startswith("decision:"))
        self.assertEqual(len(self.starts(deep)), 1)
        self.decide(str(deep), "--recommendation")
        self.run_here()
        self.assertEqual(self.task(deep)["status"], "done")

    def test_the_cost_limits_are_unchanged(self):
        self.set_config(max_budget_usd=0.5)
        drop = self.add(DROP)
        self.gate(drop)
        self.decide("C1", "--original")
        self.run_here()
        argv = self.starts(drop)[0]["argv"]
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.5")

    def test_the_round_limit_turns_later_concerns_into_advisories(self):
        task_id = self.add(FEATURE)
        self.script(task_id, "concern the API returns user emails | evidence: api.py:20")
        self.run_here()
        self.decide(str(task_id), "--original")
        with open(os.environ["FAKE_SCRIPT"], "w") as handle:
            json.dump({str(task_id): ["concern-always another worry | evidence: x.py:1"]}, handle)
        self.run_here(max_tasks=1)
        later = [row for row in self.concerns(task_id) if row["concern"] == "another worry"]
        self.assertEqual(later[0]["status"], "advisory")
        self.assertIn("challenge_max_rounds", later[0]["note"])
        self.assertFalse(self.hold(task_id).startswith("decision:"))


class Plans(ChallengeCase):

    def test_a_concern_about_a_plan_holds_its_approval_until_the_owner_decides(self):
        from test_planning import PlanCase, ready_plan
        self.make = PlanCase.make.__get__(self)   # the planning fixture, on this test case
        ready_plan(self, "shop")
        self.mq("challenge", "raise", "--plan", "shop", "--concern",
                "the sign-up stores card numbers", "--evidence", "requirement R1 keeps the card "
                "for later charges", "--recommendation", "keep a payment provider token instead")
        row = challenge.open_for_plan(self.queue(), "shop")[0]
        self.assertEqual((row["task_id"], row["plan_ref"]), (None, "shop"))
        self.assertTrue(row["revision"].startswith("p:shop@"))
        preview = self.mq("project", "approve", "shop")[1]
        self.assertIn("wait for your decision", preview)
        code, _, err = self.mq("project", "approve", "shop", "--yes", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("decide first", err)
        _, out, _ = self.decide("C%d" % row["id"], "--recommendation")
        self.assertIn("plan shop", out)
        self.assertIn("Approved for execution", self.mq("project", "approve", "shop", "--yes")[1])


class StopAndResume(ChallengeCase):

    def test_a_stopped_task_resumes_once_without_duplicate_work(self):
        task_id = self.add(FEATURE)
        self.script(task_id, "concern the --tag option clashes with --tags in v1 | evidence: "
                             "cli.py:31 already defines --tags", "write cli.py tag")
        self.run_here()
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("pending", 0))
        self.assertTrue(self.hold(task_id).startswith("decision:"))
        self.assertEqual(task["runs"][-1]["outcome"], "decision")
        self.assertFalse(os.path.exists(os.path.join(self.project, "cli.py")))
        self.run_here()                                  # still waiting: nothing starts
        self.assertEqual(len(self.starts(task_id)), 1)
        self.decide(str(task_id), "--original", "--tradeoff", "v1 users must rename the flag")
        self.run_here()
        self.run_here()
        task = self.task(task_id)
        self.assertEqual((task["status"], len(self.starts(task_id))), ("done", 2))
        commits = self.git("log", "--oneline", cwd=self.project).strip().splitlines()
        self.assertEqual(len([line for line in commits if "#%d" % task_id in line]), 1)
        self.assertIn("v1 users must rename the flag", self.starts(task_id)[1]["prompt"])

    def test_decisions_survive_a_restart(self):
        drop = self.add(DROP)
        self.gate(drop)
        self.decide("C1", "--original")
        # a new process and a fresh connection see the same decision
        data = self.mq_json("challenge", "show", str(drop))
        self.assertEqual((data[0]["status"], data[0]["decision"]), ("decided", "original"))
        self.assertIsNone(self.gate(drop))
        self.assertEqual(self.mq_json("challenge", "show", "C1")[0]["revision"],
                         challenge.task_revision(self.queue().get(drop)))


class Compatibility(ChallengeCase):

    def test_explicit_mode_is_the_default_and_raises_nothing_by_itself(self):
        self.set_config(challenge_mode=None)
        config = util.effective_config(self.queue().dir)
        self.assertEqual(config["challenge_mode"], "explicit")
        drop = self.add(DROP)
        self.script(drop, "write migration.sql drop")
        self.run_here()
        self.assertEqual(self.task(drop)["status"], "done")
        self.assertEqual(self.concerns(drop), [])
        self.assertNotIn("<concerns>", self.starts(drop)[0]["prompt"])
        # a review the owner asks for still works, and holds the task
        other = self.add(DROP + " now")
        _, out, _ = self.mq("challenge", "review", str(other))
        self.assertIn("held until you decide", out)
        self.assertTrue(self.hold(other).startswith("decision:"))

    def test_off_releases_optional_holds_but_keeps_the_approval_gate(self):
        drop, deep = self.add(DROP), self.add(DEEP)
        self.gate(drop)
        self.set_config(challenge_mode="off")
        self.script(drop, "write migration.sql drop")
        self.run_here()
        self.assertEqual(self.task(drop)["status"], "done")
        self.assertEqual(self.concerns(drop)[0]["status"], "withdrawn")
        self.assertTrue(self.hold(deep).startswith("approval:"))
        self.assertNotIn("<concerns>", self.starts(drop)[0]["prompt"])

    def test_retry_and_unblock_do_not_get_past_a_decision(self):
        from mqlib import routing
        drop = self.add(DROP)
        self.gate(drop)
        with self.assertRaises(QueueError):
            self.queue().retry([drop])
        routing.unblock(self.queue())
        self.assertTrue(self.hold(drop).startswith("decision:"))
        self.mq("models", "--reset")
        self.run_here()
        self.assertEqual(self.starts(drop), [])

    def test_a_raised_concern_needs_evidence(self):
        task_id = self.add(FEATURE)
        code, _, err = self.mq("challenge", "raise", str(task_id), "--concern", "risky",
                               "--evidence", " ", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("needs its evidence", err)
        self.mq("challenge", "raise", str(task_id), "--concern", "the flag name is taken",
                "--evidence", "cli.py:31 defines --tags", "--recommendation", "call it --label")
        self.assertTrue(self.hold(task_id).startswith("decision:"))


if __name__ == "__main__":
    unittest.main()
