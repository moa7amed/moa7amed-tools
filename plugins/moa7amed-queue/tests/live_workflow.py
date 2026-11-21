#!/usr/bin/env python3
"""Live check of the adaptive workflow with the real Claude Code CLI. THIS COSTS MONEY.

    python tests/live_workflow.py --budget 0.80
    python tests/live_workflow.py --budget 0.80 --json result.json

The same two tasks (a spelling fix and a small function with a test) run on two throwaway
projects: one with workflow_mode=off (the 0.4 behaviour), one with workflow_mode=auto.
It checks that

    profiles     the spelling fix is QUICK on Haiku, the function STANDARD on Sonnet
    verified     real models report MQ_VERIFIED when asked, so enforcing it does not fail
                 work that was done (a task without it would fail and be retried)
    compare      the cost and tokens Claude Code reports for each run, with and without the
                 workflow. One run per task is an observation, not a benchmark: the same
                 task varies from run to run.

Every task runs with `claude --safe-mode` and its own --max-budget-usd. Costs are the
figures Claude Code reports, which it computes itself and calls estimates.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from live_routing import Check, LiveRouting  # noqa: E402

TASKS = (
    ("quick", "Fix the spelling mistake 'projct' in the README.md file."),
    ("standard", "Add a function is_even(n) to util.py that returns True for even integers, "
                 "with a docstring, and a unittest for it in test_util.py."),
)


class LiveWorkflow(LiveRouting):

    def runs_with_usage(self, project):
        db = json.loads(self.mq(project, "where", "--json"))["database"]
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(
                "SELECT r.task_id, r.outcome, r.error, r.cost_usd, r.stdout_file, rr.actual_model "
                "FROM runs r LEFT JOIN run_routing rr ON rr.run_id = r.id ORDER BY r.id")]
            flows = {row["task_id"]: dict(row) for row in conn.execute(
                "SELECT task_id, profile, verified FROM task_workflow")} if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='task_workflow'").fetchone() else {}
        finally:
            conn.close()
        for row in rows:
            row["tokens"] = None
            try:
                with open(row.pop("stdout_file"), encoding="utf-8") as handle:
                    usage = json.load(handle).get("usage") or {}
                row["tokens"] = {key: usage.get(key) for key in (
                    "input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens")}
            except (OSError, ValueError, TypeError):
                pass
            row.update(flows.get(row["task_id"]) or {})
        return rows

    def run_mode(self, mode):
        project = self.project("workflow-" + mode, {"workflow_mode": mode,
                                                    "workflow_superpowers": "no"})
        for _, prompt in TASKS:
            self.mq(project, "add", prompt)
        self.mq(project, "run", expect=None)
        return project, self.runs_with_usage(project)

    def scenario(self, name, function):
        self.current = {"scenario": name, "checks": []}
        print("  %s" % name)
        try:
            function()
            self.current["passed"] = True
        except Check:
            self.current["passed"] = False
        self.results.append(self.current)

    def main(self):
        version = subprocess.run([shutil.which("claude") or "claude", "--version"],
                                 stdout=subprocess.PIPE, env=self.env).stdout.decode().strip()
        print("budget $%.2f; data in %s (%s)" % (self.args.budget, self.tmp, version))
        observations = {}

        def baseline():
            _, rows = self.run_mode("off")
            observations["off"] = rows
            self.check(all(row["outcome"] == "done" for row in rows),
                       "both tasks done without the workflow", rows)

        def adaptive():
            project, rows = self.run_mode("auto")
            observations["auto"] = rows
            self.check(len(rows) == 2, "each task ran once (no failure, no retry)",
                       [(row["task_id"], row["outcome"]) for row in rows])
            self.check(all(row["outcome"] == "done" for row in rows), "both tasks done", rows)
            profiles = [row.get("profile") for row in rows]
            self.check(profiles == ["quick", "standard"], "profiles quick, standard", profiles)
            self.check(all(row.get("verified") for row in rows),
                       "each run reported how it verified its result",
                       [row.get("verified") for row in rows])
            self.check([self.family(row["actual_model"]) for row in rows] == ["haiku", "sonnet"],
                       "haiku for the quick task, sonnet for the standard one",
                       [row["actual_model"] for row in rows])
            self.check(os.path.exists(os.path.join(project, "test_util.py")),
                       "the standard task wrote its test")

        if self.args.only in (None, "off"):
            self.scenario("baseline (workflow_mode=off)", baseline)
        if self.args.only in (None, "auto") and self.spent() < self.args.budget - 0.3:
            self.scenario("adaptive (workflow_mode=auto)", adaptive)
        print("\nReported per run (cost USD; output tokens):")
        for mode in ("off", "auto"):
            for row in observations.get(mode, []):
                print("  %-4s task %d %-8s $%s  out=%s" % (
                    mode, row["task_id"], row.get("profile") or "-", row["cost_usd"],
                    (row["tokens"] or {}).get("output_tokens")))
        summary = {"claude_code": version, "budget_usd": self.args.budget,
                   "spent_usd_reported": round(self.spent(), 4), "results": self.results,
                   "observations": observations}
        print("\nspent (reported by Claude Code): $%.4f" % summary["spent_usd_reported"])
        if self.args.json:
            with open(self.args.json, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)
        if not self.args.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)
        return 0 if all(item["passed"] for item in self.results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--json")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--only", choices=["off", "auto"])
    args = parser.parse_args(argv)
    if shutil.which("claude") is None:
        print("live_workflow: the `claude` command was not found")
        return 2
    return LiveWorkflow(args).main()


if __name__ == "__main__":
    sys.exit(main())
