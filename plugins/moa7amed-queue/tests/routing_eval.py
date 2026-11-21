#!/usr/bin/env python3
"""Measure the model router against the reference corpora and print the accuracy.

    python tests/routing_eval.py            summary per corpus, language and topic
    python tests/routing_eval.py --misses   also list every prompt routed to another tier
    python tests/routing_eval.py --json     machine-readable

The numbers describe these corpora only. They are not a claim about prompts in general.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import routing_corpus as english  # noqa: E402
import routing_corpus_multilingual as multilingual  # noqa: E402
from mqlib import router  # noqa: E402


def english_cases():
    cases = [(prompt, "haiku", "en", "docs") for prompt in english.HAIKU]
    cases += [(prompt, "sonnet", "en", "feature") for prompt in english.SONNET]
    cases += [(prompt, "opus", "en", "hard") for prompt in english.OPUS]
    cases += [(prompt, tier, "en", "short") for prompt, tier in english.SHORT_BUT_HARD]
    cases += [(prompt, tier, "en", "negation") for prompt, tier in english.NEGATED]
    cases += [(prompt, "haiku", "en", "docs") for prompt in english.DOCS_ABOUT_TECHNICAL_TOPICS]
    cases += [(prompt, "sonnet", "en", "docs") for prompt in english.LARGE_DOCS]
    cases += [(prompt, "opus", "en", "writing") for prompt in english.REASONING_HEAVY_WRITING]
    cases += [(prompt, tier, "en", "mixed-with-docs") for prompt, tier in english.MIXED_WITH_DOCS]
    cases += [(prompt, "sonnet", "en", "vague") for prompt in english.VAGUE]
    return cases


def evaluate(cases, policy="balanced"):
    """Returns {"total", "correct", "accuracy", "by_language", "by_topic", "misses"}."""
    result = {"total": len(cases), "correct": 0, "by_language": {}, "by_topic": {}, "misses": []}
    for prompt, expected, language, topic in cases:
        decision = router.classify(prompt, policy=policy)
        hit = decision["tier"] == expected
        result["correct"] += hit
        for key, name in (("by_language", language), ("by_topic", topic)):
            bucket = result[key].setdefault(name, {"total": 0, "correct": 0})
            bucket["total"] += 1
            bucket["correct"] += hit
        if not hit:
            result["misses"].append({"prompt": prompt, "expected": expected,
                                     "got": decision["tier"], "score": decision["score"],
                                     "reason": decision["reason"], "language": language,
                                     "topic": topic})
    result["accuracy"] = round(result["correct"] / float(len(cases)), 4) if cases else None
    return result


def corpora():
    return [("English (tuned against)", english_cases()),
            ("Arabic and mixed, development set (tuned against)", multilingual.DEVELOPMENT),
            ("Arabic and mixed, held-out set (not inspected while tuning)", multilingual.HELD_OUT),
            ("Arabic and mixed, second set (first score %d/%d, tuned against since)"
             % multilingual.FRESH_FIRST_SCORE_RULES_ONLY, multilingual.FRESH)]


def main(argv):
    results = [(name, evaluate(cases)) for name, cases in corpora()]
    if "--json" in argv:
        print(json.dumps({name: result for name, result in results}, ensure_ascii=False, indent=2))
        return 0
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    for name, result in results:
        print("%s: %d of %d correct (%.1f%%)" % (name, result["correct"], result["total"],
                                                 100.0 * result["accuracy"]))
        for key, label in (("by_language", "language"), ("by_topic", "topic")):
            parts = ["%s %d/%d" % (bucket, data["correct"], data["total"])
                     for bucket, data in sorted(result[key].items())]
            print("    by %-8s %s" % (label, ", ".join(parts)))
        if "--misses" in argv:
            for miss in result["misses"]:
                print("    MISS expected %-6s got %-6s (score %d) [%s/%s] %s" % (
                    miss["expected"], miss["got"], miss["score"], miss["language"],
                    miss["topic"], miss["prompt"]))
                print("         %s" % miss["reason"])
    print("\nThese figures describe the corpora in tests/ only; they are not a general "
          "accuracy claim.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
