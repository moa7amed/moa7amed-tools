"""Automatic model routing, end to end against the fake CLI: every task runs on its own
model, overrides, unavailable models, escalation, phases, and queues from earlier versions."""
import json
import os
import sqlite3
import unittest

from helpers import QueueTestCase, runner_variants

from mqlib import router

DOCS = "Fix the typo in the README title.\nFAKE: write README.md fixed"
FEATURE = "Add a search box with filtering to the customers page.\nFAKE: write search.txt done"
HARD = "Fix the deadlock in the payment worker.\nFAKE: write worker.txt fixed"
BASE_ARGS = ["-p", "--output-format", "json", "--permission-mode", "acceptEdits"]


class RoutingCase(QueueTestCase):

    def run_here(self, **options):
        """Run the queue in this process with the portable runner. Returns its exit code."""
        from mqlib import pyrunner
        flags = {key: options.pop(key) for key in list(options)
                 if key in ("max_tasks", "stop_on_failure")}
        return pyrunner.run(self.queue(), options, quiet=True, **flags)

    def history_types(self, task_id):
        return [entry.get("type", "run") for entry in self.routing(task_id)["executionModelHistory"]]


class Classification(RoutingCase):

    def test_task_is_classified_when_it_enters_the_queue(self):
        code, out, _ = self.mq("add", "--stdin", stdin=DOCS.encode())
        self.assertIn("model: haiku", out)
        self.mq("add", "--stdin", stdin=FEATURE.encode())
        self.mq("add", "--stdin", stdin=HARD.encode())
        expected = {1: "haiku", 2: "sonnet", 3: "opus"}
        for task_id, model in expected.items():
            task = self.mq_json("show", str(task_id))
            routing = task["routing"]
            self.assertEqual(routing["recommendedModel"], model)
            self.assertEqual(routing["selectedModel"], model)
            self.assertEqual(routing["modelSelectionMode"], "auto")
            self.assertFalse(routing["manualOverride"])
            self.assertIsNone(routing["actualModel"])
            self.assertEqual(routing["escalationCount"], 0)
            self.assertEqual(routing["executionModelHistory"], [])
            self.assertEqual(routing["routingPolicyVersion"], router.VERSION + "/balanced")
            self.assertTrue(routing["classificationTimestamp"])
            self.assertTrue(routing["modelReason"])
            self.assertIsInstance(routing["complexityScore"], int)
        # The original prompt is untouched by classification.
        self.assertEqual(self.task(3)["prompt"], HARD)

    def test_capture_hook_classifies_too(self):
        import subprocess
        import sys
        from helpers import MQ
        payload = json.dumps({"cwd": self.project, "session_id": "s",
                              "prompt": "queue: Fix the memory leak in the image cache."})
        subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload.encode(),
                       stdout=subprocess.PIPE, check=True)
        self.assertEqual(self.routing(1)["selectedModel"], "opus")

    def test_decisions_persist_and_survive_a_policy_change(self):
        refactor = ("Refactor the order service and its repository to remove the duplicated "
                    "validation logic while keeping backward compatibility for existing clients.")
        self.add(refactor)
        self.add(HARD)
        before = [self.routing(i) for i in (1, 2)]
        self.set_config(routing_mode="quality_first")
        # A new process sees exactly what was stored, whatever the policy is now.
        after = [self.mq_json("show", str(i))["routing"] for i in (1, 2)]
        self.assertEqual(before, after)
        self.assertEqual(after[0]["selectedModel"], "sonnet")
        # Reclassifying is an explicit step.
        self.mq("route", "--reclassify")
        self.assertEqual(self.routing(1)["selectedModel"], "opus")
        self.assertEqual(self.routing(1)["routingPolicyVersion"], router.VERSION + "/quality_first")
        self.assertIn("reclassified", self.history_types(1))

    def test_settings_accept_the_documented_names(self):
        self.mq("config", "set", "modelRouting.defaultMode", "Quality First", "--global")
        self.mq("config", "set", "modelRouting.maxEscalations", "3")
        self.mq("config", "set", "modelRouting.enabled", "false")
        config = self.mq_json("config")
        self.assertEqual((config["routing_mode"], config["routing_max_escalations"],
                          config["routing_enabled"]), ("quality_first", 3, False))
        code, _, err = self.mq("config", "set", "routing_mode", "cheapest", check=False)
        self.assertEqual(code, 1)

    def test_status_and_report_show_the_routing(self):
        self.add(DOCS)
        self.add(HARD)
        self.mq("model", "sonnet", "1")
        status = self.mq("status")[1]
        self.assertIn("MODEL", status)
        self.assertRegex(status, r"sonnet\*\s+Fix the typo")
        self.assertRegex(status, r"opus\s+Fix the deadlock")
        self.assertIn("automatic, balanced policy", status)
        self.assertEqual(self.run_here(), 0)
        report = self.mq("route", "--all")[1]
        for expected in ("Task #1 - Fix the typo in the README title.",
                         "Model:       sonnet (manual override)", "Recommended: haiku",
                         "Reason:      Small text change", "Override:    yes, set manually",
                         "Actual:      claude-sonnet-test-1", "Escalations: 0",
                         "Usage:       $0.0123 over 1 run(s), as reported by the CLI",
                         "Status:      done", "Model:       opus (automatic)",
                         "Actual:      claude-opus-test-1"):
            self.assertIn(expected, report)
        self.assertIn("asked for sonnet, ran on claude-sonnet-test-1 (confirmed)",
                      self.mq("show", "1")[1])


class PerTaskModel(object):
    """Runs through the real runner processes (Python and PowerShell)."""

    def test_each_task_runs_on_its_own_model_in_queue_order(self):
        q = self.queue()
        docs = q.add(DOCS)
        hard = q.add(HARD, priority=10)
        feature = q.add(FEATURE)
        urgent_docs = q.add("Update the CHANGELOG.\nFAKE: write CHANGELOG.md x", priority=20)
        code, output = self.run_queue()
        self.assertEqual(code, 0, output)
        # Order is decided by priority and age only; the model plays no part in it.
        self.assertEqual(self.starts(), [(urgent_docs, "haiku"), (hard, "opus"),
                                         (docs, "haiku"), (feature, "sonnet")])
        events = self.fake_events()
        self.assertEqual([event["event"] for event in events], ["start", "end"] * 4)
        for event in events:
            if event["event"] == "start":
                # The model is one separate argument; the prompt is never on the command line.
                self.assertEqual(event["argv"], BASE_ARGS + ["--model", event["model"]])
                self.assertNotIn("FAKE:", " ".join(event["argv"]))
        for task_id, model in ((docs, "haiku"), (hard, "opus"), (feature, "sonnet")):
            task = self.task(task_id)
            self.assertEqual(task["status"], "done")
            self.assertEqual(task["routing"]["actualModel"], "claude-%s-test-1" % model)
            run = task["routing"]["runs"][0]
            self.assertEqual((run["requested"], run["verified"]), (model, "confirmed"))
            self.assertEqual(run["usage"]["cost_usd"], 0.0123)
            self.assertEqual(run["usage"]["models"]["claude-%s-test-1" % model]["outputTokens"], 20)
        self.assertIn("[model: opus]", output)
        self.assertIn("ran on claude-opus-test-1", output)

    def test_model_option_of_a_run_applies_to_that_run_only(self):
        q = self.queue()
        q.add(DOCS)
        q.add(HARD)
        code, output = self.run_queue(model="sonnet")
        self.assertEqual(code, 0, output)
        self.assertEqual([model for _, model in self.starts()], ["sonnet", "sonnet"])
        # What was decided for each task is still there for the next run.
        self.assertEqual([self.routing(i)["selectedModel"] for i in (1, 2)], ["haiku", "opus"])

    def test_routing_can_be_switched_off_for_a_run(self):
        self.add(HARD)
        code, output = self.run_queue(no_routing=True)
        self.assertEqual(code, 0, output)
        start = self.events_of("start")[0]
        self.assertEqual(start["argv"], BASE_ARGS)
        self.assertEqual(self.task(1)["routing"]["actualModel"], "claude-default-test-1")

    def test_running_task_keeps_its_model(self):
        slow = self.add("Fix the deadlock in the payment worker.\nFAKE: sleep 6")
        other = self.add(DOCS)
        runner = self.start_queue()
        self.wait_for(lambda: self.statuses()[slow] == "running", message="the task to start")
        # Neither an override nor a reclassification may touch the task that is running.
        code, _, err = self.mq("model", "haiku", str(slow), check=False)
        self.assertEqual(code, 1)
        self.assertIn("is running", err)
        self.mq("route", "--reclassify")
        self.mq("model", "sonnet", str(other))  # a waiting task can still be changed
        self.assertEqual(runner.wait(timeout=180), 0)
        self.assertEqual(self.starts(), [(slow, "opus"), (other, "sonnet")])  # started once each
        task = self.task(slow)
        self.assertEqual((task["status"], task["attempts"]), ("done", 1))
        self.assertEqual(task["routing"]["selectedModel"], "opus")
        self.assertEqual(len(task["runs"]), 1)


globals().update(runner_variants(PerTaskModel))


class ManualOverride(RoutingCase):

    def test_override_one_task_and_return_it_to_automatic(self):
        docs, hard = self.add(DOCS), self.add(HARD)
        code, out, _ = self.mq("model", "opus", str(docs))
        self.assertIn("manual override", out)
        routing = self.routing(docs)
        self.assertEqual((routing["modelSelectionMode"], routing["selectedModel"],
                          routing["recommendedModel"], routing["manualOverride"]),
                         ("manual", "opus", "haiku", True))
        self.assertEqual(self.routing(hard)["modelSelectionMode"], "auto")
        self.mq("model", "auto", str(docs))
        routing = self.routing(docs)
        self.assertEqual((routing["modelSelectionMode"], routing["selectedModel"],
                          routing["manualOverride"]), ("auto", "haiku", False))
        self.assertEqual(self.history_types(docs), ["override", "override"])

    def test_override_is_used_and_recorded(self):
        docs = self.add(DOCS)
        named = self.add(FEATURE, model="claude-custom-model-7")
        self.mq("add", "Update the docs.", "--model", "sonnet")
        self.mq("model", "opus", str(docs))
        self.assertEqual(self.run_here(), 0)
        self.assertEqual([model for _, model in self.starts()],
                         ["opus", "claude-custom-model-7", "sonnet"])
        self.assertEqual(self.routing(named)["actualModel"], "claude-custom-model-7")
        # No setting file was touched by the overrides.
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "claude-config")))

    def test_override_for_every_pending_task_or_the_whole_project(self):
        a, b = self.add(DOCS), self.add(HARD)
        self.mq("model", "sonnet", "--pending")
        self.assertEqual([self.routing(i)["selectedModel"] for i in (a, b)], ["sonnet", "sonnet"])
        self.mq("model", "auto", "--pending")
        code, out, _ = self.mq("model", "haiku", "--whole-project")
        self.assertIn("Claude Code's own default model was not changed", out)
        self.mq("model", "opus", str(b))  # a task's own override wins over the project's
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(a, "haiku"), (b, "opus")])
        self.mq("model", "auto", "--whole-project")
        self.assertIsNone(self.mq_json("config")["model"])
        with open(os.path.join(self.home, "config.json")) as handle:
            self.assertNotIn("model", json.load(handle))

    def test_unsafe_or_forbidden_overrides_are_rejected(self):
        task_id = self.add(DOCS)
        for bad in ("--dangerously-skip-permissions", "opus; rm -rf /", "a b", '"quoted"', ""):
            code, _, err = self.mq("model", "--", bad, str(task_id), check=False)
            self.assertNotEqual(code, 0, bad)
        self.assertEqual(self.routing(task_id)["selectedModel"], "haiku")
        code, _, err = self.mq("add", "another", "--model", "x y", check=False)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.queue().tasks()), 1)  # a bad model fails the add as a whole
        self.set_config(routing_allow_manual_override=False)
        code, _, err = self.mq("model", "opus", str(task_id), check=False)
        self.assertEqual(code, 1)
        self.assertIn("switched off", err)

    def test_tier_model_from_settings_is_validated_before_anything_starts(self):
        self.add(FEATURE)
        self.set_config(routing_model_sonnet="sonnet --dangerously-skip-permissions")
        code, out, err = self.mq("run", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("not a valid model name", out + err)
        self.assertEqual(self.fake_events(), [])
        self.assertEqual(self.task(1)["status"], "pending")

    def test_tiers_can_be_mapped_to_other_models(self):
        self.add(HARD)
        self.set_config(routing_model_opus="claude-opus-9-9")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(1, "claude-opus-9-9")])
        self.assertEqual(self.routing(1)["runs"][0]["verified"], "confirmed")


class UnavailableModels(RoutingCase):

    def test_refused_model_falls_back_without_running_the_task_twice(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        first, second = self.add(HARD), self.add("Fix the race condition in the cache.\n"
                                                 "FAKE: write cache.txt fixed")
        self.assertEqual(self.run_here(), 0)
        # opus was asked for once, refused, and never asked for again.
        self.assertEqual([event["model"] for event in self.events_of("refused")], ["opus"])
        self.assertEqual(self.starts(), [(first, "sonnet"), (second, "sonnet")])
        task = self.task(first)
        self.assertEqual((task["status"], task["attempts"]), ("done", 1))  # refusal is no attempt
        self.assertEqual([run["outcome"] for run in task["runs"]], ["model-unavailable", "done"])
        self.assertIn("There's an issue with the selected model", task["runs"][0]["error"])
        routing = task["routing"]
        self.assertEqual((routing["recommendedModel"], routing["selectedModel"],
                          routing["selectedBy"], routing["actualModel"]),
                         ("opus", "sonnet", "fallback", "claude-sonnet-test-1"))
        self.assertEqual(self.history_types(first), ["run", "fallback", "run"])
        self.assertEqual(len(self.git("log", "--format=%s").splitlines()), 3)  # one commit each
        # The second task was moved before it started, on the evidence of the first.
        self.assertEqual(self.history_types(second), ["fallback", "run"])
        report = self.mq("models")[1]
        self.assertRegex(report, r"opus\s+opus\s+unavailable")
        self.assertRegex(report, r"sonnet\s+sonnet\s+confirmed")
        self.assertRegex(self.mq("status")[1], r"opus>sonnet")

    def test_task_is_blocked_when_no_permitted_model_is_left(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus,sonnet"
        hard, docs = self.add(HARD), self.add(DOCS)
        self.assertEqual(self.run_here(), 1)
        # A deadlock fix is never handed to the smallest model: it waits instead.
        self.assertEqual(self.starts(), [(docs, "haiku")])
        task = self.task(hard)
        self.assertEqual((task["status"], task["state"], task["attempts"]),
                         ("pending", "blocked", 0))
        self.assertIn("no permitted model is available", task["routing"]["blockedReason"])
        self.assertIn("no usable model", self.mq("status")[1])
        self.assertIn("blocked because no permitted model is available", self.mq("log")[1])
        self.assertIn("BLOCKED:", self.mq("route", str(hard))[1])
        self.assertEqual(self.run_here(), 1)  # still blocked, and nothing is retried blindly
        self.assertEqual(len(self.events_of("refused")), 2)
        # Once a model is available again the block is lifted on request.
        os.environ["FAKE_UNAVAILABLE_MODELS"] = ""
        self.mq("models", "--reset")
        self.assertEqual(self.task(hard)["state"], "pending")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts()[-1], (hard, "opus"))

    def test_fallback_can_be_switched_off(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        self.set_config(routing_fallback_on_unavailable=False)
        hard = self.add(HARD)
        self.assertEqual(self.run_here(), 1)
        self.assertEqual(self.starts(), [])
        self.assertIn("refused", self.routing(hard)["blockedReason"])
        self.mq("retry", str(hard))  # retry clears the block; the cache still knows better
        self.assertEqual(self.task(hard)["state"], "pending")
        self.assertEqual(self.run_here(), 1)
        self.assertIn("fallback is switched off", self.routing(hard)["blockedReason"])
        self.assertEqual(len(self.events_of("refused")), 1)

    def test_manual_choice_is_never_replaced_by_another_model(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        task_id = self.add(DOCS, model="opus")
        self.assertEqual(self.run_here(), 1)
        self.assertEqual(self.starts(), [])
        task = self.task(task_id)
        self.assertEqual((task["state"], task["routing"]["selectedModel"]), ("blocked", "opus"))
        self.assertIn("manually selected model", task["routing"]["blockedReason"])
        self.mq("model", "auto", str(task_id))
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "haiku")])

    def test_run_wide_model_that_is_refused_stops_the_runner(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        self.add(DOCS)
        code = self.run_here(model="opus")
        self.assertEqual(code, 3)
        self.assertEqual(self.starts(), [])
        self.assertEqual((self.task(1)["status"], self.task(1)["attempts"]), ("pending", 0))
        self.assertIn("the model set for this run (opus) cannot be used", self.mq("log")[1])

    def test_restrictions_in_claude_code_settings_are_respected(self):
        folder = os.path.join(self.tmp, "claude-config")
        os.makedirs(folder)
        with open(os.path.join(folder, "settings.json"), "w") as handle:
            json.dump({"availableModels": ["sonnet", "haiku"]}, handle)
        hard = self.add(HARD)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(hard, "sonnet")])
        self.assertEqual(self.events_of("refused"), [])  # opus was never even requested
        self.assertIn("availableModels", json.dumps(self.routing(hard)["executionModelHistory"]))
        self.assertIn("availableModels: sonnet, haiku", self.mq("models")[1])

    def test_declared_model_list_in_the_plugin_settings(self):
        self.set_config(routing_available_models=["haiku", "sonnet"])
        hard = self.add(HARD)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(hard, "sonnet")])

    def test_silent_substitution_by_the_cli_is_detected(self):
        os.environ["FAKE_ACTUAL_MODEL"] = "claude-sonnet-test-1"
        first = self.add(HARD)
        self.assertEqual(self.run_here(), 0)
        routing = self.routing(first)
        self.assertEqual(routing["actualModel"], "claude-sonnet-test-1")
        self.assertEqual(routing["runs"][0]["verified"], "mismatch")
        self.assertIn("asked for opus but the CLI ran claude-sonnet-test-1", self.mq("log")[1])
        # From now on opus counts as unavailable, so the next task is moved openly.
        second = self.add(HARD)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts()[-1], (second, "sonnet"))
        self.assertEqual(self.routing(second)["selectedBy"], "fallback")

    def test_cli_without_model_selection_runs_on_its_default(self):
        os.remove(os.path.join(self.home, "models.json"))
        os.environ["FAKE_NO_MODEL_FLAG"] = "1"
        self.add(HARD)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.events_of("start")[0]["argv"], BASE_ARGS)
        self.assertIn("has no --model option", self.mq("log")[1])
        self.assertIn("this CLI has no --model option", self.mq("models")[1])

    def test_availability_report_and_probe(self):
        os.remove(os.path.join(self.home, "models.json"))
        report = self.mq("models")[1]
        self.assertIn("Claude Code  9.9.9", report)
        self.assertEqual(report.count("unknown      never tried"), 3)
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        data = self.mq_json("models", "--probe")
        self.assertEqual({entry["tier"]: entry["status"] for entry in data["tiers"]},
                         {"opus": "unavailable", "sonnet": "confirmed", "haiku": "confirmed"})
        self.assertTrue(data["cli"]["supports_model"])


class Escalation(RoutingCase):

    def test_repeated_failure_moves_the_task_to_a_stronger_model(self):
        task_id = self.add("Fix the typo in the README title.")
        self.script(task_id, "fail-on haiku the fix needs code changes", "write README.md fixed")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "haiku"), (task_id, "haiku"),
                                         (task_id, "sonnet")])
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"], task["max_attempts"]), ("done", 3, 3))
        routing = task["routing"]
        self.assertEqual((routing["escalationCount"], routing["selectedModel"],
                          routing["selectedBy"], routing["recommendedModel"]),
                         (1, "sonnet", "escalation", "haiku"))
        escalation = [e for e in routing["executionModelHistory"] if e.get("type") == "escalation"]
        self.assertEqual(len(escalation), 1)
        self.assertEqual((escalation[0]["from"], escalation[0]["to"]), ("haiku", "sonnet"))
        self.assertIn("2 failed attempts on haiku", escalation[0]["reason"])
        self.assertIn("the fix needs code changes", escalation[0]["reason"])
        # The stronger model is told what happened, and the earlier work was kept, not lost.
        prompt = self.events_of("start")[2]["prompt"]
        self.assertIn("<escalation>", prompt)
        self.assertIn("A smaller model (haiku) could not complete this task", prompt)
        self.assertEqual(len(self.git("stash", "list").splitlines()), 2)
        self.assertRegex(self.mq("status", "--json")[1], "escalation")

    def test_one_failure_alone_does_not_escalate(self):
        task_id = self.add("Fix the typo in the README title.")
        self.script(task_id, "fail-once tests are red")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "haiku"), (task_id, "haiku")])
        self.assertEqual(self.routing(task_id)["escalationCount"], 0)

    def test_model_can_ask_for_a_stronger_one(self):
        task_id = self.add("Add a search box to the customers page.")
        self.script(task_id, "escalate-on sonnet the filter needs a redesign of the query layer")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "sonnet"), (task_id, "opus")])  # no second try
        entry = [e for e in self.routing(task_id)["executionModelHistory"]
                 if e.get("type") == "escalation"][0]
        self.assertIn("redesign of the query layer", entry["reason"])
        self.assertIn("MQ_ESCALATE", self.events_of("start")[0]["prompt"])
        self.assertNotIn("MQ_ESCALATE:", self.events_of("start")[1]["prompt"])  # nowhere to go

    def test_escalation_is_limited(self):
        task_id = self.add("Fix the typo in the README title.")
        self.script(task_id, "fail it never works")
        self.assertEqual(self.run_here(), 1)
        # Two attempts on haiku, one escalation, one attempt on sonnet, then it stops.
        self.assertEqual([model for _, model in self.starts()], ["haiku", "haiku", "sonnet"])
        task = self.task(task_id)
        self.assertEqual((task["status"], task["routing"]["escalationCount"]), ("failed", 1))
        self.assertEqual(self.run_here(), 0)  # nothing left to run, nothing is retried
        self.assertEqual(len(self.starts()), 3)

    def test_escalation_limit_and_switch_are_configurable(self):
        self.set_config(routing_max_escalations=2)
        first = self.add("Fix the typo in the README title.")
        self.script(first, "fail it never works")
        self.assertEqual(self.run_here(), 1)
        self.assertEqual([model for _, model in self.starts()],
                         ["haiku", "haiku", "sonnet", "opus"])
        self.assertEqual(self.routing(first)["escalationCount"], 2)
        self.set_config(routing_allow_escalation=False)
        second = self.add("Fix the typo in the docs.")
        self.script(second, "fail it never works")
        self.assertEqual(self.run_here(), 1)
        self.assertEqual([model for task, model in self.starts() if task == second],
                         ["haiku", "haiku"])
        self.assertNotIn("MQ_ESCALATE:", self.events_of("start")[-1]["prompt"])

    def test_escalation_skips_models_that_cannot_be_used(self):
        from mqlib import models
        models.note_unavailable("sonnet", "refused earlier")
        task_id = self.add("Fix the typo in the README title.")
        self.script(task_id, "escalate-on haiku needs more", "write README.md ok")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "haiku"), (task_id, "opus")])

    def test_manual_choice_is_not_escalated(self):
        task_id = self.add("Fix the typo.", model="haiku")
        self.script(task_id, "fail it never works")
        self.assertEqual(self.run_here(), 1)
        self.assertEqual([model for _, model in self.starts()], ["haiku", "haiku"])
        self.assertEqual(self.routing(task_id)["escalationCount"], 0)

    def test_failure_caused_by_denied_tools_is_not_a_reason_to_escalate(self):
        from mqlib import routing
        row = {"history": json.dumps([{"run": 1, "tier": "haiku", "outcome": "failed"}])}
        self.assertEqual(routing._failures_on_tier(row, "haiku"), 1)
        self.assertEqual(routing._failures_on_tier(row, "sonnet"), 0)


class ModelReview(RoutingCase):

    def setUp(self):
        super(ModelReview, self).setUp()
        self.set_config(routing_classifier="ambiguous")

    def test_borderline_task_is_reviewed_once_by_a_small_model(self):
        os.environ["FAKE_REVIEW_ANSWER"] = "tier=opus; reason=whole authentication system"
        vague, clear = self.add("Add login."), self.add(DOCS)
        self.assertEqual(self.routing(vague)["selectedModel"], "sonnet")
        self.assertEqual(self.run_here(), 0)
        reviews = self.events_of("review")
        self.assertEqual(len(reviews), 1)  # only the uncertain task, and only once
        self.assertEqual(reviews[0]["model"], "haiku")
        self.assertIn("Add login.", reviews[0]["prompt"])
        self.assertIn("--tools", reviews[0]["argv"])
        self.assertEqual(self.starts(), [(vague, "opus"), (clear, "haiku")])
        routing = self.routing(vague)
        self.assertEqual(routing["classifier"], "rules+model")
        self.assertIn("model review suggested opus: whole authentication system",
                      routing["modelReason"])
        self.assertEqual(self.history_types(vague), ["review", "run"])

    def test_review_cannot_send_a_code_change_to_the_smallest_model(self):
        os.environ["FAKE_REVIEW_ANSWER"] = "tier=haiku; reason=looks tiny"
        task_id = self.add("Fix checkout bug.")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(len(self.events_of("review")), 1)
        self.assertEqual(self.starts(), [(task_id, "sonnet")])

    def test_failed_review_keeps_the_rule_based_decision(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "haiku"
        os.environ["FAKE_REVIEW_ANSWER"] = "no idea"
        task_id = self.add("Add login.")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "sonnet")])
        review = self.routing(task_id)["executionModelHistory"][0]
        self.assertEqual(review["type"], "review")
        self.assertIn("error", review)

    def test_review_is_off_by_setting(self):
        self.set_config(routing_classifier="off")
        self.add("Add login.")
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.events_of("review"), [])


class PhasedWorkflow(RoutingCase):

    TASK = "Redesign the permission system to support role-based access control per tenant."

    def add_task(self, *directives, **kwargs):
        task_id = self.add(self.TASK, **kwargs)
        self.script(task_id, "write rbac.txt implemented", *directives)
        return task_id

    def setUp(self):
        super(PhasedWorkflow, self).setUp()
        # plan approval for deep work is tested in test_workflow; here the phases run through
        self.set_config(workflow_deep_approval="never")

    def test_phases_are_off_unless_asked_for(self):
        # With the adaptive workflow a security redesign is deep work and runs in phases
        # (test_workflow); without it, phases are opt-in as before.
        self.set_config(workflow_mode="off")
        task_id = self.add_task()
        self.assertFalse(self.routing(task_id)["phased"])
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "opus")])

    def test_plan_with_opus_then_implement_with_sonnet(self):
        self.set_config(routing_phased="auto", routing_phased_review="never")
        task_id = self.add_task()
        ordinary = self.add(FEATURE)  # not complex enough for phases
        self.assertTrue(self.routing(task_id)["phased"])
        self.assertFalse(self.routing(ordinary)["phased"])
        self.assertEqual(self.run_here(), 0)
        starts = self.events_of("start")
        self.assertEqual([(e["task"], e["phase"], e["model"]) for e in starts],
                         [(task_id, "plan", "opus"), (task_id, "implement", "sonnet"),
                          (ordinary, None, "sonnet")])
        # The planning run may not edit files; the implementation run receives the plan.
        self.assertEqual(starts[0]["argv"][-4:], ["--disallowedTools", "Edit", "Write",
                                                  "NotebookEdit"])
        self.assertNotIn("--disallowedTools", starts[1]["argv"])
        self.assertIn("<approved_plan>", starts[1]["prompt"])
        self.assertIn("PLAN for task #%d" % task_id, starts[1]["prompt"])
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("done", 1))  # one task, one attempt
        self.assertEqual([run["phase"] for run in task["runs"]], ["plan", "implement"])
        self.assertEqual(task["result_summary"], "fake result for task %d" % task_id)
        self.assertEqual(len(self.git("log", "--format=%s").splitlines()), 3)
        self.assertEqual(self.read("rbac.txt"), "implemented\n")

    def test_review_phase_and_rework_without_planning_again(self):
        self.set_config(routing_phased_review="always")
        task_id = self.add_task("review-reject-once the tenant check is missing", phased=True)
        self.assertEqual(self.run_here(), 0)
        starts = self.events_of("start")
        self.assertEqual([(e["phase"], e["model"]) for e in starts],
                         [("plan", "opus"), ("implement", "sonnet"), ("review", "opus"),
                          ("implement", "sonnet"), ("review", "opus")])
        review_prompt = starts[2]["prompt"]
        self.assertIn("<implementation_to_review>", review_prompt)
        self.assertIn("git show", review_prompt)
        self.assertIn("<approved_plan>", review_prompt)
        # The rejected review is fed back to the implementer; the plan is not redone.
        self.assertIn("the tenant check is missing", starts[3]["prompt"])
        task = self.task(task_id)
        self.assertEqual((task["status"], task["attempts"]), ("done", 2))
        self.assertIsNone(task["routing"]["phase"])

    def test_no_other_task_slips_in_between_phases(self):
        self.set_config(routing_phased_review="never")
        task_id = self.add_task(phased=True)
        self.assertEqual(self.run_here(max_tasks=1), 0)  # the planning phase only
        self.assertEqual(self.task(task_id)["routing"]["phase"], "implement")
        urgent = self.add(DOCS, priority=20)
        self.assertEqual([task["id"] for task in self.queue().execution_order()],
                         [task_id, urgent])
        self.assertEqual(self.run_here(), 0)
        self.assertEqual([(e["task"], e["phase"]) for e in self.events_of("start")],
                         [(task_id, "plan"), (task_id, "implement"), (urgent, None)])

    def test_without_the_top_model_the_task_runs_in_one_phase(self):
        os.environ["FAKE_UNAVAILABLE_MODELS"] = "opus"
        task_id = self.add_task(phased=True)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(task_id, "sonnet")])
        self.assertFalse(self.routing(task_id)["phased"])
        self.assertEqual(self.task(task_id)["status"], "done")


class EarlierVersionQueue(RoutingCase):
    """A queue written before model routing existed keeps working, untouched."""

    def make_legacy_queue(self):
        from mqlib import store, util
        root = util.resolve_project_root(self.project)
        folder = util.project_dir(root)
        os.makedirs(folder)
        util.save_json_atomic(os.path.join(folder, "project.json"), {"root": root})
        conn = sqlite3.connect(os.path.join(folder, "queue.db"))
        conn.executescript(store.SCHEMA)  # the tables of the first release, nothing else
        conn.execute("INSERT INTO meta VALUES('schema_version', '1')")
        rows = [("Old finished task", "Write the first draft.", "done", 1),
                ("Fix the deadlock in the payment worker.",
                 "Fix the deadlock in the payment worker.\nFAKE: write worker.txt fixed",
                 "pending", 0),
                ("Fix the typo in the README title.", DOCS, "pending", 0)]
        for title, prompt, status, attempts in rows:
            conn.execute("INSERT INTO tasks(title, prompt, status, attempts, created_at, "
                         "updated_at) VALUES(?,?,?,?,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",
                         (title, prompt, status, attempts))
        conn.commit()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(tasks)")]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        return columns, tables

    def test_legacy_queue_opens_runs_and_keeps_its_data(self):
        columns, tables = self.make_legacy_queue()
        self.assertNotIn("task_routing", tables)
        status = self.mq("status")[1]
        self.assertIn("2 pending", status)
        self.assertRegex(status, r"-\s+Fix the deadlock")  # not classified yet
        q = self.queue()
        self.assertEqual([row[1] for row in q.conn.execute("PRAGMA table_info(tasks)")], columns)
        self.assertEqual(q.conn.execute("SELECT value FROM meta WHERE key='schema_version'")
                         .fetchone()[0], "1")
        self.assertIsNone(self.task(2)["routing"])
        self.assertEqual(self.task(1)["prompt"], "Write the first draft.")
        # Tasks are classified when they are first needed, then run on the routed model.
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.starts(), [(2, "opus"), (3, "haiku")])
        self.assertEqual(self.task(2)["routing"]["actualModel"], "claude-opus-test-1")
        self.assertIsNone(self.task(1)["routing"])  # the finished task was left alone
        self.assertEqual(self.task(1)["status"], "done")
        self.assertIn("not classified yet", self.mq("route", "1")[1])

    def test_new_tasks_join_a_legacy_queue(self):
        self.make_legacy_queue()
        self.mq("add", "--stdin", "--priority", "urgent", stdin=FEATURE.encode())
        self.assertEqual(self.routing(4)["selectedModel"], "sonnet")
        self.assertEqual([task["id"] for task in self.queue().execution_order()], [4, 2, 3])


if __name__ == "__main__":
    unittest.main()
