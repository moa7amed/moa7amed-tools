"""The model router's classifier: which tier a task description is routed to, and why."""
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (puts the library on the path)
import routing_corpus as corpus
import routing_corpus_multilingual as multilingual
from mqlib import router, router_ar


def tier(prompt, policy="balanced", **kwargs):
    return router.classify(prompt, policy=policy, **kwargs)["tier"]


class TierSelection(unittest.TestCase):

    def assert_tier(self, prompts, expected, policy="balanced"):
        for prompt in prompts:
            decision = router.classify(prompt, policy=policy)
            self.assertEqual(decision["tier"], expected,
                             "%r -> %s (%s)" % (prompt, decision["tier"], decision["reason"]))

    def test_simple_documentation_and_text_changes_go_to_haiku(self):
        self.assert_tier(corpus.HAIKU, "haiku")

    def test_ordinary_development_goes_to_sonnet(self):
        self.assert_tier(corpus.SONNET, "sonnet")

    def test_architecture_debugging_and_high_risk_work_goes_to_opus(self):
        self.assert_tier(corpus.OPUS, "opus")

    def test_short_prompts_are_not_mistaken_for_easy_ones(self):
        for prompt, expected in corpus.SHORT_BUT_HARD:
            decision = router.classify(prompt)
            self.assertEqual(decision["tier"], expected, prompt)
            self.assertNotEqual(decision["tier"], "haiku", prompt)
            self.assertFalse(decision["haiku_eligible"], prompt)

    def test_haiku_needs_positive_evidence_of_a_trivial_change(self):
        # Nothing here says "documentation" or "mechanical": the floor is sonnet, however short.
        for prompt in ("Fix it.", "Do the thing.", "Checkout.", "x", "Add login."):
            self.assertEqual(tier(prompt), "sonnet", prompt)

    def test_negated_mentions_are_not_signals(self):
        for prompt, expected in corpus.NEGATED:
            self.assertEqual(tier(prompt), expected, prompt)
        with_scope = router.classify("Add a tooltip to the save button.")
        negated = router.classify("Add a tooltip to the save button without changing the "
                                  "schema or any migrations.")
        self.assertEqual(negated["dimensions"]["database"], with_scope["dimensions"]["database"])

    def test_technical_words_in_a_documentation_request_are_the_subject_not_the_work(self):
        self.assert_tier(corpus.DOCS_ABOUT_TECHNICAL_TOPICS, "haiku")
        for prompt in corpus.LARGE_DOCS:
            self.assertEqual(tier(prompt), "sonnet", prompt)

    def test_hard_work_is_not_hidden_by_a_documentation_chore_next_to_it(self):
        for prompt, expected in corpus.MIXED_WITH_DOCS:
            decision = router.classify(prompt)
            self.assertEqual(decision["tier"], expected,
                             "%r -> %s (%s)" % (prompt, decision["tier"], decision["reason"]))

    def test_writing_that_needs_deep_reasoning_still_goes_to_opus(self):
        self.assert_tier(corpus.REASONING_HEAVY_WRITING, "opus")

    def test_vague_requests_are_flagged_for_review(self):
        for prompt in corpus.VAGUE:
            decision = router.classify(prompt)
            self.assertEqual(decision["tier"], "sonnet", prompt)
            self.assertNotEqual(decision["confidence"], "high", prompt)
            self.assertTrue(decision["doubts"], prompt)

    def test_clear_cases_are_confident(self):
        for prompt in (corpus.HAIKU[0], corpus.SONNET[0], corpus.OPUS[1]):
            self.assertEqual(router.classify(prompt)["confidence"], "high", prompt)


class ArabicAndMixedRequests(unittest.TestCase):
    """Regression guard for the Arabic vocabulary. These sets were tuned against, so
    passing them is not an accuracy claim; see routing_corpus_multilingual.py."""

    def assert_set(self, cases):
        for prompt, expected, language, topic in cases:
            decision = router.classify(prompt)
            self.assertEqual(decision["tier"], expected,
                             "[%s/%s] %s -> %s (%s)" % (language, topic, prompt.encode("unicode_escape"),
                                                        decision["tier"], decision["reason"]))

    def test_development_set(self):
        self.assert_set(multilingual.DEVELOPMENT)

    def test_held_out_set(self):
        self.assert_set(multilingual.HELD_OUT)

    def test_second_set(self):
        self.assert_set(multilingual.FRESH)

    def test_every_language_and_topic_is_represented(self):
        cases = multilingual.DEVELOPMENT + multilingual.HELD_OUT + multilingual.FRESH
        self.assertEqual({language for _, _, language, _ in cases}, {"ar-msa", "ar-eg", "mixed"})
        for topic in ("docs", "ui", "architecture", "database", "short", "debugging"):
            self.assertGreaterEqual(len([1 for _, _, _, t in cases if t == topic]), 5, topic)
        for tier in router.TIERS:
            self.assertGreaterEqual(len([1 for _, expected, _, _ in cases if expected == tier]), 25)

    def test_no_hard_arabic_task_goes_to_the_smallest_model(self):
        for prompt, expected, _, _ in multilingual.DEVELOPMENT + multilingual.HELD_OUT + multilingual.FRESH:
            if expected != "haiku":
                self.assertFalse(router.classify(prompt)["haiku_eligible"],
                                 prompt.encode("unicode_escape"))

    def test_spelling_variants_are_read_the_same(self):
        self.assertEqual(router_ar.normalize(u"\u0623\u064e\u0639\u0650\u062f \u0647\u064a\u0643\u0644\u0629"),
                         u"\u0627\u0639\u062f \u0647\u064a\u0643\u0644\u0647")
        with_marks = u"\u0623\u0635\u0644\u0650\u062d \u062a\u0633\u0631\u064a\u0628 \u0627\u0644\u0630\u0627\u0643\u0631\u0629."
        plain = u"\u0627\u0635\u0644\u062d \u062a\u0633\u0631\u064a\u0628 \u0627\u0644\u0630\u0627\u0643\u0631\u0647."
        self.assertEqual(router.classify(with_marks)["tier"], "opus")
        self.assertEqual(router.classify(with_marks)["tier"], router.classify(plain)["tier"])

    def test_arabic_requests_left_at_the_default_tier_ask_for_a_second_opinion(self):
        # Measured: unfamiliar Arabic phrasing of hard work is what the rules miss, and
        # they miss it towards sonnet. Those decisions are therefore marked for review.
        for prompt, expected, _, _ in multilingual.DEVELOPMENT + multilingual.FRESH:
            decision = router.classify(prompt)
            if decision["tier"] == "sonnet" and not decision["critical"]:
                self.assertNotEqual(decision["confidence"], "high", prompt.encode("unicode_escape"))
        # English decisions are not affected.
        self.assertEqual(router.classify(corpus.SONNET[0])["confidence"], "high")

    def test_english_results_are_unchanged_by_the_arabic_vocabulary(self):
        for prompt in corpus.HAIKU + corpus.SONNET + corpus.OPUS:
            self.assertFalse(router_ar.has_arabic(prompt))
        self.assertTrue(router_ar.has_arabic(multilingual.DEVELOPMENT[0][0]))


class Policies(unittest.TestCase):

    def test_policies_differ_only_on_borderline_tasks(self):
        for prompt, balanced, quality, cost in corpus.POLICY_DIFFERENCES:
            got = tuple(tier(prompt, policy) for policy in router.POLICIES)
            self.assertEqual(got, (balanced, quality, cost), prompt)

    def test_quality_first_never_picks_a_weaker_model_than_balanced(self):
        order = router.TIERS
        for prompt in corpus.HAIKU + corpus.SONNET + corpus.OPUS:
            self.assertGreaterEqual(order.index(tier(prompt, "quality_first")),
                                    order.index(tier(prompt, "balanced")), prompt)

    def test_cost_optimized_never_picks_a_stronger_model_than_balanced(self):
        order = router.TIERS
        for prompt in corpus.HAIKU + corpus.SONNET + corpus.OPUS:
            self.assertLessEqual(order.index(tier(prompt, "cost_optimized")),
                                 order.index(tier(prompt, "balanced")), prompt)

    def test_cost_optimized_does_not_send_code_changes_to_haiku(self):
        for prompt in corpus.SONNET + corpus.OPUS:
            self.assertNotEqual(tier(prompt, "cost_optimized"), "haiku", prompt)

    def test_unknown_policy_behaves_like_balanced(self):
        self.assertEqual(router.classify(corpus.OPUS[0], policy="nonsense")["policy"], "balanced")


class Explanation(unittest.TestCase):

    def test_decision_carries_every_dimension_and_a_reason(self):
        decision = router.classify(corpus.OPUS[0], "- migrations run\n- no downtime")
        self.assertEqual(set(decision["dimensions"]), set(router.DIMENSIONS))
        for name, value in decision["dimensions"].items():
            self.assertTrue(0 <= value <= 10, name)
        self.assertTrue(0 <= decision["score"] <= 100)
        self.assertGreaterEqual(decision["dimensions"]["database"], 8)
        self.assertGreater(decision["dimensions"]["regression_risk"], 5)
        self.assertGreater(decision["dimensions"]["testing"], 0)
        self.assertIn("database", decision["reason"])
        self.assertTrue(decision["critical"])
        self.assertEqual(decision["version"], router.VERSION)

    def test_scores_rank_tasks_sensibly(self):
        docs = router.classify(corpus.HAIKU[0])["score"]
        feature = router.classify(corpus.SONNET[0])["score"]
        hard = router.classify(corpus.OPUS[0])["score"]
        self.assertLess(docs, feature)
        self.assertLess(feature, hard)

    def test_more_scope_means_a_higher_score(self):
        narrow = router.classify("Add validation to the signup form.")
        broad = router.classify(
            "Add validation to every form across all modules:\n- signup\n- login\n- checkout\n"
            "- profile\n- admin/users\nUpdate src/forms/base.py, src/forms/signup.py, "
            "src/forms/login.py, src/checkout/forms.py and src/admin/forms.py. All tests pass.")
        self.assertGreater(broad["dimensions"]["affected_modules"],
                           narrow["dimensions"]["affected_modules"])
        self.assertGreater(broad["dimensions"]["code_changes"], narrow["dimensions"]["code_changes"])
        self.assertGreater(broad["score"], narrow["score"])

    def test_same_input_gives_the_same_decision(self):
        for prompt in corpus.SONNET[:5] + corpus.OPUS[:5]:
            self.assertEqual(router.classify(prompt), router.classify(prompt))

    def test_workspace_files_are_taken_into_account(self):
        folder = tempfile.mkdtemp(prefix="mq-router-")
        try:
            os.makedirs(os.path.join(folder, "src"))
            with open(os.path.join(folder, "src", "engine.py"), "w") as handle:
                handle.write("x = 1\n" * 12000)  # a large module
            prompt = "Fix the bug in src/engine.py."
            known = router.classify(prompt, project_root=folder)
            unknown = router.classify(prompt)
            self.assertGreater(known["dimensions"]["code_changes"],
                               unknown["dimensions"]["code_changes"])
        finally:
            shutil.rmtree(folder)

    def test_acceptance_criteria_raise_the_testing_dimension(self):
        plain = router.classify("Add an endpoint for notes.")
        tested = router.classify("Add an endpoint for notes.",
                                 "- returns 201\n- validates the body\n- all tests pass")
        self.assertGreater(tested["dimensions"]["testing"], plain["dimensions"]["testing"])


class ModelReview(unittest.TestCase):
    """The optional model opinion can adjust a decision only within safe bounds."""

    def test_answer_is_parsed(self):
        self.assertEqual(router.parse_review("tier=opus; reason=needs root cause analysis"),
                         ("opus", "needs root cause analysis"))
        self.assertEqual(router.parse_review("Sure!\nTIER: Sonnet, reason: ordinary feature."),
                         ("sonnet", "ordinary feature"))
        self.assertEqual(router.parse_review("I cannot decide"), (None, None))

    def test_review_moves_at_most_one_step(self):
        decision = router.classify("Fix the typo in CONTRIBUTING.md.")
        self.assertEqual(router.merge_review(decision, "opus", "x")[0], "sonnet")
        self.assertEqual(router.merge_review(decision, "haiku", "x"), ("haiku", None))

    def test_review_cannot_send_a_code_change_to_haiku(self):
        decision = router.classify("Fix checkout bug.")
        self.assertEqual(decision["tier"], "sonnet")
        self.assertEqual(router.merge_review(decision, "haiku", "looks trivial"), ("sonnet", None))
        self.assertEqual(router.merge_review(decision, "opus", "payment flow")[0], "opus")

    def test_review_request_is_small_whatever_the_task_size(self):
        long_prompt = "Refactor the billing module. " + ("More detail about the module. " * 4000)
        decision = router.classify(long_prompt)
        request = router.review_prompt(long_prompt, None, decision)
        self.assertLess(len(request), 2600)
        self.assertLess(len(router.REVIEW_SYSTEM_PROMPT), 900)
        # The system prompt travels as a command-line argument: no shell metacharacters.
        self.assertNotRegex(router.REVIEW_SYSTEM_PROMPT, r"[<>|&^%\"`$]")


if __name__ == "__main__":
    unittest.main()
