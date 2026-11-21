"""The adaptive engineering workflow: quick, standard and deep process per task, chosen from
the router's analysis, applied through the prompt, the phases, plan approval and the
verification check, and bounded when it escalates."""
import json
import os
import unittest

from helpers import QueueTestCase
from test_planning import PlanCase, ready_plan

from mqlib import engine, plan_store, router, routing, util, workflow
from mqlib.util import QueueError

BUTTON = "Update the login button text to Sign in"
SEARCH = "Implement shipment search with filters on the shipments page"
DEPLOY = "Redesign the enterprise deployment architecture and move the database to a cluster"
DEADLOCK = "Fix a race condition in the billing worker"
ARABIC_TYPO = u"صحح الخطأ الإملائي في ملف README"
ARABIC_DB = (u"انقل قاعدة البيانات "
             u"من MySQL إلى PostgreSQL مع ترحيل "
             u"البيانات")


class WorkflowCase(QueueTestCase):

    def run_here(self, **options):
        from mqlib import pyrunner
        flags = {key: options.pop(key) for key in list(options)
                 if key in ("max_tasks", "stop_on_failure")}
        return pyrunner.run(self.queue(), options, quiet=True, **flags)

    def flow(self, task_id):
        return workflow.describe(self.queue(), task_id)

    def profile(self, prompt):
        return self.flow(self.add(prompt))["profile"]

    def prompt_of(self, task_id, phase=None):
        q = self.queue()
        route = {"phase": phase}
        return engine.compose_prompt(q, q.get(task_id), 1, self.project, self.project, route)


class Selection(WorkflowCase):
    """The profile follows the work, not the length of the request."""

    def test_simple_tasks_avoid_planning(self):
        task_id = self.add(BUTTON)
        info = self.flow(task_id)
        self.assertEqual(info["profile"], "quick")
        self.assertEqual(info["capabilities"]["planning"], "off")
        self.assertEqual(info["capabilities"]["architecture"], "off")
        self.assertFalse(self.routing(task_id)["phased"])
        prompt = self.prompt_of(task_id)
        self.assertIn("No planning documents", prompt)
        self.assertNotIn("Plan thoroughly", prompt)
        self.assertNotIn("deployment environment", prompt)

    def test_standard_features_get_tests_and_a_targeted_plan(self):
        task_id = self.add(SEARCH)
        info = self.flow(task_id)
        self.assertEqual(info["profile"], "standard")
        self.assertEqual(info["capabilities"]["tdd"], "when_suitable")
        prompt = self.prompt_of(task_id)
        self.assertIn("Add or update the relevant tests", prompt)
        self.assertIn("short, targeted plan", prompt)
        self.assertNotIn("Plan thoroughly", prompt)

    def test_architecture_work_is_deep_and_planned_first(self):
        task_id = self.add(DEPLOY)
        info = self.flow(task_id)
        self.assertEqual(info["profile"], "deep")
        self.assertTrue(info["material"])
        self.assertTrue(self.routing(task_id)["phased"])
        prompt = self.prompt_of(task_id, "plan")
        self.assertIn("Plan thoroughly", prompt)
        self.assertIn("at least two approaches", prompt)
        self.assertIn("do not invent hardware figures", prompt)
        self.assertIn("owner reviews this plan", prompt)

    def test_short_prompts_for_hard_work_are_not_quick(self):
        for prompt in (DEADLOCK, "Fix the race condition in checkout.", "Add OAuth login.",
                       "Migrate the database to PostgreSQL."):
            self.assertNotEqual(self.profile(prompt), "quick", prompt)
        deadlock = self.flow(1)
        self.assertEqual(deadlock["capabilities"]["debugging"], "on")
        self.assertIn("Reproduce the problem first", self.prompt_of(1))
        self.assertEqual(self.profile("Migrate the database to PostgreSQL."), "deep")

    def test_a_long_request_for_a_small_change_is_not_deep(self):
        long_text = ("Change the text of the save button on the profile page from 'Save' to "
                     "'Save changes'. The button is in the profile form. Keep the same style, "
                     "the same colour and the same position. Nothing else on the page changes. "
                     "Please be careful to only change the visible label. " * 4)
        self.assertNotEqual(self.profile(long_text), "deep")

    def test_corpus_profiles_follow_the_kind_of_work(self):
        import routing_corpus as corpus
        for prompt in [item[0] if isinstance(item, (list, tuple)) else item
                       for item in corpus.HAIKU]:
            self.assertEqual(workflow.select(router.classify(prompt))["profile"], "quick", prompt)
        for prompt in [item[0] if isinstance(item, (list, tuple)) else item
                       for item in corpus.SONNET]:
            self.assertEqual(workflow.select(router.classify(prompt))["profile"], "standard",
                             prompt)

    def test_arabic_and_english_requests(self):
        self.assertEqual(self.profile(ARABIC_TYPO), "quick")
        self.assertEqual(self.profile(ARABIC_DB), "deep")
        import routing_corpus_multilingual as multilingual
        for prompt, expected, _, topic in (multilingual.DEVELOPMENT + multilingual.HELD_OUT
                                           + multilingual.FRESH):
            profile = workflow.select(router.classify(prompt))["profile"]
            if expected == "opus":
                self.assertNotEqual(profile, "quick", prompt.encode("unicode_escape"))
            if topic in ("docs", "ui"):
                self.assertNotEqual(profile, "deep", prompt.encode("unicode_escape"))
            if topic in ("database", "security"):
                self.assertEqual(profile, "deep", prompt.encode("unicode_escape"))

    def test_hardware_assessment_only_where_infrastructure_is_in_play(self):
        quick, standard, deep = self.add(BUTTON), self.add(SEARCH), self.add(DEPLOY)
        self.assertEqual(self.flow(quick)["capabilities"]["infrastructure"], "if_relevant")
        self.assertEqual(self.flow(standard)["capabilities"]["infrastructure"], "if_affected")
        self.assertEqual(self.flow(deep)["capabilities"]["infrastructure"], "on")
        for task_id in (quick, standard):
            self.assertNotIn("deployment environment", self.prompt_of(task_id))
        self.assertIn("deployment environment", self.prompt_of(deep, "plan"))


class Agents(WorkflowCase):
    """Subagents only where the profile justifies them."""

    def test_agents_follow_the_profile_and_the_policy(self):
        quick, standard, deep = self.add(BUTTON), self.add(SEARCH), self.add(DEPLOY)
        self.assertIn("Do not start subagents.", self.prompt_of(quick))
        self.assertIn("only for independent parts", self.prompt_of(standard))
        self.assertIn("A separate subagent is justified", self.prompt_of(deep, "implement"))
        self.assertEqual(self.flow(quick)["agents"], "not allowed")
        self.set_config(workflow_agent_policy="never")
        later = self.add(DEPLOY)
        self.assertEqual(self.flow(later)["capabilities"]["subagents"], "off")
        self.assertIn("Do not start subagents.", self.prompt_of(later, "implement"))


class DeepRuns(WorkflowCase):
    """The deep process end to end, with approval."""

    def test_a_material_plan_waits_for_approval_and_other_work_continues(self):
        deep = self.add(DEPLOY)
        other = self.add(BUTTON)
        self.script(other, "write button.txt done")
        self.run_here()
        starts = [(e["task"], e["phase"], e["model"]) for e in self.events_of("start")]
        self.assertEqual(starts, [(deep, "plan", "opus"), (other, None, "haiku")])
        info = self.flow(deep)
        self.assertIn("approval: waiting", info["planning"])
        self.assertTrue(self.queue().model_block(deep).startswith("approval:"))
        self.assertEqual(self.statuses(), {deep: "pending", other: "done"})
        # neither a retry nor forgetting model knowledge gets past the approval
        with self.assertRaises(QueueError):
            self.queue().retry([deep])
        self.mq("models", "--reset")
        self.assertTrue(self.queue().model_block(deep).startswith("approval:"))
        # a new runner (a crash and restart) still waits
        self.run_here()
        self.assertEqual(len(self.events_of("start")), 2)
        code, out, _ = self.mq("workflow", "approve", str(deep))
        self.assertIn("Approved the plan", out)
        self.assertEqual(self.run_here(), 0)
        phases = [(e["task"], e["phase"]) for e in self.events_of("start")][2:]
        self.assertEqual(phases[0], (deep, "implement"))
        self.assertEqual(self.task(deep)["status"], "done")
        self.assertIn("<approved_plan>", self.events_of("start")[2]["prompt"])

    def test_a_manual_model_does_not_skip_the_plan_or_its_approval(self):
        # Found by the 0.8.0 demo recording: choosing the model by hand used to drop the
        # planning session, and with it the approval of a material deep plan.
        for how in ("at-add", "afterwards"):
            deep = self.add(DEPLOY, model="sonnet") if how == "at-add" else self.add(DEPLOY)
            if how == "afterwards":
                self.mq("model", "sonnet", str(deep))
            self.assertTrue(self.routing(deep)["phased"], how)
            self.assertIn("phased: sonnet in every phase (manual override)", self.mq("route", str(deep))[1])
            self.assertIn("sonnet in every phase", self.mq("workflow", str(deep))[1])
            self.run_here()
            start = [e for e in self.events_of("start") if e["task"] == deep][-1]
            self.assertEqual((start["phase"], start["model"]), ("plan", "sonnet"), how)
            self.assertTrue(self.queue().model_block(deep).startswith("approval:"), how)
            status = self.mq("status")[1]
            self.assertIn("[approval: the plan for this material task", status)
            self.assertNotIn("no usable model", status)
            self.mq("workflow", "approve", str(deep))
            self.run_here()
            phases = [(e["phase"], e["model"]) for e in self.events_of("start") if e["task"] == deep]
            self.assertEqual(phases[1], ("implement", "sonnet"), how)
            self.assertEqual(self.task(deep)["status"], "done", how)

    def test_approval_can_be_switched_off(self):
        self.set_config(workflow_deep_approval="never", routing_phased_review="never")
        deep = self.add(DEPLOY)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual([e["phase"] for e in self.events_of("start")], ["plan", "implement"])
        self.assertEqual(self.task(deep)["status"], "done")

    def test_budget_stop_is_not_retried_and_not_escalated(self):
        task_id = self.add(BUTTON)
        self.script(task_id, "budget")
        self.run_here()
        self.assertEqual(len(self.events_of("start")), 1)
        self.assertEqual(self.task(task_id)["status"], "failed")
        self.assertEqual(self.flow(task_id)["profile"], "quick")


class ManualOverrides(WorkflowCase):
    """The owner decides."""

    def test_profile_set_by_hand_survives_reclassification(self):
        task_id = self.add(BUTTON)
        self.mq("workflow", "set", str(task_id), "--profile", "deep")
        info = self.flow(task_id)
        self.assertEqual((info["profile"], info["source"]), ("deep", "manual"))
        self.assertTrue(self.routing(task_id)["phased"])
        q = self.queue()
        with q.tx():
            routing.classify_and_store(q, q.get(task_id), util.effective_config(q.dir),
                                       reclassify=True)
        self.assertEqual(self.flow(task_id)["profile"], "deep")
        self.mq("workflow", "set", str(task_id), "--profile", "auto")
        self.assertEqual(self.flow(task_id)["profile"], "quick")
        self.assertFalse(self.routing(task_id)["phased"])

    def test_a_manual_model_is_kept_whatever_the_profile(self):
        task_id = self.add(DEPLOY, model="haiku")
        self.assertEqual(self.flow(task_id)["profile"], "deep")
        self.assertEqual(self.routing(task_id)["selectedModel"], "haiku")

    def test_lowering_a_profile_keeps_verification(self):
        task_id = self.add(DEPLOY)
        self.mq("workflow", "set", str(task_id), "--profile", "quick")
        self.assertEqual(self.flow(task_id)["capabilities"]["verification"], "always")
        self.assertIn("MQ_VERIFIED", self.prompt_of(task_id))

    def test_workflow_command_reports_the_decision(self):
        self.add(DEADLOCK)
        out = self.mq("workflow")[1]
        for label in ("Workflow:     STANDARD", "Model:        opus", "debugging",
                      "Agents:", "Planning:", "Verification: required"):
            self.assertIn(label, out)
        data = self.mq_json("workflow", "1")
        self.assertEqual(data[0]["profile"], "standard")


class Escalation(WorkflowCase):
    """Bounded, recorded, never a loop, nothing completed is redone."""

    def test_a_failed_quick_task_is_retried_with_the_standard_process_once(self):
        self.set_config(routing_allow_escalation=False)
        task_id = self.add(BUTTON, max_attempts=3)
        self.script(task_id, "fail it touched more than expected")
        self.run_here()
        info = self.flow(task_id)
        self.assertEqual(info["profile"], "standard")
        self.assertEqual(info["escalations"], 1)
        self.assertEqual([entry["type"] for entry in info["history"]].count("escalation"), 1)
        self.assertEqual(len(self.events_of("start")), 3)      # no loop: attempts bound it
        self.assertEqual(self.task(task_id)["status"], "failed")
        self.assertIn("Escalated:    quick -> standard", self.mq("workflow", str(task_id))[1])

    def test_the_escalation_limit_is_respected(self):
        task_id = self.add(BUTTON)
        q = self.queue()
        config = util.effective_config(q.dir)
        task = q.get(task_id)
        with q.tx():
            first = workflow.after_run(q, task, None, "failed", "MQ_RESULT: FAILED - x", [], config)
            second = workflow.after_run(q, task, None, "failed",
                                        "MQ_ESCALATE_WORKFLOW: a new service\nMQ_RESULT: FAILED",
                                        [], config)
        self.assertIn("quick -> standard", first)
        self.assertIn("refused", second)
        info = self.flow(task_id)
        self.assertEqual((info["profile"], info["escalations"]), ("standard", 1))
        self.assertIn("escalation-refused", [entry["type"] for entry in info["history"]])

    def test_the_task_can_ask_for_a_deeper_workflow(self):
        self.set_config(routing_allow_escalation=False, workflow_deep_approval="never",
                        routing_phased_review="never")
        task_id = self.add(SEARCH, max_attempts=2)
        self.script(task_id, "escalate-workflow needs a new search service")
        self.run_here()
        info = self.flow(task_id)
        self.assertEqual(info["profile"], "deep")
        self.assertEqual(info["source"], "escalated")
        self.assertIn("needs a new search service", info["history"][-1]["reason"])
        self.assertEqual(self.events_of("start")[1]["phase"], "plan")

    def test_a_missing_verification_report_does_not_escalate(self):
        task_id = self.add(BUTTON, max_attempts=1)
        self.script(task_id, "no-verify")
        self.run_here()
        self.assertEqual(self.flow(task_id)["profile"], "quick")


class Verification(WorkflowCase):
    """A result without a verification report is not accepted."""

    def test_unverified_work_fails_and_verified_work_passes(self):
        unverified = self.add(BUTTON, max_attempts=1)
        self.script(unverified, "write button.txt changed", "no-verify")
        verified = self.add(SEARCH)
        self.script(verified, "write search.txt done")
        self.run_here()
        task = self.task(unverified)
        self.assertEqual(task["status"], "failed")
        self.assertIn("MQ_VERIFIED", task["last_error"])
        self.assertEqual(self.task(verified)["status"], "done")
        self.assertEqual(self.flow(verified)["verification"], "the fake checked its own output")

    def test_enforcement_can_be_switched_off(self):
        self.set_config(workflow_enforce_verification=False)
        task_id = self.add(BUTTON)
        self.script(task_id, "no-verify")
        self.run_here()
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertNotIn("MQ_VERIFIED", self.prompt_of(task_id))

    def test_a_plan_phase_needs_no_verification_report(self):
        self.assertEqual(workflow.verification_rule({}, "plan"), [])
        self.assertIsNone(workflow.verification_problem(None, {}, "plan", "a plan"))


class ApprovedPlans(WorkflowCase, PlanCase):
    """Approved architecture is reused; a changed stack invalidates it."""

    def approved(self):
        self.write("package.json", '{"dependencies": {"express": "4.0.0"}}\n')
        q = ready_plan(self)
        self.assertTrue(plan_store.approve(q, "p", confirmed=True)["approved"])
        return q

    def test_plan_tasks_reuse_the_plan_instead_of_planning_again(self):
        q = self.approved()
        plan_store.execute(q, "p", confirmed=True)
        task = [t for t in q.tasks() if str(t["source"]).startswith("plan:")][0]
        info = self.flow(task["id"])
        self.assertEqual(info["capabilities"]["planning"], "reuse")
        self.assertEqual(info["capabilities"]["architecture"], "reuse")
        self.assertEqual(info["capabilities"]["brainstorming"], "off")
        self.assertFalse(self.routing(task["id"])["phased"])
        prompt = self.prompt_of(task["id"])
        self.assertIn("Follow the approved plan", prompt)
        self.assertIn("ADR-1", prompt)
        self.assertIn("Approved technologies", prompt)

    def test_a_current_baseline_is_reused_by_deep_work(self):
        self.approved()
        task_id = self.add(DEPLOY)
        info = self.flow(task_id)
        self.assertEqual(info["capabilities"]["architecture"], "reuse")
        self.assertEqual(info["capabilities"]["infrastructure"], "reuse")
        self.assertIn("fits the approved architecture", self.prompt_of(task_id, "plan"))

    def test_a_changed_stack_invalidates_the_baseline(self):
        q = self.approved()
        self.assertTrue(workflow.project_context(q)["baseline_current"])
        self.write("package.json", '{"dependencies": {"express": "5.0.0", "pg": "8"}}\n')
        context = workflow.project_context(q)
        self.assertFalse(context["baseline_current"])
        self.assertIn("package.json changed since the plan was approved", context["stale"])
        task_id = self.add(DEPLOY)
        self.assertEqual(self.flow(task_id)["capabilities"]["architecture"], "on")
        self.assertIn("may be out of date", self.prompt_of(task_id, "plan"))

    def test_only_the_relevant_context_reaches_a_task(self):
        q = self.approved()
        minimal = workflow.project_context(q, {"dimensions": {}}, {}, minimal=True)
        full = workflow.project_context(q, None, {}, minimal=False)
        self.assertLessEqual(len(minimal["lines"]), len(full["lines"]))
        self.assertNotIn("Approved project context", self.prompt_of(self.add(BUTTON)))
        self.assertIn("Approved project context", self.prompt_of(self.add(SEARCH)))


class Superpowers(WorkflowCase):
    """One authoritative workflow when Superpowers is installed."""

    def plugins(self, *entries):
        path = os.path.join(self.tmp, "plugins.json")
        with open(path, "w") as handle:
            json.dump({"plugins": list(entries)}, handle)
        os.environ["FAKE_PLUGINS"] = path
        workflow._SUPERPOWERS_CACHE.clear()
        cache = os.path.join(self.home, "plugins-seen.json")
        if os.path.exists(cache):
            os.remove(cache)

    def test_detection_through_claude_code(self):
        self.plugins({"id": "superpowers@superpowers-marketplace", "enabled": True})
        self.assertTrue(workflow.superpowers_present({"workflow_superpowers": "auto"}))
        self.plugins({"id": "superpowers@superpowers-marketplace", "enabled": False})
        self.assertFalse(workflow.superpowers_present({"workflow_superpowers": "auto"}))
        self.plugins({"id": "other@x", "enabled": True})
        self.assertFalse(workflow.superpowers_present({"workflow_superpowers": "auto"}))
        # asked once, then cached
        listed = len(self.events_of("plugin"))
        workflow._SUPERPOWERS_CACHE.clear()
        workflow.superpowers_present({"workflow_superpowers": "auto"})
        self.assertEqual(len(self.events_of("plugin")), listed)

    def test_this_workflow_leads_and_superpowers_is_not_run_twice(self):
        self.set_config(workflow_superpowers="yes")
        prompt = self.prompt_of(self.add(SEARCH))
        self.assertIn("do not also run its brainstorming, planning or execution skills", prompt)

    def test_superpowers_can_be_made_the_authority(self):
        self.set_config(workflow_superpowers="yes", workflow_authority="superpowers")
        prompt = self.prompt_of(self.add(SEARCH))
        self.assertIn("Use the Superpowers workflow", prompt)
        self.assertNotIn("short, targeted plan", prompt)
        self.assertIn("MQ_VERIFIED", prompt)

    def test_no_superpowers_no_mention(self):
        self.set_config(workflow_superpowers="no")
        self.assertNotIn("Superpowers", self.prompt_of(self.add(SEARCH)))


class Compatibility(WorkflowCase):
    """Workflow_mode=off is the 0.4 queue; prompts stay small."""

    def test_off_restores_the_earlier_behaviour(self):
        self.set_config(workflow_mode="off")
        deep = self.add(DEPLOY)
        self.script(deep, "no-verify")
        self.assertIsNone(self.flow(deep))
        self.assertFalse(self.routing(deep)["phased"])
        prompt = self.prompt_of(deep)
        self.assertNotIn("<workflow>", prompt)
        self.assertNotIn("MQ_VERIFIED", prompt)
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.task(deep)["status"], "done")

    def test_workflow_text_is_small_and_grows_with_the_profile(self):
        sizes = {}
        for name, prompt, phase in (("quick", BUTTON, None), ("standard", SEARCH, None),
                                    ("deep", DEPLOY, "plan")):
            text = self.prompt_of(self.add(prompt), phase)
            block = text[text.index("<workflow>"):text.index("</workflow>")]
            sizes[name] = len(block)
        self.assertLess(sizes["quick"], sizes["standard"])
        self.assertLess(sizes["quick"], 600)
        self.assertLess(max(sizes.values()), 1400)

    def test_no_model_call_is_added(self):
        for prompt in (BUTTON, SEARCH, DEPLOY, DEADLOCK):
            self.add(prompt)
        self.mq("workflow")
        self.assertEqual(self.events_of("start"), [])
        self.assertEqual(self.events_of("review"), [])

    def test_a_task_classified_before_the_workflow_existed_gets_one(self):
        task_id = self.add(SEARCH)
        q = self.queue()
        with q.tx():
            q.conn.execute("DELETE FROM task_workflow")
        self.assertIsNone(self.flow(task_id))
        self.assertEqual(self.run_here(), 0)
        self.assertEqual(self.task(task_id)["status"], "done")
        self.assertEqual(self.flow(task_id)["profile"], "standard")


if __name__ == "__main__":
    unittest.main()
