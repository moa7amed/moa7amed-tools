"""The project plan: what it contains, and the rules it has to satisfy.

A plan is one JSON document (stored by plan_store, one immutable copy per approved
revision). The analysis in it is written by a person or by Claude in a planning skill;
this module does not invent any of it. What it does is check it:

  * every fact and requirement carries an evidence class (VERIFIED, USER_CONFIRMED,
    ASSUMED, UNKNOWN, CONFLICTING), and nothing weaker than USER_CONFIRMED is treated as
    settled;
  * architecture options are checked against each infrastructure scenario
    (plan_catalog), optional components must name the requirement that needs them, and
    a decision matrix with missing or unsupported scores gives a range, not a number;
  * decisions (ADRs) move through PROPOSED / NEEDS_INFORMATION / APPROVED / SUPERSEDED /
    REJECTED by rules, and APPROVED only through an explicit approval;
  * tasks form an acyclic dependency graph, link to requirements and decisions, have
    acceptance criteria, and are held while a decision they depend on is not approved.

Document outline (fields not listed are kept as given):

    id, title, mode (new | existing), summary, depth (quick | standard | deep), depth_source
    facts:        [{id, topic, statement, status, evidence: [..], source}]
    requirements: [{id, kind, text, priority (must | should | could), status, evidence,
                    source, tags: [..]}]
    infrastructure: {target: scenario id | null,
                     scenarios: [{id, label, status, env: {field: {value, status, source}},
                                  notes}],
                     developer_machine: {...}}            (never treated as the target)
    capacity:     {inputs: {name: {value, status, source}}}
    decisions:    [{id, title, category, material, context, requirements, constraints,
                    criteria: [{name, weight}], options: [{id, name, style, summary,
                    components: [id | {id, why: [..], ...}], resources: {..},
                    scores: {criterion: {score 1-5 | null, basis, rationale}}}],
                    selected, rejected: [{option, reason}], rationale, implications,
                    risks, assumptions, facts: [fact ids], status, supersedes}]
    milestones:   [{id, title, goal}]
    tasks:        [{id, title, description, milestone, epic, business_objective,
                    technical_objective, requirements, decisions, depends_on, priority,
                    effort: {estimate, unit, uncertainty}, model, files, acceptance: [..],
                    tests: [..], risk, done: [..], kind, components: [..]}]
    risks, open_questions: [..]
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from . import plan_catalog as catalog

EVIDENCE = ("VERIFIED", "USER_CONFIRMED", "ASSUMED", "UNKNOWN", "CONFLICTING")
SETTLED = ("VERIFIED", "USER_CONFIRMED")
REQUIREMENT_KINDS = ("functional", "non_functional", "security", "performance", "availability",
                     "scalability", "compliance", "integration", "operational", "deployment",
                     "budget")
DECISION_STATUSES = ("PROPOSED", "NEEDS_INFORMATION", "APPROVED", "SUPERSEDED", "REJECTED")
# Decisions of these kinds always need the owner's explicit approval.
MATERIAL_CATEGORIES = ("architecture", "infrastructure", "database", "framework", "deployment",
                       "licensing", "data-migration", "security")
TASK_KINDS = ("implementation", "infrastructure", "analysis", "documentation", "test", "migration")
DEPTHS = ("quick", "standard", "deep")
PRIORITY_VALUES = {"low": -10, "normal": 0, "high": 10, "urgent": 20}
VAGUE = re.compile(r"^\s*(implement|build|create|do|make|finish|develop|write)\s+(the\s+)?"
                   r"(backend|frontend|app|application|system|everything|rest|ui|api|"
                   r"database|project|feature|features)\s*\.?\s*$", re.I)


def new_plan(plan_id, title, mode="new"):
    return {"id": plan_id, "title": title, "mode": mode, "summary": "", "depth": None,
            "depth_source": None, "facts": [], "requirements": [],
            "infrastructure": {"target": None, "scenarios": []}, "capacity": {"inputs": {}},
            "decisions": [], "milestones": [], "tasks": [], "risks": [], "open_questions": [],
            "existing_components": []}


def content_hash(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def by_id(items):
    return {item.get("id"): item for item in items or [] if isinstance(item, dict)}


# --------------------------------------------------------------------------- infrastructure

def scenarios(plan):
    return [item for item in (plan.get("infrastructure") or {}).get("scenarios") or []
            if isinstance(item, dict)]


def target_scenario(plan):
    infrastructure = plan.get("infrastructure") or {}
    target = infrastructure.get("target")
    return by_id(scenarios(plan)).get(target) if target else None


def evaluate_option(plan, option):
    """{scenario id: check result} for one option, over every scenario (or the target)."""
    results = {}
    requirements = plan.get("requirements") or []
    for scenario in scenarios(plan):
        results[scenario["id"]] = catalog.check_option(option, scenario.get("env") or {},
                                                       requirements)
    return results


# --------------------------------------------------------------------------- decision matrix

def matrix(decision):
    """Weighted scores of each option. A missing score, or one resting on an ASSUMED or
    UNKNOWN basis, is counted as anything from 1 to 5: the result is a range. Returns
    {"options": {id: {"low", "high", "complete"}}, "decisive": bool, "leader": id | None,
     "note": str}."""
    criteria = [item for item in decision.get("criteria") or [] if isinstance(item, dict)]
    total_weight = sum(float(item.get("weight") or 0) for item in criteria) or 0.0
    result = {"options": {}, "decisive": False, "leader": None, "note": ""}
    if not criteria or total_weight <= 0:
        result["note"] = "no weighted criteria: the matrix says nothing"
        return result
    for option in decision.get("options") or []:
        low = high = 0.0
        complete = True
        scores = option.get("scores") or {}
        for criterion in criteria:
            weight = float(criterion.get("weight") or 0) / total_weight
            entry = scores.get(criterion.get("name"))
            score = entry.get("score") if isinstance(entry, dict) else entry
            basis = (entry.get("basis") or "ASSUMED") if isinstance(entry, dict) else "ASSUMED"
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = None
            if score is None or basis in ("UNKNOWN", "CONFLICTING"):
                low += weight * 1
                high += weight * 5
                complete = False
            elif basis == "ASSUMED":
                low += weight * max(1.0, score - 1)
                high += weight * min(5.0, score + 1)
                complete = False
            else:
                low += weight * score
                high += weight * score
        result["options"][option.get("id")] = {"low": round(low, 2), "high": round(high, 2),
                                               "complete": complete}
    ranked = sorted(result["options"].items(), key=lambda item: -item[1]["low"])
    if len(ranked) >= 2:
        first, second = ranked[0], ranked[1]
        result["leader"] = first[0]
        result["decisive"] = first[1]["low"] > max(item[1]["high"] for item in ranked[1:])
        if not result["decisive"]:
            result["note"] = ("the ranges of %s and %s overlap: the scores do not decide between "
                              "them; better evidence does" % (first[0], second[0]))
    elif ranked:
        result["leader"], result["decisive"] = ranked[0][0], True
    return result


# --------------------------------------------------------------------------- planning depth

def planning_depth(plan, request=""):
    """quick | standard | deep, with the reasons. Chosen from what the plan and the request
    contain; a manual choice (depth_source = manual) is kept."""
    if plan.get("depth") in DEPTHS and plan.get("depth_source") == "manual":
        return plan["depth"], ["chosen manually"]
    reasons = []
    text = (request or "") + " " + (plan.get("summary") or "")
    kinds = {item.get("kind") for item in plan.get("requirements") or []}
    deep_signals = [
        (plan.get("mode") == "new", "a new system"),
        (bool(kinds & {"security", "compliance"}), "security or compliance requirements"),
        (bool(kinds & {"availability", "scalability"}), "availability or scalability requirements"),
        (len([item for item in plan.get("requirements") or []
              if item.get("kind") == "integration"]) >= 2, "several integrations"),
        (any(item.get("category") in ("infrastructure", "database", "deployment")
             for item in plan.get("decisions") or []), "infrastructure or database decisions"),
        (bool(re.search(r"\b(migrat|enterprise|tender|rfp|srs|on-prem|gpu|llm|inference|"
                        r"kubernetes|high availability|multi-tenant)", text, re.I)),
         "the request mentions migration, enterprise scope, infrastructure or AI inference"),
    ]
    hits = [reason for hit, reason in deep_signals if hit]
    if len(hits) >= 2:
        return "deep", hits
    if hits or len(plan.get("requirements") or []) > 5 or plan.get("decisions"):
        return "standard", hits or ["more than a single well-defined change"]
    return "quick", ["a small, well-defined change"]


def stage_models(depth):
    """Which tier each planning stage should use. Tiers, not model versions: the router maps
    them to whatever models are configured and available."""
    if depth == "deep":
        return {"requirements": "opus", "infrastructure": "opus", "architecture": "opus",
                "breakdown": "sonnet", "implementation": "auto", "documentation": "auto"}
    if depth == "standard":
        return {"requirements": "sonnet", "infrastructure": "sonnet", "architecture": "opus",
                "breakdown": "sonnet", "implementation": "auto", "documentation": "auto"}
    return {"requirements": "sonnet", "infrastructure": "sonnet", "architecture": "sonnet",
            "breakdown": "sonnet", "implementation": "auto", "documentation": "auto"}


# --------------------------------------------------------------------------- capacity

def capacity(inputs):
    """Arithmetic on the workload inputs the plan records. Every output says which inputs
    it used and how settled they are. Nothing here is a statement that a configuration can
    carry a load: that needs a load test."""
    def get(name):
        item = (inputs or {}).get(name)
        if isinstance(item, dict):
            value, status = item.get("value"), item.get("status") or "ASSUMED"
        else:
            value, status = item, "ASSUMED" if item is not None else "UNKNOWN"
        try:
            return float(value), status
        except (TypeError, ValueError):
            return None, "UNKNOWN"

    def weakest(*statuses):
        order = {"VERIFIED": 0, "USER_CONFIRMED": 1, "ASSUMED": 2, "UNKNOWN": 3, "CONFLICTING": 4}
        return max(statuses, key=lambda status: order.get(status, 3))

    out = []
    concurrent, s1 = get("concurrent_users")
    per_minute, s2 = get("requests_per_user_per_minute")
    duration, s3 = get("avg_request_ms")
    peak, s4 = get("peak_factor")
    if concurrent is not None and per_minute is not None:
        rps = concurrent * per_minute / 60.0
        status = weakest(s1, s2)
        out.append({"name": "requests_per_second", "value": round(rps, 2), "status": status,
                    "method": "concurrent_users x requests_per_user_per_minute / 60"})
        if peak is not None:
            out.append({"name": "peak_requests_per_second", "value": round(rps * peak, 2),
                        "status": weakest(status, s4), "method": "requests_per_second x peak_factor"})
        if duration is not None:
            busy = rps * (peak or 1.0) * duration / 1000.0
            out.append({"name": "requests_in_flight", "value": round(busy, 2),
                        "status": weakest(status, s3, s4 if peak is not None else s3),
                        "method": "Little's law: (peak) requests per second x average duration"})
    files_per_day, f1 = get("uploads_per_day")
    file_mb, f2 = get("avg_upload_mb")
    retention, f3 = get("retention_days")
    stored_externally, f4 = get("documents_stored_externally")
    if files_per_day is not None and file_mb is not None:
        days = retention if retention is not None else 365.0
        total_gb = files_per_day * file_mb * days / 1024.0
        if stored_externally:
            out.append({"name": "application_document_storage_gb", "value": 0.0,
                        "status": f4, "method": "documents are kept in an external system "
                        "(documents_stored_externally); only metadata is stored locally"})
        else:
            out.append({"name": "document_storage_gb", "value": round(total_gb, 1),
                        "status": weakest(f1, f2, f3 if retention is not None else "ASSUMED"),
                        "method": "uploads_per_day x avg_upload_mb x %s days / 1024"
                                  % ("retention_days" if retention is not None else "365 (assumed)")})
    rows_per_day, d1 = get("db_rows_per_day")
    row_kb, d2 = get("avg_row_kb")
    if rows_per_day is not None and row_kb is not None:
        out.append({"name": "database_growth_gb_per_year",
                    "value": round(rows_per_day * row_kb * 365 / 1024 / 1024, 2),
                    "status": weakest(d1, d2), "method": "db_rows_per_day x avg_row_kb x 365"})
    for item in out:
        item["confidence"] = {"VERIFIED": "measured inputs", "USER_CONFIRMED": "owner-stated inputs",
                              "ASSUMED": "rests on assumptions"}.get(item["status"],
                                                                     "inputs unknown")
    return out


# --------------------------------------------------------------------------- tasks

def task_graph(plan):
    """Dependency facts: {"order": [ids], "cycles": [[ids]], "missing": [(task, dep)]}."""
    tasks = by_id(plan.get("tasks"))
    missing = [(task_id, dep) for task_id, task in tasks.items()
               for dep in task.get("depends_on") or [] if dep not in tasks]
    state, order, cycles, stack = {}, [], [], []

    def visit(node):
        state[node] = 1
        stack.append(node)
        for dep in tasks[node].get("depends_on") or []:
            if dep not in tasks:
                continue
            if state.get(dep) == 1:
                cycles.append(stack[stack.index(dep):] + [dep])
            elif state.get(dep) is None:
                visit(dep)
        stack.pop()
        state[node] = 2
        order.append(node)

    for node in tasks:
        if state.get(node) is None:
            visit(node)
    return {"order": order, "cycles": cycles, "missing": missing}


def readiness(plan, done=(), failed=()):
    """{task id: (state, reason)}: ready | waiting | held | blocked | done.
    held: a decision the task depends on is not approved. blocked: a prerequisite failed."""
    decisions = by_id(plan.get("decisions"))
    tasks = by_id(plan.get("tasks"))
    graph = task_graph(plan)
    result = {}
    for task_id in graph["order"]:
        task = tasks[task_id]
        if task_id in done:
            result[task_id] = ("done", "")
            continue
        unapproved = [ref for ref in task.get("decisions") or []
                      if (decisions.get(ref) or {}).get("status") != "APPROVED"]
        if unapproved:
            result[task_id] = ("held", "waits for the approval of %s" % ", ".join(unapproved))
            continue
        deps = task.get("depends_on") or []
        bad = [dep for dep in deps if dep in failed or result.get(dep, ("",))[0] == "blocked"]
        if bad:
            result[task_id] = ("blocked", "prerequisite %s failed" % ", ".join(bad))
        elif any(result.get(dep, ("",))[0] == "held" for dep in deps):
            result[task_id] = ("held", "a prerequisite is held")
        elif all(dep in done for dep in deps):
            result[task_id] = ("ready", "")
        else:
            result[task_id] = ("waiting", "waits for %s" % ", ".join(dep for dep in deps
                                                                    if dep not in done))
    return result


def approved_components(plan):
    """Technologies the approved decisions and the existing system allow."""
    allowed = set(catalog.component_id(item) for item in plan.get("existing_components") or [])
    for decision in plan.get("decisions") or []:
        if decision.get("status") != "APPROVED":
            continue
        option = by_id(decision.get("options")).get(decision.get("selected")) or {}
        allowed.update(catalog.component_id(item) for item in option.get("components") or [])
    return allowed


def rejected_components(plan):
    """Technologies only rejected options proposed (and no approved option includes)."""
    rejected = set()
    for decision in plan.get("decisions") or []:
        if decision.get("status") not in ("APPROVED", "SUPERSEDED"):
            continue
        for option in decision.get("options") or []:
            if option.get("id") != decision.get("selected"):
                rejected.update(catalog.component_id(item) for item in option.get("components") or [])
    return rejected - approved_components(plan)


# --------------------------------------------------------------------------- validation

def _finding(level, code, where, message):
    return {"level": level, "code": code, "where": where, "message": message}


def validate(plan):
    """Every rule the plan breaks. errors stop approval and import; warnings are shown."""
    out = []
    ids = {}
    for section in ("facts", "requirements", "decisions", "milestones", "tasks"):
        for item in plan.get(section) or []:
            if not isinstance(item, dict) or not item.get("id"):
                out.append(_finding("error", "missing-id", section, "an entry has no id"))
                continue
            if item["id"] in ids:
                out.append(_finding("error", "duplicate-id", item["id"],
                                    "the id is used in %s and %s" % (ids[item["id"]], section)))
            ids[item["id"]] = section
    for section in ("facts", "requirements"):
        for item in plan.get(section) or []:
            status = item.get("status")
            if status not in EVIDENCE:
                out.append(_finding("error", "evidence-class", item.get("id"),
                                    "status must be one of %s" % ", ".join(EVIDENCE)))
            elif status == "VERIFIED" and not item.get("evidence"):
                out.append(_finding("error", "verified-without-evidence", item.get("id"),
                                    "VERIFIED needs evidence (a file, document or measurement)"))
            elif status == "CONFLICTING":
                out.append(_finding("warning", "conflicting", item.get("id"),
                                    "conflicting evidence has to be resolved: %s"
                                    % (item.get("statement") or item.get("text") or "")))
    for item in plan.get("requirements") or []:
        if item.get("kind") not in REQUIREMENT_KINDS:
            out.append(_finding("error", "requirement-kind", item.get("id"),
                                "kind must be one of %s" % ", ".join(REQUIREMENT_KINDS)))
    infrastructure = plan.get("infrastructure") or {}
    target = infrastructure.get("target")
    scenario_ids = [item["id"] for item in scenarios(plan)]
    if target and target not in scenario_ids:
        out.append(_finding("error", "target", "infrastructure",
                            "target %s is not one of the scenarios" % target))
    for scenario in scenarios(plan):
        for field, entry in (scenario.get("env") or {}).items():
            if field not in catalog.ENV_FIELDS:
                out.append(_finding("warning", "env-field", scenario["id"],
                                    "unknown infrastructure field %s" % field))
            if isinstance(entry, dict) and entry.get("status") not in EVIDENCE:
                out.append(_finding("error", "evidence-class", "%s.%s" % (scenario["id"], field),
                                    "status must be one of %s" % ", ".join(EVIDENCE)))
        if scenario.get("source") == "developer-machine" and scenario["id"] == target:
            out.append(_finding("error", "developer-machine-target", scenario["id"],
                                "the developer's machine is not the deployment target"))
    requirement_ids = {item.get("id") for item in plan.get("requirements") or []}
    decisions = by_id(plan.get("decisions"))
    facts = by_id(plan.get("facts"))
    for decision in plan.get("decisions") or []:
        where = decision.get("id")
        if decision.get("status") not in DECISION_STATUSES:
            out.append(_finding("error", "decision-status", where,
                                "status must be one of %s" % ", ".join(DECISION_STATUSES)))
        options = by_id(decision.get("options"))
        if len(options) < 2 and decision.get("status") not in ("REJECTED", "SUPERSEDED"):
            out.append(_finding("warning", "single-option", where,
                                "only one option was considered; record the alternatives"))
        for ref in decision.get("requirements") or []:
            if ref not in requirement_ids:
                out.append(_finding("error", "unknown-requirement", where, "links %s" % ref))
        for ref in decision.get("facts") or []:
            if ref not in facts:
                out.append(_finding("error", "unknown-fact", where, "links %s" % ref))
        selected = decision.get("selected")
        if selected and selected not in options:
            out.append(_finding("error", "unknown-option", where, "selected %s is not an option"
                                % selected))
        if selected in options:
            option = options[selected]
            if not decision.get("rationale"):
                out.append(_finding("error", "no-rationale", where,
                                    "a selected option needs a rationale"))
            for component in catalog.unjustified(option):
                out.append(_finding("error", "unjustified-component", where,
                                    "%s adds %s without naming the requirement that needs it "
                                    "(components: {\"id\": \"%s\", \"why\": [\"R..\"]})"
                                    % (selected, component, component)))
            for verdict_scenario, result in evaluate_option(plan, option).items():
                if target and verdict_scenario != target:
                    continue
                if result["verdict"] == "incompatible":
                    out.append(_finding("error", "incompatible", where,
                                        "%s is incompatible with %s: %s" % (
                                            selected, verdict_scenario,
                                            "; ".join(item["reason"] for item in result["findings"]
                                                      if item["verdict"] == "incompatible"))))
            if not scenarios(plan) and option.get("components"):
                out.append(_finding("warning", "no-infrastructure", where,
                                    "no infrastructure is recorded, so compatibility was not checked"))
            simpler = _simpler_equal(plan, decision, option)
            if simpler:
                out.append(_finding("warning", "simpler-option", where,
                                    "%s is simpler and the matrix does not show %s to be better: "
                                    "prefer the simpler option or record why not"
                                    % (simpler, selected)))
            existing = {catalog.component_id(item): (catalog.COMPONENTS.get(
                catalog.component_id(item)) or {}).get("category")
                for item in plan.get("existing_components") or []}
            for item in option.get("components") or []:
                cid = catalog.component_id(item)
                category = (catalog.COMPONENTS.get(cid) or {}).get("category")
                replaced = [old for old, old_category in existing.items()
                            if old_category and old_category == category and old != cid
                            and category in ("framework", "database", "runtime")]
                if replaced and not decision.get("material"):
                    out.append(_finding("error", "replaces-existing", where,
                                        "%s would replace the existing %s; that is a material "
                                        "decision (set material: true) and needs evidence"
                                        % (cid, ", ".join(replaced))))
        if decision.get("category") in MATERIAL_CATEGORIES and decision.get("material") is False:
            out.append(_finding("error", "materiality", where,
                                "a %s decision is always material" % decision.get("category")))
        if decision.get("supersedes") and decision["supersedes"] not in decisions:
            out.append(_finding("error", "supersedes", where, "supersedes an unknown decision"))
    milestone_ids = {item.get("id") for item in plan.get("milestones") or []}
    graph = task_graph(plan)
    for task_id, dep in graph["missing"]:
        out.append(_finding("error", "unknown-dependency", task_id, "depends on unknown %s" % dep))
    for cycle in graph["cycles"]:
        out.append(_finding("error", "cycle", cycle[0], "circular dependency: %s"
                            % " -> ".join(cycle)))
    allowed = approved_components(plan)
    proposed = set()
    for decision in plan.get("decisions") or []:
        option = by_id(decision.get("options")).get(decision.get("selected")) or {}
        proposed.update(catalog.component_id(item) for item in option.get("components") or [])
    for task in plan.get("tasks") or []:
        where = task.get("id")
        if not task.get("title"):
            out.append(_finding("error", "task-title", where, "a task needs a title"))
        elif VAGUE.match(task["title"]) and not task.get("acceptance"):
            out.append(_finding("error", "vague-task", where,
                                "\"%s\" is not an executable task: say what, where and how it "
                                "is verified" % task["title"]))
        kind = task.get("kind") or "implementation"
        if kind not in TASK_KINDS:
            out.append(_finding("error", "task-kind", where, "kind must be one of %s"
                                % ", ".join(TASK_KINDS)))
        if kind in ("implementation", "infrastructure", "migration") and not task.get("acceptance"):
            out.append(_finding("error", "no-acceptance", where,
                                "an %s task needs acceptance criteria" % kind))
        for ref in task.get("requirements") or []:
            if ref not in requirement_ids:
                out.append(_finding("error", "unknown-requirement", where, "links %s" % ref))
        for ref in task.get("decisions") or []:
            if ref not in decisions:
                out.append(_finding("error", "unknown-decision", where, "links %s" % ref))
        if task.get("milestone") and task["milestone"] not in milestone_ids:
            out.append(_finding("error", "unknown-milestone", where, "milestone %s"
                                % task["milestone"]))
        for component in task.get("components") or []:
            cid = catalog.component_id(component)
            if cid not in allowed and cid not in proposed:
                out.append(_finding("error", "unplanned-technology", where,
                                    "introduces %s, which no decision selects" % cid))
    covered = {ref for task in plan.get("tasks") or [] for ref in task.get("requirements") or []}
    for requirement in plan.get("requirements") or []:
        if requirement.get("priority", "must") == "must" and requirement.get("id") not in covered \
                and plan.get("tasks"):
            out.append(_finding("warning", "uncovered-requirement", requirement.get("id"),
                                "no task implements this requirement"))
    for fact in plan.get("facts") or []:
        if fact.get("status") == "UNKNOWN":
            out.append(_finding("info", "unknown", fact.get("id"), "still unknown: %s"
                                % fact.get("statement") or fact.get("topic")))
    return out


def _simpler_equal(plan, decision, selected_option):
    """A compatible option that is simpler than the selected one and not shown worse."""
    table = matrix(decision)
    mine = table["options"].get(selected_option.get("id"))
    if not mine:
        return None
    target = (plan.get("infrastructure") or {}).get("target")
    for option in decision.get("options") or []:
        if option.get("id") == selected_option.get("id"):
            continue
        other = table["options"].get(option.get("id"))
        if not other or catalog.complexity(option) >= catalog.complexity(selected_option):
            continue
        if target:
            verdict = evaluate_option(plan, option).get(target, {}).get("verdict")
            if verdict != "compatible":
                continue
        if other["high"] >= mine["low"]:
            return option.get("id")
    return None


def suggested_status(plan, decision):
    """What the decision's status should be, given the evidence (APPROVED excepted: only an
    approval sets that). Returns (status, reasons)."""
    if decision.get("status") in ("APPROVED", "SUPERSEDED", "REJECTED"):
        return decision["status"], []
    reasons = []
    facts = by_id(plan.get("facts"))
    for ref in decision.get("facts") or []:
        if (facts.get(ref) or {}).get("status") in ("UNKNOWN", "CONFLICTING"):
            reasons.append("fact %s is %s" % (ref, facts[ref]["status"].lower()))
    option = by_id(decision.get("options")).get(decision.get("selected"))
    if not option:
        reasons.append("no option is selected")
    else:
        target = (plan.get("infrastructure") or {}).get("target")
        results = evaluate_option(plan, option)
        if not target:
            reasons.append("the deployment target is not known (scenarios: %s)"
                           % (", ".join(results) or "none"))
        elif results.get(target, {}).get("verdict") in ("needs_information", "not_assessed"):
            reasons.extend("%s: %s" % (item["component"], item["reason"])
                           for item in results[target]["findings"]
                           if item["verdict"] in ("needs_information", "not_assessed"))
    return ("NEEDS_INFORMATION" if reasons else "PROPOSED"), reasons


def decision_assumptions(plan, decision):
    """What a decision rests on that nobody has confirmed: assumed infrastructure values of
    the target, assumed facts it links, and the assumptions it lists itself."""
    found = []
    option = by_id(decision.get("options")).get(decision.get("selected"))
    target = target_scenario(plan)
    if option and target:
        found += ["infrastructure: %s" % item for item in
                  catalog.assumptions(option, target.get("env") or {})]
    facts = by_id(plan.get("facts"))
    found += ["fact %s: %s (assumed)" % (ref, facts[ref].get("statement"))
              for ref in decision.get("facts") or []
              if (facts.get(ref) or {}).get("status") == "ASSUMED"]
    requirements = by_id(plan.get("requirements"))
    found += ["requirement %s: %s (assumed)" % (ref, requirements[ref].get("text"))
              for ref in decision.get("requirements") or []
              if (requirements.get(ref) or {}).get("status") == "ASSUMED"]
    found += ["stated: %s" % item for item in decision.get("assumptions") or []]
    return found


def stage(plan, approved_revision=None):
    """Where the plan stands on the way from analysis to execution."""
    decisions = [item for item in plan.get("decisions") or []
                 if item.get("status") not in ("SUPERSEDED", "REJECTED")]
    material = [item for item in decisions if item.get("material")
                or item.get("category") in MATERIAL_CATEGORIES]
    errors = [item for item in validate(plan) if item["level"] == "error"]
    if not decisions:
        return "analysis"
    if any(item.get("status") != "APPROVED" for item in material):
        return "architecture proposed"
    if not plan.get("tasks") or errors:
        return "architecture approved"
    if approved_revision is None:
        return "plan ready"
    return "approved for execution"


# --------------------------------------------------------------------------- execution

def compose_task_prompt(plan, task):
    """The prompt a plan task enters the queue with. The approved architecture travels
    with it, so the session that implements it knows the boundaries."""
    decisions = by_id(plan.get("decisions"))
    lines = ["[%s %s] %s" % (plan.get("id"), task.get("id"), task.get("title"))]
    if task.get("description"):
        lines += ["", task["description"]]
    for label, key in (("Business objective", "business_objective"),
                       ("Technical objective", "technical_objective")):
        if task.get(key):
            lines.append("%s: %s" % (label, task[key]))
    if task.get("files"):
        lines.append("Files/modules: %s" % ", ".join(task["files"]))
    requirements = by_id(plan.get("requirements"))
    linked = [requirements[ref] for ref in task.get("requirements") or [] if ref in requirements]
    if linked:
        lines += ["", "Requirements:"] + ["- %s: %s" % (item["id"], item.get("text"))
                                          for item in linked]
    constraints = []
    for ref in task.get("decisions") or []:
        decision = decisions.get(ref) or {}
        option = by_id(decision.get("options")).get(decision.get("selected")) or {}
        constraints.append("- %s (approved): %s. Use %s." % (
            ref, decision.get("title"), option.get("name") or decision.get("selected")))
    allowed = sorted(approved_components(plan))
    if constraints or allowed:
        lines += ["", "Approved architecture (do not change it; if the task cannot be done "
                  "within it, stop and report MQ_RESULT: FAILED - needs a plan revision):"]
        lines += constraints
        if allowed:
            lines.append("- Approved technologies: %s. Do not add databases, caches, brokers, "
                         "containers or other infrastructure that are not listed."
                         % ", ".join(allowed))
    if task.get("reference"):
        lines += ["", "Reference (from the replica inventory; the evidence class is in "
                      "brackets, and only OBSERVED, DOCUMENTED or USER_CONFIRMED behaviour is "
                      "settled):"] + ["- %s" % item for item in task["reference"]]
    if task.get("tests"):
        lines += ["", "Tests:"] + ["- %s" % item for item in task["tests"]]
    if task.get("done"):
        lines += ["", "Definition of done:"] + ["- %s" % item for item in task["done"]]
    acceptance = "\n".join("- %s" % item for item in task.get("acceptance") or [])
    return "\n".join(lines), acceptance


CONTEXT_HEADERS = ("Approved architecture (", "Reference (from the replica inventory")


def routing_text(prompt):
    """The part of a plan task's prompt that describes the work, for the model router: the
    context blocks every task carries (the approved architecture, the replica reference)
    are left out, so their wording ("do not add databases", "design tokens", "synthetic
    data") does not decide which model the task gets."""
    out, skipping = [], False
    for line in (prompt or "").splitlines():
        if any(line.startswith(header) for header in CONTEXT_HEADERS):
            skipping = True
            continue
        if skipping and not line.strip():
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).strip()


def task_digest(task):
    return content_hash(task)[:16]


# Signs in a commit that a technology was introduced: (file pattern, text pattern, component).
INTRODUCTION_SIGNS = [
    (r"(^|/)Dockerfile$|(^|/)docker-compose[^/]*\.ya?ml$|(^|/)compose\.ya?ml$", None, "docker"),
    (r"\.ya?ml$", r"^\+\s*kind:\s*(Deployment|StatefulSet|DaemonSet|Ingress)\b", "kubernetes"),
    (r"(^|/)Chart\.yaml$", None, "kubernetes"),
    (r"package\.json$", r'^\+.*"(redis|ioredis)"\s*:', "redis"),
    (r"package\.json$", r'^\+.*"(amqplib|amqp-connection-manager)"\s*:', "rabbitmq"),
    (r"package\.json$", r'^\+.*"(kafkajs|node-rdkafka)"\s*:', "kafka"),
    (r"package\.json$", r'^\+.*"(pg|postgres)"\s*:', "postgres"),
    (r"package\.json$", r'^\+.*"(mysql2?|mariadb)"\s*:', "mysql"),
    (r"package\.json$", r'^\+.*"(mongodb|mongoose)"\s*:', "mongodb"),
    (r"package\.json$", r'^\+.*"(sqlite3|better-sqlite3)"\s*:', "sqlite"),
    (r"package\.json$", r'^\+.*"(@elastic/elasticsearch|@opensearch-project/opensearch)"\s*:', "elasticsearch"),
    (r"package\.json$", r'^\+.*"(openai|@anthropic-ai/sdk)"\s*:', "cloud-llm-api"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(redis)\b", "redis"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(pika|kombu)\b", "rabbitmq"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(kafka-python|confluent-kafka)\b", "kafka"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(psycopg2?|psycopg2-binary|asyncpg)\b", "postgres"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(pymongo|motor)\b", "mongodb"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(openai|anthropic)\b", "cloud-llm-api"),
    (r"(requirements[^/]*\.txt|pyproject\.toml|Pipfile)$", r"^\+\s*['\"]?(elasticsearch|opensearch-py)\b", "elasticsearch"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(StackExchange\.Redis)", "redis"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(RabbitMQ\.Client|MassTransit\.RabbitMQ)", "rabbitmq"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(Confluent\.Kafka)", "kafka"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(Npgsql)", "postgres"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(MongoDB\.Driver)", "mongodb"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(Microsoft\.Data\.Sqlite|System\.Data\.SQLite)", "sqlite"),
    (r"\.(cs|vb|fs)proj$|packages\.config$", r"^\+.*(Pomelo\.EntityFrameworkCore\.MySql|MySql\.Data)", "mysql"),
    (r"\.(cs|vb|fs)proj$", r"^\+.*<TargetFramework>net\d+\.\d+", "dotnet"),
    (r"\.(cs|vb|fs)proj$", r"^\+.*<TargetFramework(Version)?>(net4|v4)", "dotnet-framework"),
]


def introduced_components(diff_by_file):
    """Catalogued technologies a change introduces. `diff_by_file` maps a changed path to
    the unified diff of that file (added files: their whole content as + lines)."""
    found = {}
    for path, diff in diff_by_file.items():
        normalized = path.replace("\\", "/")
        for file_pattern, text_pattern, component in INTRODUCTION_SIGNS:
            if not re.search(file_pattern, normalized, re.I):
                continue
            if text_pattern is None:
                if diff.lstrip().startswith("+") or "\n+" in diff or diff == "":
                    found.setdefault(component, []).append(normalized)
            elif re.search(text_pattern, diff, re.I | re.M):
                found.setdefault(component, []).append(normalized)
    return found


def compliance(plan, task, diff_by_file):
    """Findings for a finished task's change, compared with the approved architecture.
    violation: a technology the plan rejected, or one no approved decision allows and that
    is incompatible with the target; unapproved: a technology no approved decision covers."""
    findings = []
    allowed = approved_components(plan)
    rejected = rejected_components(plan)
    target = target_scenario(plan)
    for component, paths in sorted(introduced_components(diff_by_file).items()):
        if component in allowed:
            continue
        reason = "introduces %s (%s), which the approved architecture does not include" % (
            component, ", ".join(sorted(set(paths))[:3]))
        level = "unapproved"
        if component in rejected:
            level, reason = "violation", reason + "; an approved decision rejected it"
        elif target:
            verdicts = [item["verdict"] for item in catalog.check_component(
                component, target.get("env") or {}, plan.get("requirements") or [])]
            if "incompatible" in verdicts:
                level, reason = "violation", reason + "; it is incompatible with the target %s" % target["id"]
        findings.append({"level": level, "component": component, "files": sorted(set(paths)),
                         "message": reason})
    return findings


def freeze(plan):
    """A deep copy suitable for storing as an immutable revision."""
    return copy.deepcopy(plan)
