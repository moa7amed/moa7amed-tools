"""Replica: the rules of application reconstruction (no I/O here).

A replica project rebuilds an application from evidence: references (URLs, screenshots,
recordings, source, documents, API and database specifications), an inventory of screens,
features and user flows built from them, design tokens, and the plan that turns all of it
into queue tasks. This module holds the vocabulary, the intent detection used by the
capture hook and the replica skill, the choice of mode, the evidence rules, the parity
matrix and the plan generation. Storage is in replica_store.py.

Evidence classes (every screen, feature, flow and design token carries one):

    OBSERVED        seen in a reference that is available (a capture, a screenshot)
    DOCUMENTED      stated in a supplied document, specification, schema or source
    USER_CONFIRMED  stated by the owner
    INFERRED        concluded from evidence, not seen or stated: never a requirement
    UNKNOWN         not known; listed, asked about, never filled in
"""
from __future__ import annotations

import re

from . import router_ar

EVIDENCE = ("OBSERVED", "DOCUMENTED", "USER_CONFIRMED", "INFERRED", "UNKNOWN")
SETTLED = ("OBSERVED", "DOCUMENTED", "USER_CONFIRMED")
MODES = ("visual", "functional", "full")
QUALITY = ("prototype", "mvp", "production")
OWNERSHIP = ("own", "licensed", "third_party", "unknown")
INTENTS = ("REPLICA_DISCOVER", "REPLICA_VISUAL", "REPLICA_FUNCTIONAL", "REPLICA_FULL",
           "REPLICA_COMPARE", "REPLICA_IMPROVE")
# reference kinds: what they can prove
REF_KINDS = {
    "url": "a page of a web application (captured on request, never crawled)",
    "screenshot": "an image of a screen",
    "video": "a screen recording (metadata only unless a frame tool is installed)",
    "html": "an HTML, CSS or JavaScript reference file",
    "source": "application source code (a folder or repository on this machine)",
    "doc": "documentation or a design specification",
    "api": "an API specification (OpenAPI, GraphQL schema, Postman collection)",
    "schema": "an approved database schema",
    "design": "a design file or export",
}
DOCUMENT_KINDS = ("html", "source", "doc", "api", "schema", "design")
VISUAL_KINDS = ("url", "screenshot", "video", "design")
FEATURE_STATES = ("planned", "in_progress", "implemented", "mock", "verified", "failed",
                  "unknown", "out_of_scope")
SCREEN_ID = re.compile(r"^S\d+[a-z]?$")
FEATURE_ID = re.compile(r"^F\d+[a-z]?$")
FLOW_ID = re.compile(r"^U\d+[a-z]?$")

# --------------------------------------------------------------------------- intent

_OBJECT_EN = (r"(web ?site|site|web ?app(lication)?|application|app|landing page|home ?page|"
              r"page|pages|ui|user interface|interface|front-?end|screens?|dashboard|portal|"
              r"system|store|shop)")
_SOURCES_EN = r"(screenshots?|screen ?shots?|recordings?|videos?|mock-?ups?|designs?|wireframes?|figma)"
_REBUILD_EN = re.compile(
    r"\b(re-?build|re-?create|replicate|reconstruct|re-?implement|reproduce|remake|clone|"
    r"rewrite|port)\b[^.?!\n]{0,60}?(\b%s\b|https?://)" % _OBJECT_EN, re.I)
_EQUIVALENT_EN = re.compile(
    r"\b(build|create|make|develop|code|implement|write)\b[^.?!\n]{0,40}?\b(equivalent|similar|"
    r"identical|same|copy|clone|replica|version|working|exact)\b[^.?!\n]{0,40}?"
    r"(\b%s\b|https?://|\b%s\b)" % (_OBJECT_EN, _SOURCES_EN), re.I)
_BASED_ON_EN = re.compile(
    r"\b(build|create|make|develop|code|implement|generate)\b[^.?!\n]{0,60}?\b(based on|from|"
    r"matching|that (looks|works) like|like)\b[^.?!\n]{0,30}?(\b%s\b|https?://)" % _SOURCES_EN,
    re.I)
_NOT_RECONSTRUCTION_EN = re.compile(
    r"\bgit clone\b|\bclone (the|this|my|a|our) (repo|repository|branch)\b|"
    r"\breproduce (the|this|a|that) (bug|issue|error|crash|problem|failure)\b|"
    r"\brebuild (the|this|my)? ?(project|solution|index|cache|docker image|image|container|"
    r"database index|package)s?\b|\bport (number|\d+)\b", re.I)
_QUESTION_EN = re.compile(r"^\s*(how|what|why|which|who|whose|when|where|is it|is this|"
                          r"should i|would it|does|do you know|explain|tell me about)\b", re.I)
_COMPARE_EN = re.compile(
    r"\b(compare|diff)\b[^.?!\n]{0,50}\b(reference|original|replica|rebuild|rebuilt)\b|"
    r"\bhow close is (the |our )?(replica|rebuild|new (app|application|site|version))\b", re.I)
_IMPROVE_EN = re.compile(
    r"\b(make|bring|get)\b[^.?!\n]{0,40}\b(closer|more similar|more like|match better)\b"
    r"[^.?!\n]{0,40}\b(reference|original)\b|\bimprove (the )?(fidelity|similarity|visual match)\b|"
    r"\b(fix|reduce) (the )?(visual )?differences\b[^.?!\n]{0,30}\b(reference|original)\b", re.I)
_DISCOVER_EN = re.compile(
    r"\b(analy[sz]e|inventory|map|catalog(ue)?|survey|discover|document)\b[^.?!\n]{0,40}"
    r"\b(screens|pages|features|flows|user flows)\b[^.?!\n]{0,60}"
    r"(\b(rebuild|reconstruction|replica|recreat\w+)\b|https?://)", re.I)
_FULL_EN = re.compile(r"\b(full|complete|entire|whole|end[- ]to[- ]end)\b[^.?!\n]{0,30}"
                      r"\b(reconstruction|rebuild|application|app|system|stack)\b|"
                      r"\b(back-?end|database|api|server side|source code|including the backend)\b",
                      re.I)
_FUNCTIONAL_EN = re.compile(r"\b(working|functional|functioning|interactive|clickable|that works|"
                            r"behaviou?r|features|functionality|workflows?|crud|forms? that)\b",
                            re.I)
_VISUAL_EN = re.compile(r"\b(visual(ly)?|looks?|look like|design|layout|appearance|pixel|"
                        r"styling|style|ui|interface|screens?)\b", re.I)
_APPLICATION_EN = re.compile(r"\b(web ?app(lication)?|application|app|system|portal|dashboard)\b",
                             re.I)

# Arabic (normalised: alef forms -> ا, ة -> ه, ى -> ي)
_AR_OBJECT = (u"موقع|الموقع|ويب ?سايت|تطبيق|التطبيق|ابلكيشن|برنامج|البرنامج|سيستم|السيستم|"
              u"نظام|النظام|واجهه|الواجهه|صفحه|الصفحه|شاشات|الشاشات|الشاشه|داشبورد|بوابه")
_AR_SOURCES = u"سكرين ?شوت|اسكرين ?شوت|لقطات|اللقطات|صور الشاشه|صور|الصور|فيديو|الفيديو|تسجيلات|التصميم"
_AR_REBUILD = re.compile(
    u"(اعد بناء|اعاده بناء|ابني نسخه|ابن نسخه|اعمل نسخه|اعملي نسخه|اعملي|اعمل|ابني|ابن|انشئ|"
    u"استنسخ|انسخ|كرر|اعد انشاء|اعاده انشاء|اعد تصميم)"
    u"[^.?!؟\n]{0,50}?(" + _AR_OBJECT + u"|https?://|" + _AR_SOURCES + u")")
_AR_COPY_WORDS = re.compile(u"نسخه|زي|مثل|مطابق|شبه|مشابه|نفس|طبق الاصل|بناء|بنفس|على اساس|من ال")
_AR_QUESTION = re.compile(u"^\\s*(ايه|ما هو|ما هي|ماذا|ليه|لماذا|كيف|ازاي|هل|مين|اشرح|قولي|احكيلي)")
_AR_COMPARE = re.compile(u"قارن[^.?!؟\n]{0,40}(الاصل|الاصلي|المرجع|الريبليكا|النسخه)")
_AR_IMPROVE = re.compile(u"(خليه|خليها|اجعله|اجعلها|قربه|قربها)[^.?!؟\n]{0,30}(اقرب|اشبه|مطابق)")
_AR_FULL = re.compile(u"كامل|بالكامل|باك ?اند|الباك ?اند|قاعده البيانات|الداتابيز|الكود المصدري|"
                      u"السورس ?كود|الكود")
_AR_FUNCTIONAL = re.compile(u"يشتغل|شغال|شغاله|الوظائف|بنفس الوظائف|تفاعلي|بيشتغل|يعمل")
_AR_VISUAL = re.compile(u"شكل|الشكل|تصميم|التصميم|واجهه|الواجهه|بصري")


def intent(text):
    """{"intent": one of INTENTS or None, "mode": ..., "reason": ...}.

    A request to reconstruct is recognised by a reconstruction verb applied to an
    application, page or set of screens (or a URL), not by the presence of a URL: a
    question about a website stays a conversation."""
    raw = text or ""
    normalised = router_ar.normalize(raw)
    urls = re.findall(r"https?://[^\s)>\]\"']+", raw)
    question = bool(_QUESTION_EN.match(raw) or _AR_QUESTION.match(normalised))
    if _COMPARE_EN.search(raw) or _AR_COMPARE.search(normalised):
        return {"intent": "REPLICA_COMPARE", "mode": None, "urls": urls,
                "reason": "asks to compare a rebuild with its reference"}
    if _IMPROVE_EN.search(raw) or _AR_IMPROVE.search(normalised):
        return {"intent": "REPLICA_IMPROVE", "mode": None, "urls": urls,
                "reason": "asks to bring a rebuild closer to its reference"}
    english = bool(_REBUILD_EN.search(raw) or _EQUIVALENT_EN.search(raw)
                   or _BASED_ON_EN.search(raw)) and not _NOT_RECONSTRUCTION_EN.search(raw)
    arabic = bool(_AR_REBUILD.search(normalised) and _AR_COPY_WORDS.search(normalised))
    if _DISCOVER_EN.search(raw) and not question:
        return {"intent": "REPLICA_DISCOVER", "mode": None, "urls": urls,
                "reason": "asks to analyse an application before rebuilding it"}
    if question or not (english or arabic):
        return {"intent": None, "mode": None, "urls": urls,
                "reason": "a question or an ordinary request, not a reconstruction"
                if (english or arabic or urls) else "no reconstruction requested"}
    mode = choose_mode(raw)
    return {"intent": "REPLICA_" + mode.upper(), "mode": mode, "urls": urls,
            "reason": "asks to rebuild an application (%s)" % mode}


def choose_mode(text, refs=None):
    """The least extensive mode that satisfies the request. An explicit mention of a
    backend, database, API or source code asks for full; working behaviour, or an
    application rather than pages, asks for functional; otherwise visual."""
    raw = text or ""
    normalised = router_ar.normalize(raw)
    if _FULL_EN.search(raw) or _AR_FULL.search(normalised):
        return "full"
    if _FUNCTIONAL_EN.search(raw) or _AR_FUNCTIONAL.search(normalised):
        return "functional"
    if _VISUAL_EN.search(raw) or _AR_VISUAL.search(normalised):
        return "visual"
    if _APPLICATION_EN.search(raw) or re.search(u"تطبيق|برنامج|نظام|سيستم", normalised):
        return "functional"
    return "visual"


def full_eligibility(refs):
    """Full reconstruction needs evidence of what is behind the screens: source code, an
    API specification, a database schema or documentation. Returns (eligible, missing)."""
    kinds = {ref["kind"] for ref in refs if ref.get("status") != "unavailable"}
    behind = kinds & {"source", "api", "schema", "doc"}
    missing = []
    if not behind:
        missing.append("source code, an API specification, a database schema or documentation "
                       "of the business rules")
    if not kinds & set(VISUAL_KINDS) and "source" not in kinds:
        missing.append("a visual reference (screenshots, a URL or a recording)")
    return (not missing, missing)


def effective_mode(requested, refs):
    """(mode, note): a full request without the evidence for it runs as functional."""
    if requested == "full":
        eligible, missing = full_eligibility(refs)
        if not eligible:
            return "functional", ("full reconstruction needs %s; until that is supplied the "
                                  "project runs in functional mode" % " and ".join(missing))
    return requested, None


# --------------------------------------------------------------------------- the document

def new_document():
    return {"screens": [], "features": [], "flows": [], "design": {"tokens": []},
            "unknowns": [], "notes": [], "candidate": {}, "acceptance": {}}


def by_id(items):
    return {item.get("id"): item for item in items or [] if isinstance(item, dict)}


def _finding(level, code, where, message):
    return {"level": level, "code": code, "where": where, "message": message}


def validate(doc, refs, mode="visual"):
    """Findings about the inventory. Errors are things that would make the reconstruction
    claim more than the evidence supports."""
    out = []
    references = {ref["id"]: ref for ref in refs}
    available = {key for key, ref in references.items() if ref.get("status") != "unavailable"}
    screens, features, flows = by_id(doc.get("screens")), by_id(doc.get("features")), \
        by_id(doc.get("flows"))

    def check_evidence(item, where):
        status = item.get("evidence")
        if status not in EVIDENCE:
            out.append(_finding("error", "evidence", where, "evidence must be one of %s"
                                % ", ".join(EVIDENCE)))
            return
        sources = item.get("refs") or []
        for ref in sources:
            if ref not in references:
                out.append(_finding("error", "unknown-reference", where, "cites %s, which is not "
                                    "a reference of this project" % ref))
        if status == "OBSERVED":
            seen = [ref for ref in sources if ref in available
                    and references[ref]["kind"] in VISUAL_KINDS + ("html",)]
            if not seen:
                out.append(_finding("error", "observed-without-reference", where,
                                    "OBSERVED needs an available visual reference (a capture, "
                                    "screenshot, recording or page source) in refs"))
        if status == "DOCUMENTED":
            if not [ref for ref in sources if ref in available
                    and references[ref]["kind"] in DOCUMENT_KINDS]:
                out.append(_finding("error", "documented-without-document", where,
                                    "DOCUMENTED needs a document, specification, schema or "
                                    "source reference in refs"))

    for key in screens:
        if not key or not SCREEN_ID.match(str(key)):
            out.append(_finding("error", "screen-id", key, "screen ids are S1, S2, ..."))
    for key in features:
        if not key or not FEATURE_ID.match(str(key)):
            out.append(_finding("error", "feature-id", key, "feature ids are F1, F2, ..."))
    for key in flows:
        if not key or not FLOW_ID.match(str(key)):
            out.append(_finding("error", "flow-id", key, "flow ids are U1, U2, ..."))
    for key, screen in screens.items():
        check_evidence(screen, key)
        if not screen.get("name"):
            out.append(_finding("error", "screen-name", key, "a screen needs a name"))
        for target in screen.get("navigates_to") or []:
            if target not in screens:
                out.append(_finding("warning", "unknown-destination", key,
                                    "navigates to %s, which is not in the inventory" % target))
    for key, feature in features.items():
        check_evidence(feature, key)
        for screen in feature.get("screens") or []:
            if screen not in screens:
                out.append(_finding("error", "unknown-screen", key, "on %s, which is not in the "
                                    "inventory" % screen))
        state = feature.get("state", "planned")
        if state not in FEATURE_STATES:
            out.append(_finding("error", "feature-state", key, "state must be one of %s"
                                % ", ".join(FEATURE_STATES)))
        if feature.get("evidence") in ("INFERRED", "UNKNOWN") and feature.get("requirement"):
            out.append(_finding("error", "inferred-requirement", key,
                                "an %s feature cannot be a requirement: confirm it or document it"
                                % feature["evidence"].lower()))
        if feature.get("backend") and mode == "visual" and state not in ("out_of_scope", "mock"):
            out.append(_finding("warning", "backend-in-visual", key,
                                "visual mode rebuilds the interface: this backend behaviour is "
                                "out of scope or a labelled mock"))
        if feature.get("evidence") == "UNKNOWN":
            out.append(_finding("info", "unknown", key, "behaviour unknown: %s"
                                % (feature.get("name") or key)))
    for key, flow in flows.items():
        check_evidence(flow, key)
        steps = flow.get("steps") or []
        if not steps:
            out.append(_finding("error", "flow-steps", key, "a flow needs its steps"))
        for step in steps:
            screen = step.get("screen") if isinstance(step, dict) else None
            if screen and screen not in screens:
                out.append(_finding("error", "unknown-screen", key, "step on %s, which is not in "
                                    "the inventory" % screen))
        if flow.get("evidence") in ("INFERRED", "UNKNOWN") and flow.get("outcome_claimed"):
            out.append(_finding("error", "invented-outcome", key,
                                "a successful outcome is claimed for a flow nobody observed or "
                                "documented"))
    for token in (doc.get("design") or {}).get("tokens") or []:
        if token.get("evidence") not in EVIDENCE:
            out.append(_finding("error", "evidence", "token %s" % token.get("name"),
                                "evidence must be one of %s" % ", ".join(EVIDENCE)))
    if mode in ("functional", "full") and not features:
        out.append(_finding("warning", "no-features", "inventory",
                            "%s mode without a feature matrix" % mode))
    if not screens:
        out.append(_finding("warning", "no-screens", "inventory", "no screens recorded yet"))
    return out


# --------------------------------------------------------------------------- parity

def parity(doc, tests, comparisons, tasks_by_feature=None):
    """The feature parity matrix: per feature, the evidence, what was expected, what was
    built (from the queue), the tests recorded, and a verdict that only says "verified"
    when a real (not mocked) test passed. A visual comparison never verifies a feature."""
    tasks_by_feature = tasks_by_feature or {}
    rows = []
    for feature in doc.get("features") or []:
        key = feature.get("id")
        own = [test for test in tests if test.get("feature") == key]
        latest = own[-1] if own else None
        states = [task["status"] for task in tasks_by_feature.get(key, [])]
        built = ("done" if states and all(state == "done" for state in states)
                 else "in progress" if states else "not started")
        if feature.get("state") == "out_of_scope":
            verdict = "out of scope"
        elif feature.get("evidence") == "UNKNOWN":
            verdict = "unknown behaviour"
        elif latest and latest["result"] == "fail":
            verdict = "failing"
        elif latest and latest["result"] == "pass" and latest["kind"] == "real":
            verdict = "verified"
        elif latest and latest["result"] == "pass":
            verdict = "works against a mock"
        elif feature.get("state") == "mock" or (latest and latest["kind"] == "mock"):
            verdict = "mock"
        elif built == "done":
            verdict = "built, not verified"
        else:
            verdict = built
        rows.append({"feature": key, "name": feature.get("name"),
                     "evidence": feature.get("evidence"),
                     "expected": feature.get("behavior") or feature.get("expected"),
                     "backend": bool(feature.get("backend")), "built": built,
                     "tests": len(own), "last_test": latest, "verdict": verdict})
    screens = {}
    for item in comparisons:
        screens[item["screen"]] = item
    totals = {}
    for row in rows:
        totals[row["verdict"]] = totals.get(row["verdict"], 0) + 1
    return {"features": rows, "totals": totals,
            "screens": [{"screen": key, "score": value.get("score"), "method": value.get("method"),
                         "at": value.get("created_at")} for key, value in sorted(screens.items())]}


# --------------------------------------------------------------------------- plan generation

STAGES = (("M1", "Reference analysis and requirements"), ("M2", "UI foundation"),
          ("M3", "Screens"), ("M4", "Backend and data"), ("M5", "Integrations"),
          ("M6", "Functional tests"), ("M7", "Visual verification"),
          ("M8", "Final verification"))


def _screen_lines(screen, refs_by_id, data_dir_label):
    lines = ["Screen %s %s (%s)" % (screen["id"], screen.get("name"), screen.get("evidence"))]
    if screen.get("route"):
        lines.append("  page in the rebuild: %s" % screen["route"])
    if screen.get("layout"):
        lines.append("  layout: %s" % screen["layout"])
    if screen.get("components"):
        lines.append("  components: %s" % ", ".join(screen["components"]))
    if screen.get("actions"):
        lines.append("  user actions: %s" % ", ".join(screen["actions"]))
    if screen.get("navigates_to"):
        lines.append("  navigates to: %s" % ", ".join(screen["navigates_to"]))
    for ref in screen.get("refs") or []:
        item = refs_by_id.get(ref)
        if item and item.get("path") and item["kind"] in ("screenshot", "design"):
            lines.append("  reference image: %s (read it; do not copy its assets)" % item["path"])
    if screen.get("unknown"):
        lines.append("  unknown (do not invent): %s" % "; ".join(screen["unknown"]))
    return lines


def _feature_lines(feature):
    lines = ["Feature %s %s (%s)%s" % (feature["id"], feature.get("name"), feature.get("evidence"),
                                       ", backend" if feature.get("backend") else "")]
    if feature.get("behavior"):
        lines.append("  expected: %s" % feature["behavior"])
    if feature.get("evidence") not in SETTLED:
        lines.append("  not settled by evidence: build it as a clearly labelled mock or "
                     "placeholder, and say so in the result")
    return lines


def plan_fragment(project, doc, refs, mode, quality):
    """Milestones, requirements and tasks for the linked project plan. Tasks are grouped
    (foundation, a few screens per task, flows, tests) rather than one per element, and
    each carries only the inventory entries it needs, in `reference`."""
    refs_by_id = {ref["id"]: ref for ref in refs}
    screens = [item for item in doc.get("screens") or [] if item.get("evidence") != "UNKNOWN"]
    features = [item for item in doc.get("features") or []
                if item.get("state") != "out_of_scope"]
    flows = doc.get("flows") or []
    ownership = project.get("ownership") or "unknown"
    asset_rule = ("The reference belongs to the owner of this project: its own assets may be "
                  "reused where the owner supplied them." if ownership in ("own", "licensed")
                  else "The reference is a third-party application: write original code, use "
                  "original branding, names and artwork; reproduce layout and interaction "
                  "patterns only. Do not copy its logos, images, fonts, text content or code.")
    sample = doc.get("sample_data") or {}
    reuse_sample = (ownership in ("own", "licensed") and sample.get("synthetic") is True
                    and sample.get("evidence") == "USER_CONFIRMED")
    data_rule = ("The sample data shown in the reference is synthetic and belongs to the owner "
                 "(confirmed): reproduce the rows listed under the screens exactly, so behaviour "
                 "and screens can be compared." if reuse_sample else
                 "Use synthetic data only; never data copied from the reference.")
    requirements, tasks = [], []
    for feature in features:
        if feature.get("evidence") in SETTLED:
            requirements.append({"id": "R-%s" % feature["id"], "kind": "functional",
                                 "text": "%s: %s" % (feature.get("name"),
                                                     feature.get("behavior") or "as observed"),
                                 "status": "USER_CONFIRMED" if feature["evidence"] ==
                                 "USER_CONFIRMED" else "VERIFIED",
                                 "evidence": ", ".join(feature.get("refs") or []) or "inventory"})
    req_of = {feature["id"]: "R-%s" % feature["id"] for feature in features
              if feature.get("evidence") in SETTLED}
    tokens = (doc.get("design") or {}).get("tokens") or []
    token_lines = ["%s %s = %s (%s)" % (item.get("group", "token"), item.get("name"),
                                        item.get("value"), item.get("evidence"))
                   for item in tokens[:40]]
    tasks.append({
        "id": "T1", "kind": "implementation", "milestone": "M2", "priority": "high",
        "title": "Set up the project structure, layout shell, navigation and design tokens of "
                 "the rebuild (%s)" % project["id"],
        "reference": [asset_rule, data_rule] + token_lines,
        "acceptance": ["the application starts and shows the navigation shell",
                       "design tokens are defined once and used by the components",
                       "the verification line names how the shell was checked"],
        "tests": ["a smoke test that the start page renders"],
        "replica": {"screens": [], "features": []}})
    group = 3 if quality == "prototype" else 2
    number = 2
    screen_tasks = {}
    for start in range(0, len(screens), group):
        batch = screens[start:start + group]
        ids = [item["id"] for item in batch]
        related = [item for item in features if set(item.get("screens") or []) & set(ids)]
        lines = [asset_rule, data_rule]
        for item in batch:
            lines += _screen_lines(item, refs_by_id, None)
            if reuse_sample and item.get("data"):
                lines.append("  sample data: %s" % item["data"])
        if mode != "visual":
            for item in related:
                lines += _feature_lines(item)
        task_id = "T%d" % number
        number += 1
        tasks.append({
            "id": task_id, "kind": "implementation", "milestone": "M3", "depends_on": ["T1"],
            "title": "Build screens %s (%s)" % (", ".join(ids), ", ".join(
                item.get("name") or item["id"] for item in batch)),
            "reference": lines,
            "requirements": [req_of[item["id"]] for item in related if item["id"] in req_of]
            if mode != "visual" else [],
            "acceptance": ["each screen is reachable through the navigation",
                           "the layout matches the reference description and image at the "
                           "reference viewport"]
            + (["the listed features work as described; unsettled ones are labelled mocks"]
               if mode != "visual" and related else []),
            "tests": ["one rendering test per screen"]
            + (["a test per settled feature, named with its feature id"]
               if mode != "visual" and related else []),
            "replica": {"screens": ids, "features": [item["id"] for item in related]
                        if mode != "visual" else []}})
        for screen in ids:
            screen_tasks[screen] = task_id
    built = [task["id"] for task in tasks if task["id"] != "T1"] or ["T1"]
    if mode in ("functional", "full") and flows:
        lines = [data_rule]
        for flow in flows:
            lines.append("Flow %s %s (%s): %s" % (flow["id"], flow.get("name"), flow.get("evidence"),
                                                  " -> ".join(
                                                      (step.get("action") or step.get("screen") or "")
                                                      if isinstance(step, dict) else str(step)
                                                      for step in flow.get("steps") or [])))
            if flow.get("evidence") not in SETTLED:
                lines.append("  not settled by evidence: implement only what is shown, mark the "
                             "rest as a gap")
        tasks.append({"id": "T%d" % number, "kind": "implementation", "milestone": "M3",
                      "depends_on": built, "title": "Connect the user flows %s end to end" %
                      ", ".join(flow["id"] for flow in flows),
                      "reference": lines,
                      "acceptance": ["every settled flow can be completed with synthetic data",
                                     "validation and error states shown in the reference exist"],
                      "tests": ["one automated test per flow, named with its flow id"],
                      "replica": {"flows": [flow["id"] for flow in flows]}})
        built = ["T%d" % number]
        number += 1
    backend = [item for item in features if item.get("backend")]
    if mode == "full" and backend:
        lines = []
        for item in backend:
            lines += _feature_lines(item)
        tasks.append({"id": "T%d" % number, "kind": "implementation", "milestone": "M4",
                      "depends_on": ["T1"], "title": "Implement the data model and backend "
                      "endpoints for %s" % ", ".join(item["id"] for item in backend),
                      "reference": lines + ["Design only from the supplied source, schema or "
                                            "API specification; mark anything else as a mock."],
                      "requirements": [req_of[item["id"]] for item in backend
                                       if item["id"] in req_of],
                      "acceptance": ["the data model follows the supplied schema or "
                                     "specification", "each endpoint has a test"],
                      "tests": ["API tests per endpoint with fixtures"],
                      "replica": {"features": [item["id"] for item in backend]}})
        built.append("T%d" % number)
        number += 1
    if mode != "visual" and features:
        tasks.append({"id": "T%d" % number, "kind": "test", "milestone": "M6",
                      "depends_on": list(built), "title": "Write browser tests for the feature "
                      "parity matrix (one per settled feature, named with the feature id)",
                      "reference": ["Write tests that a real browser or HTTP client runs; a "
                                    "test against a mock must say so in its name (mock)."],
                      "acceptance": ["every settled feature has a test named with its id",
                                     "the test command and its report path are written to the "
                                     "project README"],
                      "replica": {"features": [item["id"] for item in features]}})
        number += 1
    if quality == "production":
        for title, accept in (
                ("Add security controls: input validation, authentication and authorization "
                 "checks, secure headers", "the security checklist in the plan is met"),
                ("Add deployment configuration for the approved target, with backup and "
                 "recovery notes", "the application deploys with the documented steps"),
                ("Assess performance against the stated load and record the results",
                 "measured figures are recorded with their method")):
            tasks.append({"id": "T%d" % number, "kind": "implementation", "milestone": "M8",
                          "depends_on": list(built), "title": title,
                          "acceptance": [accept], "replica": {}})
            number += 1
    milestones = [{"id": key, "title": title} for key, title in STAGES
                  if any(task.get("milestone") == key for task in tasks)]
    return {"milestones": milestones, "requirements": requirements, "tasks": tasks,
            "screen_tasks": screen_tasks}
