#!/usr/bin/env python3
"""BidStudio through the queue with the real Claude Code CLI. THIS COSTS MONEY.

    python tests/bid_live.py --budget 2.00 [--stages T1,T2] [--json result.json] [--keep]

On a throwaway project and data folder: a company profile, the synthetic tender package
in tests/fixtures/bid (RFP, addendum, vendor quotation), a bid project, and its first
stages queued as ordinary tasks (RFP intake, compliance). The runner executes them with
the real CLI; each task reads the stage's skill and records its results through
`moa7amed-queue bid` commands, which its scoped permissions allow. Then the script checks
what was recorded: requirements quoted from their documents (the engine verifies every
quote), the addendum handled by supersession, compliance answers that are not
restatements, and no company claim without evidence.
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

HERE = os.path.dirname(os.path.abspath(__file__))
MQ = os.path.join(os.path.dirname(HERE), "scripts", "mq.py")
FIX = os.path.join(HERE, "fixtures", "bid")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--per-task", type=float, default=1.5)
    parser.add_argument("--stages", default="T1,T2")
    parser.add_argument("--json")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    if shutil.which("claude") is None:
        print("bid_live: the `claude` command was not found")
        return 2
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mq-bid-live-"))
    home, project = os.path.join(tmp, "queue-home"), os.path.join(tmp, "bid-office")
    os.makedirs(home)
    os.makedirs(project)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MOA7AMED_QUEUE_", "CTQ_")) and key != "CLAUDE_PROJECT_DIR"}
    env.update(MOA7AMED_QUEUE_HOME=home, PYTHONIOENCODING="utf-8", DISABLE_AUTOUPDATER="1")
    with open(os.path.join(home, "config.json"), "w") as handle:
        json.dump({"claude_args": ["--safe-mode"], "max_budget_usd": args.per_task,
                   "cost_limit_run_usd": args.budget, "routing_classifier": "off",
                   "timeout_minutes": 20, "retry_backoff_seconds": 0,
                   "workflow_superpowers": "no"}, handle)
    for command in (["init", "-q", "-b", "main"], ["config", "user.name", "Bid Live"],
                    ["config", "user.email", "bid-live@example.invalid"]):
        subprocess.run(["git"] + command, cwd=project, check=True)
    with open(os.path.join(project, "README.md"), "w") as handle:
        handle.write("Bid office workspace (throwaway)\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=project, check=True)
    checks = []

    def mq(*arguments, expect=0):
        result = subprocess.run([sys.executable, "-B", MQ, "--project", project] + list(arguments),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, timeout=7200)
        out = result.stdout.decode("utf-8", "replace")
        if expect is not None and result.returncode != expect:
            raise SystemExit("`%s` exited %d: %s %s" % (" ".join(arguments[:3]), result.returncode,
                                                        out[-800:], result.stderr.decode()[-800:]))
        return out

    def check(ok, what, detail=None):
        checks.append({"check": what, "passed": bool(ok), "detail": detail})
        print("  [%s] %s%s" % ("ok  " if ok else "FAIL", what, "" if ok or detail is None
                               else "  (%s)" % (detail,)))

    mq("bid", "company", "new", "acme", "--name", "Acme Systems (fictitious)")
    mq("bid", "company", "apply", "acme", "--data", json.dumps({"items": [
        {"id": "C-CR", "type": "registration", "text": "Commercial registration 1010000000",
         "evidence": "CR certificate on file", "approved": True, "valid_until": "2027-06-30"},
        {"id": "C-REF-VMS", "type": "reference", "text": "Visitor system for Harbour Clinic (2024)",
         "evidence": "completion certificate", "approved": True}]}))
    mq("bid", "new", "b1", "--request", "Draft our technical and commercial offer for this tender.",
       "--company", "acme")
    mq("bid", "doc", "b1", os.path.join(FIX, "rfp-visitor-management.docx"), "--type", "rfp")
    mq("bid", "doc", "b1", os.path.join(FIX, "addendum-1.md"), "--type", "addendum", "--amends", "D1",
       "--date", "2026-11-05")
    mq("bid", "doc", "b1", os.path.join(FIX, "quotation-hardware.xlsx"), "--type", "quotation")
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
    os.environ.update(MOA7AMED_QUEUE_HOME=home)
    from mqlib import bid_store, store, util
    q = store.Queue(util.resolve_project_root(project))
    stages = tuple(args.stages.split(","))
    queued = bid_store.queue_pipeline(q, "b1", os.path.dirname(HERE), stages)
    q.close()
    check(len(queued["added"]) == len(stages), "stages %s queued" % ", ".join(stages))
    version = subprocess.run([shutil.which("claude"), "--version"], stdout=subprocess.PIPE,
                             env=env).stdout.decode().strip()
    print("Claude Code %s; budget $%.2f; data in %s" % (version, args.budget, tmp))
    code = subprocess.run([sys.executable, "-B", MQ, "--project", project, "run"], env=env,
                          stdin=subprocess.DEVNULL, timeout=7200).returncode
    status = json.loads(mq("bid", "status", "b1", "--json"))
    check(code == 0 and status["queue"].get("done", 0) == len(stages),
          "every stage task done (runner exit %s)" % code, status["queue"])
    document = json.loads(mq("bid", "show", "b1"))
    requirements = document.get("requirements") or []
    check(len(requirements) >= 15, "%d requirements recorded" % len(requirements))
    findings = json.loads(mq("bid", "check", "b1", "--json", expect=None))
    quotes = [f for f in findings if f["code"] in ("quote-not-found", "no-source")]
    check(not quotes, "every explicit requirement is quoted from its document", quotes[:3])
    superseded = [r for r in requirements if r.get("supersedes")]
    check(any("90" in (r.get("text") or "") for r in superseded),
          "the addendum's 90-day delivery supersedes the original clause",
          [r.get("text") for r in superseded])
    by_cat = {}
    for r in requirements:
        by_cat[r.get("category")] = by_cat.get(r.get("category"), 0) + 1
    check(by_cat.get("eligibility", 0) + by_cat.get("certification", 0) >= 2,
          "eligibility and certification requirements found", by_cat)
    if "T2" in stages:
        matrix = document.get("compliance") or []
        check(len(matrix) >= len([r for r in requirements if r.get("mandatory")
                                  and not r.get("superseded_by")]) * 0.8,
              "%d compliance entries" % len(matrix))
        bad = [f for f in findings if f["code"] in ("restates-requirement", "unsupported-claim",
                                                     "unapproved-claim")]
        check(not bad, "no restated answers or unsupported claims", bad[:3])
        iso = [row for row in matrix if "27001" in json.dumps(
            [r for r in requirements if r["id"] == row["id"]])]
        check(iso and iso[0]["status"] != "COMPLIANT",
              "ISO 27001 (no evidence in the profile) is not claimed as compliant",
              [row.get("status") for row in iso])
    spent = 0.0
    projects = os.path.join(home, "projects")
    for folder in os.listdir(projects):
        conn = sqlite3.connect(os.path.join(projects, folder, "queue.db"))
        spent += conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM runs").fetchone()[0]
        conn.close()
    print("\nspent (reported by Claude Code): $%.4f" % spent)
    summary = {"claude_code": version, "spent_usd_reported": round(spent, 4), "checks": checks,
               "requirements": len(requirements), "categories": by_cat,
               "status": status, "data": tmp}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, default=str)
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(item["passed"] for item in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
