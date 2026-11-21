#!/usr/bin/env python3
"""Live model-routing verification with the real Claude Code CLI. THIS COSTS MONEY.

    python tests/live_routing.py --budget 1.20
    python tests/live_routing.py --budget 1.20 --no-opus
    python tests/live_routing.py --budget 1.20 --json result.json

Every other routing test uses a stand-in for `claude`. This one checks, on throwaway
projects with their own data directory, that:

    auto        a documentation task is routed to Haiku and a small feature to Sonnet, and
                each actually ran on the model the CLI reports
    override    a manual model choice is honoured and recorded as manual
    fallback    a tier whose model the CLI refuses falls back to the nearest permitted
                tier; the refusal is remembered and costs nothing
    escalation  a task that asks for a stronger model moves up once and no further
                (routing_max_escalations = 1)
    opus        one trivial task on Opus (skipped with --no-opus or when the budget left
                does not cover it)

Every task runs with `claude --safe-mode` and its own --max-budget-usd. Costs are the
figures Claude Code reports, which it computes itself and calls estimates.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MQ = os.path.join(os.path.dirname(HERE), "scripts", "mq.py")
RESERVE = {"auto": 0.30, "override": 0.15, "fallback": 0.30, "escalation": 0.40, "opus": 0.50}
PER_TASK = 0.40


class Check(Exception):
    pass


class LiveRouting(object):

    def __init__(self, args):
        self.args = args
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-live-routing-"))
        self.home = os.path.join(self.tmp, "queue-home")
        os.makedirs(self.home)
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(("MOA7AMED_QUEUE_", "CTQ_")) or key == "CLAUDE_PROJECT_DIR":
                del self.env[key]
        self.env.update({"MOA7AMED_QUEUE_HOME": self.home, "PYTHONIOENCODING": "utf-8",
                         "DISABLE_AUTOUPDATER": "1"})
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            json.dump({"claude_args": ["--safe-mode"], "max_budget_usd": PER_TASK,
                       "routing_classifier": "off", "timeout_minutes": 8,
                       "retry_backoff_seconds": 0}, handle)
        self.results, self.current = [], None

    # ------------------------------------------------------------------ helpers

    def mq(self, project, *arguments, expect=0):
        result = subprocess.run([sys.executable, "-B", MQ, "--project", project] + list(arguments),
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, timeout=900)
        out = result.stdout.decode("utf-8", errors="replace")
        if expect is not None and result.returncode != expect:
            raise Check("`%s` exited %d: %s %s" % (" ".join(arguments), result.returncode,
                                                   out[-400:], result.stderr.decode(
                                                       "utf-8", errors="replace")[-400:]))
        return out

    def project(self, name, config=None):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        with open(os.path.join(path, "README.md"), "w", newline="\n") as handle:
            handle.write("# %s\n\nThis projct is a throwaway test.\n" % name)
        with open(os.path.join(path, "util.py"), "w", newline="\n") as handle:
            handle.write('"""Small helpers."""\n')
        for command in (["init", "-q", "-b", "main"], ["config", "user.name", "Live Routing"],
                        ["config", "user.email", "live-routing@example.invalid"],
                        ["add", "-A"], ["commit", "-q", "-m", "initial commit"]):
            subprocess.run(["git"] + command, cwd=path, stdout=subprocess.DEVNULL)
        for key, value in (config or {}).items():
            self.mq(path, "config", "set", key, str(value))
        return path

    def task(self, project, task_id):
        return json.loads(self.mq(project, "show", str(task_id), "--json"))

    def runs(self, project):
        db = json.loads(self.mq(project, "where", "--json"))["database"]
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(
                "SELECT r.task_id, r.attempt, r.outcome, r.cost_usd, rr.tier, rr.model_arg, "
                "rr.actual_model, rr.source, rr.verified FROM runs r LEFT JOIN run_routing rr "
                "ON rr.run_id = r.id ORDER BY r.id")]
        finally:
            conn.close()

    def spent(self):
        total = 0.0
        for folder in os.listdir(os.path.join(self.home, "projects")) \
                if os.path.isdir(os.path.join(self.home, "projects")) else []:
            conn = sqlite3.connect(os.path.join(self.home, "projects", folder, "queue.db"))
            try:
                total += conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs").fetchone()[0]
            finally:
                conn.close()
        return total

    def check(self, condition, what, detail=None):
        self.current["checks"].append({"check": what, "passed": bool(condition),
                                       "detail": detail})
        print("    [%s] %s%s" % ("ok  " if condition else "FAIL", what,
                                 "" if condition or detail is None else "  (%s)" % (detail,)))
        if not condition:
            raise Check(what)

    @staticmethod
    def family(model):
        model = (model or "").lower()
        return next((name for name in ("opus", "sonnet", "haiku") if name in model), None)

    # ------------------------------------------------------------------ scenarios

    def scenario_auto(self):
        project = self.project("auto")
        docs = json.loads(self.mq(project, "add", "Fix the spelling mistake 'projct' in the "
                                  "README.md file.", "--json"))["id"]
        feature = json.loads(self.mq(project, "add", "Add a function is_even(n) to util.py "
                                     "that returns True for even integers, with a docstring.\n"
                                     "Acceptance criteria: util.is_even(4) is True and "
                                     "util.is_even(3) is False.", "--json"))["id"]
        chosen = {task_id: self.task(project, task_id)["routing"]["selectedModel"]
                  for task_id in (docs, feature)}
        self.current["measurements"]["router_choice"] = chosen
        self.check(chosen[docs] == "haiku", "the documentation fix is routed to haiku", chosen)
        self.check(chosen[feature] == "sonnet", "the small feature is routed to sonnet", chosen)
        self.mq(project, "run")
        runs = self.runs(project)
        self.current["measurements"]["runs"] = runs
        self.check([run["outcome"] for run in runs] == ["done", "done"], "both tasks succeeded",
                   [run["outcome"] for run in runs])
        self.check([self.family(run["actual_model"]) for run in runs] == ["haiku", "sonnet"],
                   "each ran on the model it was routed to, as the CLI reports",
                   [run["actual_model"] for run in runs])
        attributed = [self.task(project, task_id)["routing"]["actualModel"]
                      for task_id in (docs, feature)]
        self.check(attributed == [run["actual_model"] for run in runs],
                   "the task history names the model that actually ran", attributed)

    def scenario_override(self):
        project = self.project("override")
        task_id = json.loads(self.mq(project, "add", "Add a function is_odd(n) to util.py.",
                                     "--json"))["id"]
        before = self.task(project, task_id)["routing"]["selectedModel"]
        self.mq(project, "model", "haiku", str(task_id))
        routing = self.task(project, task_id)["routing"]
        self.check(before != "haiku" and routing["selectedModel"] == "haiku"
                   and routing["manualOverride"], "the override replaces the automatic choice "
                   "and is marked manual", (before, routing["selectedModel"]))
        self.mq(project, "run")
        run = self.runs(project)[-1]
        self.current["measurements"]["run"] = run
        self.check(self.family(run["actual_model"]) == "haiku" and run["source"] == "manual",
                   "the task ran on haiku as a manual choice", run)

    def scenario_fallback(self):
        project = self.project("fallback", {"routing_model_haiku": "claude-haiku-does-not-exist-1"})
        task_id = json.loads(self.mq(project, "add", "Fix the spelling mistake 'projct' in "
                                     "README.md.", "--json"))["id"]
        self.mq(project, "run")
        runs = self.runs(project)
        self.current["measurements"]["runs"] = runs
        routing = self.task(project, task_id)["routing"]
        history = [entry.get("type") for entry in routing["executionModelHistory"]]
        self.check(runs[-1]["outcome"] == "done", "the task was completed", runs)
        self.check(self.family(runs[-1]["actual_model"]) == "sonnet",
                   "it fell back to the nearest permitted tier (sonnet)", runs[-1])
        self.check("fallback" in history, "the fallback is recorded in the task history", history)
        refused = [run for run in runs if run["model_arg"] == "claude-haiku-does-not-exist-1"]
        self.check(all((run["cost_usd"] or 0) == 0 for run in refused),
                   "the refused attempt cost nothing", refused)
        models = json.loads(self.mq(project, "models", "--json"))
        self.current["measurements"]["models"] = models
        self.check("claude-haiku-does-not-exist-1" in json.dumps(models),
                   "the refusal is remembered for later tasks")

    def scenario_escalation(self):
        project = self.project("escalation", {"routing_max_escalations": 1,
                                              "routing_escalation_attempts": 1,
                                              "max_attempts": 1})
        task_id = json.loads(self.mq(project, "add", "Fix the typo in the README title.\n"
                                     "Note for this run: leave the file unchanged and end your "
                                     "reply with the two lines MQ_ESCALATE: stronger model needed "
                                     "and MQ_RESULT: FAILED - escalation check", "--json"))["id"]
        self.check(self.task(project, task_id)["routing"]["selectedModel"] == "haiku",
                   "the router starts the task on haiku")
        self.mq(project, "run", expect=None)
        runs = self.runs(project)
        routing = self.task(project, task_id)["routing"]
        self.current["measurements"]["runs"] = runs
        self.current["measurements"]["escalation_count"] = routing["escalationCount"]
        families = [self.family(run["actual_model"]) for run in runs]
        self.check(len(runs) <= 2, "at most two runs: the first and one escalated run", families)
        if routing["selectedModel"] != "haiku" and len(runs) == 1:
            self.check(False, "the task started on haiku", routing["selectedModel"])
        self.check(families[:1] == ["haiku"], "it started on haiku", families)
        self.check(routing["escalationCount"] == 1 and families[1:] == ["sonnet"],
                   "it was escalated exactly once, to sonnet", families)
        self.check(self.task(project, task_id)["status"] == "failed",
                   "after the bound was reached it stopped instead of escalating again")

    def scenario_opus(self):
        project = self.project("opus")
        task_id = json.loads(self.mq(project, "add", "Fix the spelling mistake 'projct' in "
                                     "README.md.", "--model", "opus", "--json"))["id"]
        self.mq(project, "run")
        run = self.runs(project)[-1]
        self.current["measurements"]["run"] = run
        self.check(run["outcome"] == "done" and self.family(run["actual_model"]) == "opus",
                   "the task ran on opus", run)

    # ------------------------------------------------------------------ driver

    def run(self):
        version = subprocess.run([shutil.which("claude") or "claude", "--version"],
                                 stdout=subprocess.PIPE).stdout.decode().strip()
        print("Claude Code %s; budget $%.2f; data in %s" % (version, self.args.budget, self.tmp))
        names = ["auto", "override", "fallback", "escalation"] + ([] if self.args.no_opus
                                                                  else ["opus"])
        for name in names:
            self.current = {"scenario": name, "checks": [], "measurements": {}}
            self.results.append(self.current)
            left = self.args.budget - self.spent()
            if left < RESERVE[name]:
                self.current.update(status="not verified",
                                    reason="budget: $%.2f left, needs $%.2f" % (left, RESERVE[name]))
                print("\n%s: NOT VERIFIED (%s)" % (name, self.current["reason"]))
                continue
            print("\n%s" % name)
            before, started = self.spent(), time.time()
            try:
                getattr(self, "scenario_" + name)()
                self.current["status"] = "passed"
            except Check as exc:
                self.current.update(status="failed", reason=str(exc))
            self.current["cost_usd"] = round(self.spent() - before, 4)
            self.current["seconds"] = round(time.time() - started, 1)
            print("  -> %s, $%.4f, %.0f s" % (self.current["status"], self.current["cost_usd"],
                                            self.current["seconds"]))
        summary = {"claude_code": version, "budget_usd": self.args.budget,
                   "spent_usd": round(self.spent(), 4), "scenarios": self.results,
                   "measured_at": time.strftime("%Y-%m-%d %H:%M")}
        print("\nSpent $%.4f of $%.2f (as reported by Claude Code)" % (summary["spent_usd"],
                                                                     self.args.budget))
        if self.args.json:
            with open(self.args.json, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, default=str)
        if not self.args.keep:
            shutil.rmtree(self.tmp, onerror=lambda f, p, _i: (os.chmod(p, stat.S_IWRITE), f(p)))
        return 0 if all(item.get("status") == "passed" for item in self.results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--no-opus", action="store_true")
    parser.add_argument("--json", metavar="FILE")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    return LiveRouting(args).run()


if __name__ == "__main__":
    sys.exit(main())
