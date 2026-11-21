"""BidStudio: the rules of tender analysis and proposal preparation (no I/O here).

A bid project holds a tender package (documents with provenance), the requirements read
from it (each with its source and verbatim wording), a compliance matrix, a clarification
register, a bid/no-bid assessment, an effort estimate and a pricing model. This module
checks that what the proposal says can be traced: a requirement quotes its document, a
COMPLIANT answer shows how it is met and with what evidence, a company claim cites an
approved profile item, and numbers come from the pricing engine.
"""
from __future__ import annotations

import re
from decimal import Decimal

from . import router_ar

CLASSES = ("EXPLICIT_REQUIREMENT", "VERIFIED_SUPPORTING_EVIDENCE", "PROPOSED_SOLUTION",
           "ASSUMPTION", "UNKNOWN", "CONFLICTING")
COMPLIANCE = ("COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "PENDING_VERIFICATION",
              "NOT_APPLICABLE")
CATEGORIES = ("submission", "eligibility", "certification", "registration", "scope", "functional",
              "non_functional", "security", "infrastructure", "integration", "delivery", "training",
              "warranty", "sla", "support", "payment", "performance", "penalty", "contractual",
              "evaluation", "local_content", "forms", "commercial", "technical", "other")
MODES = ("analyze", "technical", "commercial", "complete", "review")
DECISIONS = ("BID", "BID_WITH_CONDITIONS", "NO_BID", "INSUFFICIENT_INFORMATION")
SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW")
DOC_TYPES = ("rfp", "rfq", "tender", "scope", "addendum", "clarification", "annex", "form",
             "technical_proposal", "commercial_proposal", "quotation", "capability", "reference",
             "other")
GATES = ("architecture", "compliance_commitments", "licensing", "pricing", "contract_exceptions",
         "release")
FACTORS = ("technical_capability", "delivery_feasibility", "partner_capabilities",
           "certifications", "licensing", "timeline", "financial_exposure", "contractual_risk",
           "resources", "support_obligations", "competitiveness")
REQ_ID = re.compile(r"^REQ-\d{3,4}[a-z]?$")
Q_ID = re.compile(r"^Q-\d{2,4}$")

# --------------------------------------------------------------------------- intent

_TENDER_EN = r"(tender|rfp|rfq|rfi|request for (proposals?|quotations?|information)|bid|bidding|" \
             r"procurement|solicitation|scope of work|sow|terms of reference|tor)"
_ANALYZE = re.compile(r"\b(analy[sz]e|summari[sz]e|read|assess|evaluate|go through|understand|"
                      r"break down|extract)\b[^.?!\n]{0,40}\b%s\b" % _TENDER_EN, re.I)
_TECHNICAL = re.compile(r"\btechnical (proposal|offer|response|envelope|bid)\b", re.I)
_COMMERCIAL = re.compile(r"\b(commercial|financial|price|pricing) (proposal|offer|envelope|bid|"
                         r"model)\b|\bquotation\b|\bboq\b|\bbill of quantities\b", re.I)
_COMPLETE = re.compile(r"\b(complete|full|whole|entire)\b[^.?!\n]{0,30}\b(bid|proposal|tender|"
                       r"response|package)\b|\btechnical and commercial\b|\bcommercial and "
                       r"technical\b", re.I)
_REVIEW = re.compile(r"\breview\b[^.?!\n]{0,40}\b(our |the |this )?(proposal|quotation|offer|"
                     r"bid|response|submission)\b|\bcheck\b[^.?!\n]{0,30}\b(proposal|quotation)"
                     r"\b[^.?!\n]{0,30}\bagainst\b", re.I)
_UPDATE = re.compile(r"\b(update|revise|apply)\b[^.?!\n]{0,40}\b(clarification|addendum|addenda|"
                     r"corrigendum)\b", re.I)
_PREPARE = re.compile(r"\b(prepare|write|draft|generate|create|produce|build)\b[^.?!\n]{0,50}"
                      r"\b(proposal|offer|bid|response|quotation|tender)\b", re.I)
_TENDER_WORD = re.compile(r"\b%s\b" % _TENDER_EN, re.I)
_QUESTION = re.compile(r"^\s*(what|why|how|who|when|where|is|are|does|do|explain|tell me)\b", re.I)
# Arabic (normalised)
_AR_TENDER = u"مناقصه|المناقصه|عطاء|العطاء|كراسه الشروط|كراسة|طلب عروض|طلب تسعير|منافسه|المنافسه|ممارسه"
_AR_TECHNICAL = re.compile(u"العرض الفني|عرض فني")
_AR_COMMERCIAL = re.compile(u"العرض المالي|عرض مالي|العرض التجاري|عرض سعر|التسعير|جدول الكميات")
_AR_ANALYZE = re.compile(u"(حلل|تحليل|لخص|اقرا|راجع كراسه)[^.?!؟\n]{0,40}(" + _AR_TENDER + u")")
_AR_PREPARE = re.compile(u"(جهز|حضر|اعد|اكتب|اعمل|جهزلي|حضرلي)[^.?!؟\n]{0,40}(عرض|العرض|العروض|عطاء)")
_AR_REVIEW = re.compile(u"راجع[^.?!؟\n]{0,40}(عرضنا|العرض|عرض السعر)")
_AR_UPDATE = re.compile(u"(حدث|عدل)[^.?!؟\n]{0,40}(الاستفسار|الاستيضاح|الملحق|ملحق)")
_AR_COMPLETE = re.compile(u"(الفني والمالي|المالي والفني|كامل|الكامل)")


def intent(text):
    """{"intent": "BID_<MODE>" or None, "mode", "reason"}. A question about tenders or
    an unrelated proposal stays a conversation."""
    raw = text or ""
    ar = router_ar.normalize(raw)
    tenderish = bool(_TENDER_WORD.search(raw) or re.search(_AR_TENDER, ar)
                     or _UPDATE.search(raw) or _AR_UPDATE.search(ar)
                     or _TECHNICAL.search(raw) or _COMMERCIAL.search(raw)
                     or _AR_TECHNICAL.search(ar) or _AR_COMMERCIAL.search(ar))
    question = bool(_QUESTION.match(raw)) and not _PREPARE.search(raw)
    mode = None
    if _UPDATE.search(raw) or _AR_UPDATE.search(ar):
        mode = "update"
    elif _REVIEW.search(raw) or _AR_REVIEW.search(ar):
        mode = "review"
    elif (_COMPLETE.search(raw) or _AR_COMPLETE.search(ar)) and (
            _PREPARE.search(raw) or _AR_PREPARE.search(ar)):
        mode = "complete"
    elif (_TECHNICAL.search(raw) and _COMMERCIAL.search(raw)) or \
            (_AR_TECHNICAL.search(ar) and _AR_COMMERCIAL.search(ar)):
        mode = "complete"
    elif _TECHNICAL.search(raw) or _AR_TECHNICAL.search(ar):
        mode = "technical"
    elif _COMMERCIAL.search(raw) or _AR_COMMERCIAL.search(ar):
        mode = "commercial"
    elif _ANALYZE.search(raw) or _AR_ANALYZE.search(ar):
        mode = "analyze"
    elif _PREPARE.search(raw) and _TENDER_WORD.search(raw):
        mode = "complete"
    if mode is None or not tenderish or (question and mode not in ("review",)):
        return {"intent": None, "mode": None,
                "reason": "a question or an unrelated request" if tenderish
                else "no tender work requested"}
    return {"intent": "BID_" + mode.upper(), "mode": mode,
            "reason": "tender work: %s" % mode}


# --------------------------------------------------------------------------- helpers

def normalise_text(text):
    text = router_ar.normalize(text or "").lower()
    text = re.sub(r"[‘’“”\"'`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_found(quote, document_text):
    """Is the requirement's wording in the source document (spacing, case, quotes and
    Arabic spelling variants ignored)? A long quote may be abbreviated with '...'."""
    haystack = normalise_text(document_text)
    parts = [normalise_text(part) for part in re.split(r"\.\.\.|…", quote or "")]
    parts = [part for part in parts if len(part) >= 4]
    return bool(parts) and all(part in haystack for part in parts)


def words(text):
    return set(re.findall(r"\w{3,}", normalise_text(text)))


def restates(response, requirement_text):
    """Does the response merely repeat the requirement's wording?"""
    answer, asked = words(response), words(requirement_text)
    if not answer:
        return True
    new = answer - asked
    return len(new) < 4 or len(new) / float(len(answer)) < 0.25


def by_id(items):
    return {item.get("id"): item for item in items or [] if isinstance(item, dict)}


def _finding(severity, code, where, message, verified=True):
    return {"severity": severity, "code": code, "where": where, "message": message,
            "verified": verified}


# --------------------------------------------------------------------------- validation

def validate(doc, documents, profile=None):
    """Findings about requirements, compliance, claims and clarifications.

    documents: {doc id: {"text": extracted text or None, "status": .., "superseded_by": ..}}
    profile:   the company profile ({"items": [...]}) or None."""
    out = []
    requirements = by_id(doc.get("requirements"))
    items = by_id((profile or {}).get("items"))
    for key, req in requirements.items():
        where = key
        if not key or not REQ_ID.match(str(key)):
            out.append(_finding("HIGH", "requirement-id", where, "requirement ids are REQ-001..."))
        if req.get("class", "EXPLICIT_REQUIREMENT") not in CLASSES:
            out.append(_finding("HIGH", "class", where, "class must be one of %s" % ", ".join(CLASSES)))
        if req.get("category") not in CATEGORIES:
            out.append(_finding("MEDIUM", "category", where, "category must be one of %s"
                                % ", ".join(CATEGORIES)))
        source = req.get("source") or {}
        if req.get("class", "EXPLICIT_REQUIREMENT") == "EXPLICIT_REQUIREMENT":
            document = documents.get(source.get("doc"))
            if not source.get("doc") or document is None:
                out.append(_finding("BLOCKER", "no-source", where, "an explicit requirement must "
                                    "name the document it comes from"))
            elif not (source.get("page") or source.get("section") or source.get("table")
                      or source.get("sheet")):
                out.append(_finding("HIGH", "no-location", where, "give the page, section, table "
                                    "or worksheet of the requirement"))
            if document is not None and document.get("text") and req.get("text") \
                    and not quote_found(req.get("quote") or req["text"], document["text"]):
                out.append(_finding("BLOCKER", "quote-not-found", where,
                                    "the requirement's wording is not in %s: quote the document "
                                    "exactly (use ... to skip words)" % source.get("doc")))
            pending_amendments = [doc_id for doc_id in (document or {}).get("amended_by") or []
                                  if doc_id not in (req.get("checked_against") or [])]
            if pending_amendments and not req.get("superseded_by"):
                out.append(_finding("MEDIUM", "amended-source", where, "%s is amended by %s: "
                                    "check this requirement against it (then record "
                                    "\"checked_against\": [...], or supersede it)"
                                    % (source.get("doc"), ", ".join(pending_amendments))))
            if document is not None and document.get("superseded_by") and not req.get("superseded_by"):
                out.append(_finding("HIGH", "superseded-source", where, "%s is superseded by %s: "
                                    "check the requirement against the newer document"
                                    % (source.get("doc"), document["superseded_by"])))
    matrix = by_id(doc.get("compliance"))
    for key, req in requirements.items():
        if req.get("superseded_by"):
            continue
        if req.get("mandatory") and key not in matrix and \
                req.get("class", "EXPLICIT_REQUIREMENT") == "EXPLICIT_REQUIREMENT":
            out.append(_finding("HIGH", "unanswered", key, "mandatory requirement without a "
                                "compliance entry"))
    for key, row in matrix.items():
        req = requirements.get(key)
        if req is None:
            out.append(_finding("HIGH", "unknown-requirement", key, "compliance entry for an "
                                "unknown requirement"))
            continue
        status = row.get("status")
        if status not in COMPLIANCE:
            out.append(_finding("HIGH", "status", key, "status must be one of %s" % ", ".join(COMPLIANCE)))
            continue
        if status == "COMPLIANT":
            if not row.get("response"):
                out.append(_finding("HIGH", "no-response", key, "COMPLIANT without a response"))
            elif restates(row["response"], req.get("text")):
                out.append(_finding("HIGH", "restates-requirement", key, "the response repeats "
                                    "the requirement instead of saying how it is met"))
            if not row.get("evidence"):
                out.append(_finding("HIGH", "no-evidence", key, "COMPLIANT needs evidence: a "
                                    "profile item, a solution component or a document"))
            if not row.get("section"):
                out.append(_finding("MEDIUM", "no-section", key, "name the proposal section that "
                                    "answers it"))
        for ref in row.get("evidence") or []:
            if str(ref).startswith("C-"):
                item = items.get(ref)
                if item is None:
                    out.append(_finding("BLOCKER", "unsupported-claim", key, "cites company item "
                                        "%s, which the company profile does not contain" % ref))
                elif not item.get("approved") or not item.get("evidence"):
                    out.append(_finding("HIGH", "unapproved-claim", key, "company item %s has no "
                                        "approved evidence" % ref))
                elif item.get("valid_until") and str(item["valid_until"]) < str(doc.get("deadline") or ""):
                    out.append(_finding("HIGH", "expired-claim", key, "company item %s expires "
                                        "(%s) before the submission deadline" % (ref, item["valid_until"])))
        if row.get("claim") == "existing_capability" and not any(
                str(ref).startswith("C-") for ref in row.get("evidence") or []):
            out.append(_finding("BLOCKER", "unsupported-claim", key, "presented as an existing "
                                "capability without approved company evidence"))
    for item in doc.get("clarifications") or []:
        if not Q_ID.match(str(item.get("id") or "")):
            out.append(_finding("LOW", "question-id", item.get("id"), "question ids are Q-01..."))
        for ref in item.get("requirements") or []:
            if ref not in requirements:
                out.append(_finding("MEDIUM", "unknown-requirement", item.get("id"),
                                    "linked to unknown %s" % ref))
    return out


def disqualifying(doc):
    """Mandatory requirements that, as answered now, could exclude the bid."""
    matrix = by_id(doc.get("compliance"))
    out = []
    for req in doc.get("requirements") or []:
        if req.get("superseded_by") or not req.get("mandatory"):
            continue
        row = matrix.get(req["id"]) or {}
        status = row.get("status") or "unanswered"
        if status in ("NON_COMPLIANT", "unanswered", "PENDING_VERIFICATION") or \
                (status == "PARTIALLY_COMPLIANT" and req.get("category") in
                 ("eligibility", "certification", "registration", "forms", "submission")):
            out.append({"requirement": req["id"], "category": req.get("category"),
                        "status": status, "text": req.get("text")})
    return out


# --------------------------------------------------------------------------- bid / no-bid

def bid_decision(assessment, doc):
    """A recommendation from the factor ratings (each 1-5 with evidence, or unknown) and
    the disqualifying conditions. It is a recommendation; the owner decides."""
    factors = assessment.get("factors") or {}
    rated = {name: value for name, value in factors.items()
             if isinstance(value, dict) and isinstance(value.get("rating"), (int, float))}
    unknown = [name for name in FACTORS if name not in rated]
    blockers = disqualifying(doc)
    hard = [item for item in blockers if item["status"] == "NON_COMPLIANT"
            and item["category"] in ("eligibility", "certification", "registration")]
    reasons = []
    if hard:
        reasons.append("mandatory %s not met: %s" % (
            ", ".join(sorted({item["category"] for item in hard})),
            ", ".join(item["requirement"] for item in hard)))
    low = [name for name, value in rated.items() if value["rating"] <= 1]
    weak = [name for name, value in rated.items() if value["rating"] == 2]
    if hard and not assessment.get("conditions"):
        decision = "NO_BID"
    elif len(unknown) > len(FACTORS) // 2:
        decision = "INSUFFICIENT_INFORMATION"
        reasons.append("not assessed: %s" % ", ".join(unknown))
    elif low:
        decision = "NO_BID" if len(low) >= 2 else "BID_WITH_CONDITIONS"
        reasons.append("critical weakness: %s" % ", ".join(low))
    elif hard or weak or [item for item in blockers if item["status"] != "NON_COMPLIANT"]:
        decision = "BID_WITH_CONDITIONS"
        if weak:
            reasons.append("weak: %s" % ", ".join(weak))
        open_items = [item["requirement"] for item in blockers if item["status"] != "NON_COMPLIANT"]
        if open_items:
            reasons.append("mandatory items still open: %s" % ", ".join(open_items[:8]))
    else:
        decision = "BID"
    score = None
    if rated:
        score = round(sum(value["rating"] for value in rated.values()) / float(len(rated)), 2)
    return {"decision": decision, "reasons": reasons, "average_rating": score,
            "unassessed": unknown, "conditions": assessment.get("conditions") or [],
            "disqualifying": blockers,
            "note": "a recommendation from the recorded ratings; the bid decision is yours"}


# --------------------------------------------------------------------------- estimation

def estimate(wbs, hours_per_day=8):
    """Totals of a work breakdown: per package the three-point effort (optimistic, likely,
    pessimistic, person-days), its PERT expectation (o + 4m + p) / 6, and totals by role and
    scenario. A simple schedule follows the dependencies with the stated team sizes."""
    packages = by_id(wbs.get("packages"))
    out, by_role, issues = [], {}, []
    scenario = {"optimistic": Decimal(0), "baseline": Decimal(0), "conservative": Decimal(0)}
    for key, package in packages.items():
        for dep in package.get("depends_on") or []:
            if dep not in packages:
                issues.append({"level": "error", "where": key, "message": "depends on unknown %s" % dep})
        roles = package.get("roles") or []
        if not roles:
            issues.append({"level": "error", "where": key, "message": "no role effort"})
        if package.get("basis") not in ("historical", "benchmark", "heuristic"):
            issues.append({"level": "warning", "where": key,
                           "message": "say whether the estimate is historical, benchmark or heuristic"})
        package_total = {"optimistic": Decimal(0), "baseline": Decimal(0), "conservative": Decimal(0)}
        for role in roles:
            o, m, p = (Decimal(str(role.get(k, 0))) for k in ("optimistic", "likely", "pessimistic"))
            if not (o <= m <= p):
                issues.append({"level": "error", "where": key,
                               "message": "%s: optimistic <= likely <= pessimistic" % role.get("role")})
            expected = (o + 4 * m + p) / 6
            by_role[role.get("role")] = by_role.get(role.get("role"), Decimal(0)) + expected
            package_total["optimistic"] += o
            package_total["baseline"] += expected
            package_total["conservative"] += p
        for name in scenario:
            scenario[name] += package_total[name]
        out.append({"id": key, "name": package.get("name"), "deliverable": package.get("deliverable"),
                    "basis": package.get("basis"), "complexity": package.get("complexity"),
                    "confidence": package.get("confidence"), "assumptions": package.get("assumptions"),
                    "depends_on": package.get("depends_on") or [],
                    "optimistic": package_total["optimistic"],
                    "baseline": package_total["baseline"].quantize(Decimal("0.1")),
                    "conservative": package_total["conservative"],
                    "team": package.get("team", 1)})
    schedule, finish = _schedule(out)
    return {"packages": out, "by_role": {role: value.quantize(Decimal("0.1"))
                                         for role, value in by_role.items()},
            "scenarios": {name: value.quantize(Decimal("0.1")) for name, value in scenario.items()},
            "schedule": schedule, "duration_days": finish, "issues": issues,
            "note": "person-days; PERT expectation; a model of the work, not a commitment"}


def _schedule(packages):
    """Earliest start and finish (working days) of each package from its dependencies,
    with duration = baseline effort / team size."""
    order, done, result = [], set(), {}
    pending = {item["id"]: item for item in packages}
    for _ in range(len(pending) + 1):
        progressed = False
        for key, item in list(pending.items()):
            if all(dep in done or dep not in {p["id"] for p in packages} for dep in item["depends_on"]):
                start = max([result[dep]["finish"] for dep in item["depends_on"] if dep in result] or [0])
                team = max(1, int(item.get("team") or 1))
                duration = int((item["baseline"] / team).to_integral_value(rounding="ROUND_CEILING"))
                result[key] = {"id": key, "start": start, "finish": start + duration,
                               "duration": duration}
                order.append(key)
                done.add(key)
                del pending[key]
                progressed = True
        if not pending or not progressed:
            break
    for key in pending:
        result[key] = {"id": key, "start": None, "finish": None, "duration": None,
                       "note": "circular dependency"}
    finish = max([row["finish"] for row in result.values() if row["finish"] is not None] or [0])
    return [result[key] for key in order] + [result[key] for key in pending], finish


# --------------------------------------------------------------------------- review

def review(doc, documents, profile, pricing_result, outputs, deadline_days=None):
    """Deterministic review findings (verified defects). Advisory findings from a reader
    are recorded separately and marked unverified."""
    findings = [f for f in validate(doc, documents, profile)]
    for item in disqualifying(doc):
        findings.append(_finding("BLOCKER", "disqualifying", item["requirement"],
                                 "mandatory %s requirement is %s" % (item["category"], item["status"])))
    estimate_data = doc.get("estimate") or {}
    duration = estimate_data.get("duration_days")
    limit = doc.get("delivery_days")
    if duration and limit and int(duration) > int(limit):
        findings.append(_finding("HIGH", "timeline", "schedule", "the estimated schedule (%s working "
                                 "days) exceeds the delivery period (%s days)" % (duration, limit)))
    if pricing_result is not None:
        for name in pricing_result["pending"]:
            findings.append(_finding("HIGH", "pending-price", name, "no confirmed or estimated cost"))
        if pricing_result["estimates"]:
            findings.append(_finding("MEDIUM", "estimated-price", "pricing",
                                     "planning estimates instead of quotations: %s"
                                     % ", ".join(pricing_result["estimates"])))
        if pricing_result["assumed_fx"]:
            findings.append(_finding("MEDIUM", "assumed-fx", "pricing", "exchange rates not "
                                     "confirmed: %s" % ", ".join(pricing_result["assumed_fx"])))
    for output in outputs or []:
        for problem in output.get("problems") or []:
            findings.append(_finding(problem.get("severity", "HIGH"), problem.get("code", "output"),
                                     output.get("kind"), problem["message"]))
    for item in doc.get("forms") or []:
        if item.get("required") and item.get("status") not in ("prepared", "signed", "attached"):
            findings.append(_finding("HIGH", "missing-form", item.get("name"),
                                     "required form or attachment is %s" % (item.get("status") or "missing")))
        if item.get("signature") and item.get("status") != "signed":
            findings.append(_finding("HIGH", "signature", item.get("name"), "needs a signature"))
    for item in doc.get("advisory") or []:
        findings.append(dict(item, verified=False))
    order = {name: index for index, name in enumerate(SEVERITIES)}
    findings.sort(key=lambda item: (order.get(item["severity"], 9), str(item["where"])))
    return findings
