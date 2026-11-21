"""Planning documents: Markdown for people, JSON for tools. Generated from the stored plan;
the database stays the single source of truth, so these files can always be regenerated."""
from __future__ import annotations

import json
import os

from . import plan_catalog as catalog
from . import plan_model as model


def _status(item):
    return item.get("status") or "UNKNOWN"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(":--" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell if cell is not None else "").replace("|", "\\|")
                                     .replace("\n", " ") for cell in row) + " |")
    return out


def requirements_md(plan):
    lines = ["# Requirements: %s" % plan.get("title"), "",
             "Evidence classes: VERIFIED (source code, documents, measurements), USER CONFIRMED "
             "(stated by the owner), ASSUMED (not confirmed), UNKNOWN (still needed), "
             "CONFLICTING (contradictory evidence).", ""]
    for kind in model.REQUIREMENT_KINDS:
        items = [item for item in plan.get("requirements") or [] if item.get("kind") == kind]
        if items:
            lines += ["## %s" % kind.replace("_", " ").capitalize(), ""]
            lines += _table(["ID", "Requirement", "Priority", "Evidence", "Source"],
                            [[item["id"], item.get("text"), item.get("priority", "must"),
                              _status(item), "; ".join(item.get("evidence") or [])
                              or item.get("source", "")] for item in items])
            lines.append("")
    facts = plan.get("facts") or []
    if facts:
        lines += ["## Facts", ""] + _table(["ID", "Topic", "Statement", "Evidence", "Source"], [
            [item["id"], item.get("topic"), item.get("statement"), _status(item),
             "; ".join(item.get("evidence") or [])] for item in facts]) + [""]
    missing = [item for item in facts + (plan.get("requirements") or [])
               if _status(item) in ("UNKNOWN", "CONFLICTING")]
    lines += ["## Missing or conflicting information", ""]
    lines += ["- %s: %s (%s)" % (item["id"], item.get("statement") or item.get("text"),
                                 _status(item).lower()) for item in missing] or ["- none recorded"]
    for question in plan.get("open_questions") or []:
        lines.append("- Open question: %s" % (question.get("text") if isinstance(question, dict)
                                              else question))
    return "\n".join(lines) + "\n"


def infrastructure_md(plan):
    infrastructure = plan.get("infrastructure") or {}
    lines = ["# Infrastructure: %s" % plan.get("title"), ""]
    target = infrastructure.get("target")
    lines.append("Deployment target: **%s**" % (target or "not decided: the scenarios below are "
                                                 "options, not facts"))
    lines.append("")
    for scenario in model.scenarios(plan):
        lines += ["## %s: %s%s" % (scenario["id"], scenario.get("label") or "",
                                   " (target)" if scenario["id"] == target else ""), ""]
        rows = []
        for field in catalog.ENV_FIELDS:
            entry = (scenario.get("env") or {}).get(field)
            if entry is None:
                continue
            value, status = catalog.env_value(scenario.get("env"), field)
            rows.append([field, value, status, entry.get("source", "") if isinstance(entry, dict)
                         else ""])
        lines += _table(["Field", "Value", "Evidence", "Source"], rows) if rows else \
            ["Nothing recorded."]
        if scenario.get("notes"):
            lines += ["", scenario["notes"]]
        lines.append("")
    machine = infrastructure.get("developer_machine")
    if machine:
        lines += ["## Developer machine (not the deployment target)", "",
                  "```", json.dumps(machine, indent=2), "```", ""]
    estimates = model.capacity((plan.get("capacity") or {}).get("inputs"))
    lines += ["## Capacity estimates", "",
              "Calculated from the inputs below. They are arithmetic on stated or assumed "
              "figures, not measurements: no configuration is shown to carry a load until it "
              "has been load-tested.", ""]
    inputs = (plan.get("capacity") or {}).get("inputs") or {}
    if inputs:
        lines += _table(["Input", "Value", "Evidence"], [
            [name, entry.get("value") if isinstance(entry, dict) else entry,
             entry.get("status") if isinstance(entry, dict) else "ASSUMED"]
            for name, entry in sorted(inputs.items())]) + [""]
    lines += _table(["Estimate", "Value", "Method", "Confidence"], [
        [item["name"], item["value"], item["method"], item["confidence"]] for item in estimates]) \
        if estimates else ["No workload inputs recorded."]
    return "\n".join(lines) + "\n"


def decision_md(plan, decision):
    options = decision.get("options") or []
    table = model.matrix(decision)
    lines = ["# %s: %s" % (decision["id"], decision.get("title")), "",
             "- Status: **%s**%s" % (decision.get("status"), " (material: needs the owner's "
                                     "approval)" if decision.get("material") else ""),
             "- Category: %s" % decision.get("category", "-"),
             "- Date: %s; plan revision %s" % (decision.get("date", "-"), decision.get("revision", "-"))]
    if decision.get("supersedes"):
        lines.append("- Supersedes: %s" % decision["supersedes"])
    if decision.get("superseded_by"):
        lines.append("- Superseded by: %s" % decision["superseded_by"])
    lines += ["", "## Context", "", decision.get("context") or "-", "",
              "Requirements addressed: %s" % (", ".join(decision.get("requirements") or []) or "-"),
              "", "Constraints: %s" % ("; ".join(decision.get("constraints") or []) or "-"), "",
              "## Options considered", ""]
    for option in options:
        verdicts = model.evaluate_option(plan, option)
        lines += ["### %s: %s" % (option["id"], option.get("name")), "",
                  option.get("summary") or "",
                  "", "Components: %s" % (", ".join(
                      "%s%s" % (catalog.component_id(item), " (needed by %s)" % ", ".join(item["why"])
                                if isinstance(item, dict) and item.get("why") else "")
                      for item in option.get("components") or []) or "-")]
        for scenario_id, result in verdicts.items():
            lines.append("- %s: **%s**" % (scenario_id, result["verdict"]))
            for finding in result["findings"]:
                if finding["verdict"] != "ok":
                    lines.append("  - %s (%s): %s" % (finding["component"], finding["basis"],
                                                     finding["reason"]))
        lines.append("")
    if table["options"]:
        criteria = decision.get("criteria") or []
        lines += ["## Decision matrix", "",
                  "Scores are 1 to 5. A missing score, or one resting on an assumption, counts as "
                  "a range; the totals are ranges for that reason.", ""]
        header = ["Option"] + ["%s (w %s)" % (item["name"], item.get("weight")) for item in criteria] \
            + ["Weighted total"]
        rows = []
        for option in options:
            scores = option.get("scores") or {}
            cells = []
            for item in criteria:
                entry = scores.get(item["name"])
                score = entry.get("score") if isinstance(entry, dict) else entry
                basis = entry.get("basis", "") if isinstance(entry, dict) else ""
                cells.append("%s %s" % (score if score is not None else "?",
                                        "(%s)" % basis.lower() if basis else ""))
            total = table["options"].get(option["id"], {})
            rows.append([option["id"]] + cells + [
                "%.2f" % total["low"] if total.get("low") == total.get("high")
                else "%.2f to %.2f" % (total.get("low", 0), total.get("high", 0))])
        lines += _table(header, rows) + [""]
        if table["note"]:
            lines += [table["note"], ""]
    lines += ["## Decision", "", "Selected: **%s**" % (decision.get("selected") or "none yet"), "",
              decision.get("rationale") or "-", ""]
    for item in decision.get("rejected") or []:
        lines.append("- Rejected %s: %s" % (item.get("option"), item.get("reason")))
    implications = decision.get("implications") or {}
    if implications:
        lines += ["", "## Implications", ""] + ["- %s: %s" % (key, value)
                                                 for key, value in implications.items()]
    for label, key in (("Risks", "risks"), ("Assumptions", "assumptions")):
        if decision.get(key):
            lines += ["", "## %s" % label, ""] + ["- %s" % item for item in decision[key]]
    status, reasons = model.suggested_status(plan, decision)
    if reasons:
        lines += ["", "## Missing before it can be approved", ""] + ["- %s" % item for item in reasons]
    return "\n".join(lines) + "\n"


def roadmap_md(plan, progress=None):
    states = {row["task"]: row for row in (progress or {}).get("tasks", [])}
    lines = ["# Roadmap: %s" % plan.get("title"), ""]
    tasks = plan.get("tasks") or []
    milestones = plan.get("milestones") or [{"id": None, "title": "Tasks"}]
    for milestone in milestones:
        mine = [task for task in tasks if task.get("milestone") == milestone.get("id")
                or (milestone.get("id") is None)]
        if not mine:
            continue
        lines += ["## %s%s" % ("%s: " % milestone["id"] if milestone.get("id") else "",
                               milestone.get("title")), ""]
        if milestone.get("goal"):
            lines += [milestone["goal"], ""]
        lines += _table(["Task", "Title", "Depends on", "Decisions", "Risk", "Effort", "Model",
                         "State"], [
            [task["id"], task.get("title"), ", ".join(task.get("depends_on") or []),
             ", ".join(task.get("decisions") or []), task.get("risk", ""),
             "%s %s (%s)" % ((task.get("effort") or {}).get("estimate", ""),
                             (task.get("effort") or {}).get("unit", ""),
                             (task.get("effort") or {}).get("uncertainty", ""))
             if task.get("effort") else "", task.get("model") or "auto",
             (states.get(task["id"]) or {}).get("state", "")] for task in mine]) + [""]
    graph = model.task_graph(plan)
    lines += ["## Dependency graph", "", "```mermaid", "graph TD"]
    for task in tasks:
        lines.append("  %s[\"%s: %s\"]" % (task["id"], task["id"],
                                          (task.get("title") or "").replace('"', "'")[:50]))
        for dep in task.get("depends_on") or []:
            lines.append("  %s --> %s" % (dep, task["id"]))
    lines += ["```", ""]
    if graph["cycles"]:
        lines += ["Circular dependencies: %s" % "; ".join(" -> ".join(cycle)
                                                         for cycle in graph["cycles"]), ""]
    return "\n".join(lines) + "\n"


def report_md(plan, findings, progress=None, approvals=()):
    depth, depth_reasons = model.planning_depth(plan)
    lines = ["# Project plan: %s" % plan.get("title"), "",
             "Plan `%s`, %s project, planning depth **%s** (%s)." % (
                 plan.get("id"), plan.get("mode"), depth, "; ".join(depth_reasons)), ""]
    sections = []

    def section(title, body):
        sections.append((title, body))

    section("Executive summary", plan.get("summary") or "-")
    req = plan.get("requirements") or []
    counts = {}
    for item in req:
        counts[_status(item)] = counts.get(_status(item), 0) + 1
    section("Requirements analysis", "%d requirement(s): %s. Details in requirements.md." % (
        len(req), ", ".join("%d %s" % (number, status.lower()) for status, number in counts.items())
        or "none recorded"))
    existing = [item for item in plan.get("facts") or [] if item.get("source") == "discovery"]
    section("Existing system assessment", "\n".join("- %s (%s)" % (item["statement"],
                                                                   _status(item).lower())
                                                    for item in existing[:25])
            or ("not applicable (new project)" if plan.get("mode") == "new" else "no discovery yet"))
    target = model.target_scenario(plan)
    section("Infrastructure and hardware findings", "Target: %s. %d scenario(s). Details in "
            "infrastructure.md." % (target["id"] if target else "not decided",
                                    len(model.scenarios(plan))))
    estimates = model.capacity((plan.get("capacity") or {}).get("inputs"))
    section("Capacity planning", "\n".join("- %s: %s (%s; %s)" % (
        item["name"], item["value"], item["method"], item["confidence"]) for item in estimates)
        or "no workload inputs recorded" + "\n\nRecommendation: load-test before committing to "
        "a configuration.")
    decisions = plan.get("decisions") or []
    section("Proposed architecture", "\n".join("- %s %s: %s -> %s" % (
        item["id"], item.get("status"), item.get("title"), item.get("selected") or "undecided")
        for item in decisions) or "no decisions recorded")
    section("Alternative architecture comparison", "Each decision lists its options, their "
            "compatibility with every scenario, and a decision matrix: decisions/*.md.")
    section("Technology selection rationale", "\n".join("- %s: %s" % (
        item["id"], item.get("rationale") or "-") for item in decisions) or "-")
    storage = [item for item in decisions if item.get("category") in ("database", "storage")]
    section("Database and storage strategy", "\n".join("- %s: %s" % (
        item["id"], item.get("rationale") or item.get("title")) for item in storage)
        or "no database or storage decision recorded")
    security = [item for item in req if item.get("kind") == "security"]
    section("Security and deployment implications", "\n".join("- %s: %s" % (
        item["id"], item.get("text")) for item in security) or "no security requirements recorded")
    assumptions = [item for item in plan.get("facts") or [] if _status(item) == "ASSUMED"]
    section("Risks and assumptions", "\n".join(["- Risk: %s" % (item.get("text") if isinstance(
        item, dict) else item) for item in plan.get("risks") or []] + [
        "- Assumption %s: %s" % (item["id"], item.get("statement")) for item in assumptions])
        or "none recorded")
    costs = [(item["id"], (item.get("implications") or {}).get("cost")) for item in decisions]
    section("Cost considerations", "\n".join("- %s: %s" % (ref, cost) for ref, cost in costs if cost)
            or "no cost implications recorded")
    section("Project phases and milestones", "\n".join("- %s: %s" % (
        item["id"], item.get("title")) for item in plan.get("milestones") or []) or "-")
    section("Detailed task breakdown", "%d task(s); see roadmap.md and tasks.json."
            % len(plan.get("tasks") or []))
    section("Dependency graph", "See roadmap.md (Mermaid).")
    section("Acceptance criteria", "\n".join("- %s: %s" % (task["id"], "; ".join(
        task.get("acceptance") or ["-"])) for task in plan.get("tasks") or []) or "-")
    tests = sorted({item for task in plan.get("tasks") or [] for item in task.get("tests") or []})
    section("Test strategy", "\n".join("- %s" % item for item in tests) or "no tests recorded")
    outstanding = [item for item in findings if item["level"] in ("error", "warning")]
    open_decisions = [item for item in decisions if item.get("status") in ("PROPOSED",
                                                                          "NEEDS_INFORMATION")]
    section("Outstanding decisions", "\n".join(["- %s (%s): %s" % (
        item["id"], item.get("status"), item.get("title")) for item in open_decisions] + [
        "- %s %s: %s" % (item["level"], item["where"], item["message"]) for item in outstanding])
        or "none")
    section("Approval status", "\n".join("- revision %s approved %s for %s (sha256 %s...)" % (
        item["revision"], item["approved_at"], item["scope"], item["sha256"][:12])
        for item in approvals) or "not approved")
    stage = (progress or {}).get("stage") or model.stage(plan)
    section("Execution readiness", "Stage: **%s**. %s" % (stage, (
        "Errors must be fixed before approval." if any(item["level"] == "error" for item in findings)
        else "No blocking errors.")))
    for number, (title, body) in enumerate(sections, 1):
        lines += ["## %d. %s" % (number, title), "", body, ""]
    return "\n".join(lines)


def export(plan, folder, findings, progress=None, approvals=()):
    """Write the planning documents to `folder`. Returns the files written."""
    written = []

    def put(relative, text):
        path = os.path.join(folder, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append(path)

    put("report.md", report_md(plan, findings, progress, approvals))
    put("requirements.md", requirements_md(plan))
    put("infrastructure.md", infrastructure_md(plan))
    put("architecture.md", "# Architecture: %s\n\n" % plan.get("title") + "\n".join(
        "- [%s](decisions/%s.md) %s: %s" % (item["id"], item["id"], item.get("status"),
                                            item.get("title")) for item in plan.get("decisions") or [])
        + "\n")
    for decision in plan.get("decisions") or []:
        put("decisions/%s.md" % decision["id"], decision_md(plan, decision))
    put("roadmap.md", roadmap_md(plan, progress))
    put("tasks.json", json.dumps({"plan": plan.get("id"), "tasks": plan.get("tasks") or [],
                                  "milestones": plan.get("milestones") or []}, indent=2,
                                 ensure_ascii=False) + "\n")
    put("risks.md", "# Risks\n\n" + "\n".join("- %s" % (item.get("text") if isinstance(item, dict)
                                                         else item) for item in plan.get("risks") or [])
        + "\n")
    put("assumptions.md", "# Assumptions\n\n" + "\n".join(
        "- %s: %s" % (item["id"], item.get("statement") or item.get("text"))
        for item in (plan.get("facts") or []) + (plan.get("requirements") or [])
        if _status(item) == "ASSUMED") + "\n")
    if progress:
        put("progress.md", "# Progress\n\nStage: %s\n\n" % progress["stage"] + "\n".join(
            _table(["Task", "Title", "State", "Queue task", "Reason"], [
                [row["task"], row["title"], row["state"], row["queue_task"] or "", row["reason"]]
                for row in progress["tasks"]])) + "\n")
    put("plan.json", json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    return written
