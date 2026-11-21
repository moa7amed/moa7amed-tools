#!/usr/bin/env python3
"""Replica end to end with the real Claude Code CLI and a real browser. THIS COSTS MONEY.

    python tests/replica_e2e.py --budget 3.00
    python tests/replica_e2e.py --budget 3.00 --json result.json --keep

The reference is the sample application in tests/fixtures/replica_sample (owned by this
repository, synthetic data), opened as file:// pages. In a throwaway git repository and
data folder, the script drives the real commands:

    1. replica new          the request, kept verbatim
    2. replica capture      the two reference screens, with Playwright
    3. replica analyze      the reconnaissance recorded for this fixture (below): what the
                            captures show, with evidence classes. In a session this is
                            Claude's work (replica-recon); here it is fixed so the run is
                            reproducible
    4. replica tokens       design tokens from the captures
    5. replica plan         tasks into the linked project plan; infrastructure and one
                            architecture decision through `project apply`; `project
                            approve --yes`; `project execute --yes`
    6. run                  the queue builds the rebuild with the real CLI (routed models,
                            workflows, verification, commits)
    7. replica compare      every screen against its reference
    8. functional checks    a real browser exercises the rebuild (filter, search, details,
                            open a shipment) and records real test results
    9. replica improve      bounded correction rounds with the real CLI, then compare again
   10. replica report

Every task runs with `claude --safe-mode`, a per-task --max-budget-usd and a session limit.
Costs are what Claude Code reports.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MQ = os.path.join(os.path.dirname(HERE), "scripts", "mq.py")
SAMPLE = os.path.join(HERE, "fixtures", "replica_sample")


def file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")


INVENTORY = {
    "screens": [
        {"id": "S1", "name": "Shipments list", "evidence": "OBSERVED", "refs": ["E3", "E4"],
         "route": "index.html",
         "layout": "full-width blue top bar (brand 'Parcel Desk' left, links 'Shipments' "
                   "(underlined, active) and 'Last opened'); light grey page; centred content "
                   "column about 910px wide; heading 'Shipments'; filter row with a 'Status' "
                   "select (All, In transit, Delivered, Delayed), a 'Search' box with "
                   "placeholder 'Reference or city' and a blue 'Apply' button; white table "
                   "with a subtle shadow, uppercase grey headers Reference, From, To, Status; "
                   "five rows; reference links in blue; status shown as rounded badges "
                   "(grey In transit, green Delivered, red Delayed)",
         "components": ["top bar", "filter form", "data table", "status badges"],
         "actions": ["filter by status", "search by reference or city", "open a shipment"],
         "navigates_to": ["S2"],
         "data": "rows: SH-1001 Riyadh-Jeddah In transit; SH-1002 Cairo-Alexandria Delivered; "
                 "SH-1003 Dubai-Abu Dhabi Delayed; SH-1004 Amman-Aqaba In transit; "
                 "SH-1005 Doha-Riyadh Delivered (synthetic sample data)"},
        {"id": "S2", "name": "Shipment details", "evidence": "OBSERVED", "refs": ["E5", "E6"],
         "route": "details.html?id=SH-1002",
         "layout": "same top bar with 'Last opened' active; blue link '<- Back to shipments'; "
                   "heading 'Shipment SH-1002'; white card with a two-column list: From Cairo, "
                   "To Alexandria, Status Delivered, Weight 3 kg, Carrier Delta Post; labels grey",
         "components": ["top bar", "back link", "detail card"], "actions": ["go back"],
         "navigates_to": ["S1"],
         "unknown": ["weight and carrier of shipments other than SH-1002"]}],
    "features": [
        {"id": "F1", "name": "Filter shipments by status", "screens": ["S1"],
         "evidence": "OBSERVED", "refs": ["E3", "E4"],
         "behavior": "Apply shows only rows with the chosen status"},
        {"id": "F2", "name": "Search by reference or city", "screens": ["S1"],
         "evidence": "OBSERVED", "refs": ["E4"],
         "behavior": "Apply shows rows whose reference, origin or destination contain the text"},
        {"id": "F3", "name": "Shipment details from the address", "screens": ["S2"],
         "evidence": "OBSERVED", "refs": ["E5"],
         "behavior": "details.html?id=<reference> shows that shipment's fields"}],
    "flows": [
        {"id": "U1", "name": "Open a shipment from the list", "evidence": "OBSERVED",
         "refs": ["E3", "E5"],
         "steps": [{"screen": "S1", "action": "click a reference link"},
                   {"screen": "S2", "action": "the shipment's details are shown"}],
         "outcome": "the details screen of that shipment"}],
    "unknowns": ["user roles, persistence and any server behind the sample"],
    "sample_data": {"synthetic": True, "evidence": "USER_CONFIRMED",
                    "note": "the sample's five shipments are invented and owned by this repository"}}

DECISION = {
    "infrastructure": {"target": "static", "scenarios": [{"id": "static", "label": "static file hosting",
                                                          "env": {"hosting": {"value": "static", "status": "USER_CONFIRMED", "source": "e2e"},
                                                                  "os": {"value": "linux", "status": "USER_CONFIRMED", "source": "e2e"}}}]},
    "decisions": [{"id": "ADR-1", "title": "How the rebuild is delivered", "category": "architecture",
                   "options": [{"id": "static", "name": "static HTML, CSS and JavaScript", "components": [],
                                "style": "monolith"},
                               {"id": "node", "name": "Node.js with a server", "components": ["node"],
                                "style": "monolith"}],
                   "selected": "static", "status": "PROPOSED",
                   "rationale": "the reference behaves entirely in the browser with fixed sample data; "
                                "no server behaviour was observed",
                   "requirements": ["R-F1"]}]}

CHECKS = r"""
import json, sys
from playwright.sync_api import sync_playwright
base = sys.argv[1]
out = {}
def launch(p):
    for kwargs in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return p.chromium.launch(**kwargs)
        except Exception:
            pass
    raise SystemExit("no browser")
def rows(page):
    return [r.inner_text() for r in page.locator("tbody tr").all() if r.is_visible()]
def apply(page):
    button = page.get_by_role("button", name="Apply")
    if button.count():
        button.first.click()
    page.wait_for_timeout(400)
with sync_playwright() as p:
    browser = launch(p)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.goto(base + "index.html"); page.wait_for_timeout(300)
        page.locator("select").first.select_option(label="Delayed"); apply(page)
        found = rows(page)
        out["F1"] = (len(found) == 1 and "Delayed" in found[0], found[:3])
    except Exception as exc:
        out["F1"] = (False, str(exc)[:200])
    try:
        page.goto(base + "index.html"); page.wait_for_timeout(300)
        page.locator("input[type=search], input[type=text]").first.fill("Cairo"); apply(page)
        found = rows(page)
        out["F2"] = (len(found) >= 1 and all("Cairo" in r for r in found), found[:3])
    except Exception as exc:
        out["F2"] = (False, str(exc)[:200])
    try:
        page.goto(base + "details.html?id=SH-1002"); page.wait_for_timeout(300)
        text = page.inner_text("body")
        out["F3"] = (all(v in text for v in ("Cairo", "Alexandria", "Delta Post", "3 kg")), text[:120])
    except Exception as exc:
        out["F3"] = (False, str(exc)[:200])
    try:
        page.goto(base + "index.html"); page.wait_for_timeout(300)
        page.get_by_text("SH-1003", exact=True).first.click(); page.wait_for_timeout(500)
        text = page.inner_text("body")
        out["U1"] = ("SH-1003" in text and "Dubai" in text and "details" in page.url, page.url[-40:])
    except Exception as exc:
        out["U1"] = (False, str(exc)[:200])
    browser.close()
print(json.dumps(out))
"""


class Stop(Exception):
    pass


class E2E(object):

    def __init__(self, args):
        self.args = args
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-replica-e2e-"))
        self.home = os.path.join(self.tmp, "queue-home")
        self.project = os.path.join(self.tmp, "rebuild")
        os.makedirs(self.home)
        os.makedirs(self.project)
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(("MOA7AMED_QUEUE_", "CTQ_")) or key == "CLAUDE_PROJECT_DIR":
                del self.env[key]
        self.env.update({"MOA7AMED_QUEUE_HOME": self.home, "PYTHONIOENCODING": "utf-8",
                         "DISABLE_AUTOUPDATER": "1"})
        with open(os.path.join(self.home, "config.json"), "w") as handle:
            json.dump({"claude_args": ["--safe-mode"], "max_budget_usd": args.per_task,
                       "cost_limit_run_usd": args.budget, "routing_classifier": "off",
                       "timeout_minutes": 15, "retry_backoff_seconds": 0,
                       "workflow_superpowers": "no", "replica_max_iterations": args.rounds,
                       "replica_fixes_per_iteration": 2, "replica_budget_usd": args.budget * 0.9},
                      handle)
        for command in (["init", "-q", "-b", "main"], ["config", "user.name", "Replica E2E"],
                        ["config", "user.email", "replica-e2e@example.invalid"]):
            subprocess.run(["git"] + command, cwd=self.project, check=True)
        with open(os.path.join(self.project, "README.md"), "w", newline="\n") as handle:
            handle.write("# Parcel Desk rebuild\n\nBuilt by moa7amed-queue Replica from the sample "
                         "reference.\n")
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=self.project, check=True)
        self.checks, self.timings = [], {}

    def mq(self, *arguments, expect=0, data=None):
        command = [sys.executable, "-B", MQ, "--project", self.project] + list(arguments)
        result = subprocess.run(command, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=3600)
        out = result.stdout.decode("utf-8", "replace")
        if expect is not None and result.returncode != expect:
            raise Stop("`%s` exited %d: %s %s" % (" ".join(arguments[:3]), result.returncode,
                                                 out[-600:], result.stderr.decode("utf-8", "replace")[-600:]))
        return out

    def check(self, condition, what, detail=None):
        self.checks.append({"check": what, "passed": bool(condition), "detail": detail})
        print("  [%s] %s%s" % ("ok  " if condition else "FAIL", what,
                               "" if condition or detail is None else "  (%s)" % (detail,)))
        return condition

    def spent(self):
        total = 0.0
        projects = os.path.join(self.home, "projects")
        for folder in os.listdir(projects) if os.path.isdir(projects) else []:
            conn = sqlite3.connect(os.path.join(projects, folder, "queue.db"))
            try:
                total += conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs").fetchone()[0]
            finally:
                conn.close()
        return total

    def timed(self, name, function):
        start = time.time()
        try:
            return function()
        finally:
            self.timings[name] = round(time.time() - start, 1)

    def run(self):
        version = subprocess.run([shutil.which("claude") or "claude", "--version"],
                                 stdout=subprocess.PIPE, env=self.env).stdout.decode().strip()
        print("Claude Code %s; budget $%.2f; data in %s" % (version, self.args.budget, self.tmp))
        index, details = file_url(os.path.join(SAMPLE, "index.html")), \
            file_url(os.path.join(SAMPLE, "details.html")) + "?id=SH-1002"
        request = "Reconstruct this app from its URL and these screenshots: %s" % index
        out = self.mq("replica", "new", "r1", "--request", request, "--ownership", "own",
                      "--quality", "prototype", "--json")
        project = json.loads(out)
        self.check(project["mode"] == "functional", "the request is a functional reconstruction",
                   project["mode"])
        captured = json.loads(self.timed("capture", lambda: self.mq(
            "replica", "capture", "r1", index, details, "--json")))
        self.check(all(item["ok"] for item in captured), "both reference screens captured",
                   [item.get("error") for item in captured])
        refs = {item["id"]: item for item in json.loads(self.mq("replica", "ref", "r1", "--json"))}
        shots = [(item["screenshot"], item["html"]) for item in captured]
        self.check(all(refs.get(shot, {}).get("kind") == "screenshot" for shot, _ in shots),
                   "screenshots stored as references %s" % [shot for shot, _ in shots], sorted(refs))
        inventory = json.loads(json.dumps(INVENTORY))
        inventory["screens"][0]["refs"] = list(shots[0])
        inventory["screens"][1]["refs"] = list(shots[1])
        for feature in inventory["features"]:
            feature["refs"] = list(shots[0] if "S1" in feature["screens"] else shots[1])
        inventory["flows"][0]["refs"] = [shots[0][0], shots[1][0]]
        analysis = json.loads(self.mq("replica", "analyze", "r1", "--data", json.dumps(inventory),
                                      "--json"))
        self.check(not [f for f in analysis["findings"] if f["level"] == "error"],
                   "inventory accepted (2 screens, 3 features, 1 flow)", analysis["findings"])
        tokens = json.loads(self.mq("replica", "tokens", "r1", "--json"))
        self.check("#2b6cb0" in [t["value"] for t in tokens["tokens"]], "accent colour measured")
        self.mq("replica", "plan", "r1")
        self.mq("project", "apply", "r1", "--data", json.dumps(DECISION))
        check = self.mq("project", "check", "r1", expect=None)
        approved = self.mq("project", "approve", "r1", "--yes", expect=None)
        self.check("pproved" in approved, "plan approved by the owner (scripted --yes)",
                   approved[-300:] + check[-300:])
        imported = json.loads(self.mq("project", "execute", "r1", "--yes", "--json"))
        self.check(len(imported["added"]) >= 3, "%d tasks imported into the queue"
                   % len(imported["added"]))
        workflow = self.mq("workflow")
        print(workflow)
        code = self.timed("build", lambda: subprocess.run(
            [sys.executable, "-B", MQ, "--project", self.project, "run"], env=self.env,
            stdin=subprocess.DEVNULL, timeout=7200).returncode)
        status = json.loads(self.mq("replica", "status", "r1", "--json"))
        self.check(code == 0 and status["queue"].get("done", 0) == len(imported["added"]),
                   "every imported task done (runner exit %s)" % code, status["queue"])
        self.mq("replica", "config", "r1", "--candidate-url", file_url(self.project) + "/")
        compare = json.loads(self.timed("compare", lambda: self.mq("replica", "compare", "r1",
                                                                  "--all", "--json", expect=None)))
        before = {item["screen"]: item.get("score") for item in compare}
        print("  first comparison: %s" % before)
        self.check(all(isinstance(value, float) for value in before.values()),
                   "both screens of the rebuild captured and compared", compare)
        self.functional("after the build")
        rounds = json.loads(self.timed("improve", lambda: self.mq("replica", "improve", "r1",
                                                                 "--run", "--json", expect=None)))
        after = rounds[-1]["scores"]
        print("  rounds: %s" % [(item["action"], item.get("reason") or item.get("tasks"))
                                for item in rounds])
        self.check(rounds[-1]["action"] == "stop", "the improvement loop stopped by itself: %s"
                   % rounds[-1].get("reason"))
        self.functional("after the improvement rounds")
        report = self.mq("replica", "report", "r1")
        with open(os.path.join(self.tmp, "replica-report.md"), "w", encoding="utf-8") as handle:
            handle.write(report)
        sweep = self.mq("replica", "sweep", "r1", expect=None)
        summary = {"claude_code": version, "budget_usd": self.args.budget,
                   "spent_usd_reported": round(self.spent(), 4), "scores_first": before,
                   "scores_last": after, "rounds": rounds, "timings_s": self.timings,
                   "checks": self.checks, "status": json.loads(self.mq("replica", "status", "r1",
                                                                       "--json")),
                   "report": report, "sweep": sweep, "data": self.tmp}
        print("\nspent (reported by Claude Code): $%.4f" % summary["spent_usd_reported"])
        return summary

    def functional(self, label):
        python = sys.executable
        result = subprocess.run([python, "-c", CHECKS, file_url(self.project) + "/"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        try:
            found = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.check(False, "functional checks ran (%s)" % label,
                       result.stderr.decode("utf-8", "replace")[-300:])
            return
        for key, (passed, detail) in sorted(found.items()):
            option = "--flow" if key.startswith("U") else "--feature"
            self.mq("replica", "test", "r1", option, key, "--result", "pass" if passed else "fail",
                    "--kind", "real", "--name", "e2e browser check %s (%s)" % (key, label),
                    "--evidence", "replica_e2e.py Playwright check")
            self.check(passed, "%s works in a real browser (%s)" % (key, label), detail)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--per-task", type=float, default=1.0)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--json")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    if shutil.which("claude") is None:
        print("replica_e2e: the `claude` command was not found")
        return 2
    runner = E2E(args)
    try:
        summary = runner.run()
        ok = all(item["passed"] for item in runner.checks)
    except Stop as exc:
        print("STOPPED: %s" % exc)
        summary = {"stopped": str(exc), "checks": runner.checks,
                   "spent_usd_reported": round(runner.spent(), 4), "data": runner.tmp}
        ok = False
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
    if not args.keep:
        shutil.rmtree(runner.tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
