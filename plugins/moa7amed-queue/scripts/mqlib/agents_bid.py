"""Built-in agent decompositions for BidStudio stages.

Drafting (stage T7): the technical proposal sections that have no content yet are split
between up to agent_max_parallel read-only workers; each worker gets only the tender
facts, the solution summary and the requirements that concern its sections, and returns
section texts. Intake (stage T1): one read-only worker per readable tender document
returns the requirements it finds there; the coordinator numbers them, checks every
quote against the document and drops duplicates. Pricing, compliance decisions,
approvals and document generation stay with the coordinator and the engine.
"""
from __future__ import annotations

import json
import math
import os

from . import agents_apply, bid_docs, bid_store

SECTION_CATEGORIES = {
    "understanding of requirements": None,      # all requirements, briefly
    "proposed solution": ("functional", "non_functional", "infrastructure", "integration", "security"),
    "solution architecture": ("non_functional", "infrastructure", "integration", "security"),
    "functional scope": ("functional", "scope"),
    "technical specifications": ("non_functional", "infrastructure", "performance"),
    "infrastructure and hardware": ("infrastructure", "non_functional"),
    "security and integration": ("security", "integration"),
    "implementation methodology": ("delivery",),
    "project governance": ("delivery", "contractual"),
    "implementation schedule": ("delivery",),
    "testing and acceptance": ("delivery", "performance"),
    "training and knowledge transfer": ("training",),
    "warranty and support": ("warranty", "sla", "support"),
}
PROFILE_SECTIONS = ("company profile", "relevant experience", "project team")
AUTO_SECTIONS = ("compliance matrix", "risks and mitigations", "assumptions and dependencies")


def _plugin_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))




def drafting_plan(q, bid_id, config):
    project = bid_store.get(q, bid_id)
    document = project["document"]
    technical = document.get("technical") or {}
    wanted = list(technical.get("required_sections") or []) or list(bid_docs.DEFAULT_TECHNICAL)
    existing = {str(s.get("title") or "").lower(): s for s in technical.get("sections") or []}
    todo = [title for title in wanted if title.lower() not in AUTO_SECTIONS
            and not (existing.get(title.lower()) or {}).get("content")]
    if len(todo) < 2:
        return None
    limit = max(1, int(config.get("agent_max_parallel", 3)))
    groups = min(limit, max(1, len(todo) // 2))
    size = int(math.ceil(len(todo) / float(groups)))
    requirements = [r for r in document.get("requirements") or [] if not r.get("superseded_by")]
    profile = bid_store.company_profile(project["company"]) if project["company"] else None
    tender = document.get("tender") or {}
    solution = document.get("solution") or {}
    items = []
    for index in range(groups):
        chunk = todo[index * size:(index + 1) * size]
        if not chunk:
            continue
        categories, all_reqs, wants_profile = set(), False, False
        for title in chunk:
            mapped = SECTION_CATEGORIES.get(title.lower(), ())
            if mapped is None:
                all_reqs = True
            else:
                categories.update(mapped)
            wants_profile = wants_profile or title.lower() in PROFILE_SECTIONS
        relevant = requirements if all_reqs else [r for r in requirements if r.get("category") in categories]
        context = ["Tender: %s" % json.dumps({k: v for k, v in tender.items()
                                              if not isinstance(v, (dict, list))}, ensure_ascii=False),
                   "Response language: %s" % (document.get("language") or "en"),
                   "Solution summary: %s" % (solution.get("summary") or "not recorded yet")[:1500]]
        if relevant:
            context.append("Requirements that concern these sections:")
            context += ["- %s [%s%s] %s" % (r["id"], r.get("category"), ", mandatory" if r.get("mandatory")
                                            else "", (r.get("text") or "")[:300]) for r in relevant[:60]]
        if wants_profile:
            items_ = [i for i in (profile or {}).get("items") or [] if i.get("approved")]
            context.append("Approved company profile items (the only company claims you may make): %s"
                           % ("; ".join("%s: %s" % (i["id"], i.get("text")) for i in items_) or "none"))
        if (document.get("estimate") or {}).get("duration_days"):
            context.append("Estimated schedule: %s working days; effort scenarios %s" % (
                document["estimate"]["duration_days"], document["estimate"].get("scenarios")))
        items.append({
            "key": "draft-%d" % (index + 1), "role": "proposal drafting", "mode": "read",
            "applier": "bid_sections", "model": "sonnet",
            "objective": "Draft these sections of the technical proposal of bid %s: %s. Use only the "
                         "inputs below; write in the response language; no "
                         "prices, rates or currency amounts; company claims only from the approved "
                         "profile items; where information is missing, say what is to be provided."
                         % (bid_id, "; ".join(chunk)),
            "inputs": {"bid": bid_id, "sections": chunk, "context": "\n".join(context),
                       "fingerprints": {title: agents_apply.section_fingerprint(
                           (existing.get(title.lower()) or {}).get("content")) for title in chunk}},
            "weight": len(chunk)})
    return {"subtasks": items, "source": "built-in: BidStudio section drafting"}


def intake_plan(q, bid_id, config):
    docs = [d for d in bid_store.documents(q, bid_id)
            if d["status"] in ("read", "partly read") and d.get("text_path") and d["type"] not in
            ("quotation", "capability", "reference", "technical_proposal", "commercial_proposal")
            and not d.get("superseded_by")]
    if len(docs) < 2:
        return None
    items = []
    for doc in docs[:max(1, int(config.get("agent_max_parallel", 3))) * 3]:
        size = os.path.getsize(doc["text_path"]) if os.path.exists(doc["text_path"]) else 1
        items.append({
            "key": "intake-%s" % doc["id"].lower(), "role": "requirements analysis", "mode": "read",
            "applier": "bid_requirements", "model": "sonnet",
            "objective": "Read document %s (%s, %s) of bid %s completely and list every requirement it "
                         "states, in the document's own words, with its section/page/table. Do not "
                         "number them; the coordinator does. Read-only." % (
                             doc["id"], doc["name"], doc["type"], bid_id),
            "inputs": {"bid": bid_id, "doc": doc["id"], "read_files": [doc["text_path"]],
                       "context": "The extracted text of %s is the file listed below (page markers "
                                  "'=== page N ===' give the pages)." % doc["id"]},
            "weight": max(1.0, size / 4000.0)})
    return {"subtasks": items, "source": "built-in: BidStudio document intake"}
