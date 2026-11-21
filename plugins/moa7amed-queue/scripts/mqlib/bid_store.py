"""BidStudio: storage and operations of bid projects.

A bid project lives in additive tables of the project's queue database and is linked to a
project plan of the same id for the solution architecture and infrastructure (the
planning engine's evidence, compatibility checks and approvals). Tender documents are
copied into the project's data folder and their text extracted there; company profiles
and rate cards live in the data directory, outside every project and every package.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from . import bid_model as model
from . import bid_pricing as pricing
from . import ooxml, plan_store, util
from .util import QueueError

SOURCE_PREFIX = "bid:"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bid_projects (
      id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, title TEXT, request TEXT NOT NULL, mode TEXT NOT NULL,
      company TEXT, document TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bid_documents (
      project TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, type TEXT, version TEXT,
      issued TEXT, source TEXT, path TEXT, sha256 TEXT, pages INTEGER, method TEXT,
      confidence TEXT, status TEXT NOT NULL, limitation TEXT, unreadable TEXT, supersedes TEXT,
      superseded_by TEXT, amends TEXT, text_path TEXT, added_at TEXT NOT NULL,
      PRIMARY KEY (project, id))""",
    """CREATE TABLE IF NOT EXISTS bid_revisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, number INTEGER NOT NULL,
      note TEXT, sha256 TEXT NOT NULL, content TEXT NOT NULL, at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bid_approvals (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, gate TEXT NOT NULL,
      digest TEXT NOT NULL, note TEXT, at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bid_outputs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, kind TEXT NOT NULL,
      format TEXT NOT NULL, envelope TEXT, path TEXT NOT NULL, sha256 TEXT, revision INTEGER,
      final INTEGER NOT NULL DEFAULT 0, problems TEXT NOT NULL DEFAULT '[]', at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS bid_revisions_frozen BEFORE UPDATE ON bid_revisions
       BEGIN SELECT RAISE(ABORT, 'bid revisions are never changed'); END""",
)
SECTIONS = ("requirements", "compliance", "clarifications", "forms", "advisory", "risks")
OBJECTS = ("tender", "assessment", "wbs", "solution", "technical", "commercial", "pricing",
           "submission")
SCALARS = ("deadline", "delivery_days", "language", "currency", "envelopes")


def ensure_schema(q):
    for statement in SCHEMA:
        q.conn.execute(statement)


def folder(q, project, *parts):
    path = os.path.join(q.dir, "bids", project, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_hash(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- projects

def new_document():
    return {"tender": {}, "requirements": [], "compliance": [], "clarifications": [],
            "assessment": {}, "wbs": {"packages": []}, "solution": {}, "technical": {},
            "commercial": {}, "pricing": {"currency": "USD", "items": []}, "forms": [],
            "advisory": [], "risks": [], "submission": {}}


def create(q, project_id, request, title=None, mode=None, company=None):
    ensure_schema(q)
    if not plan_store.PLAN_ID.match(project_id or ""):
        raise QueueError("a bid id is lowercase letters, digits, '.', '_' or '-' (up to 64)")
    if not (request or "").strip():
        raise QueueError("a bid project needs the request it answers")
    found = model.intent(request)
    mode = mode or (found["mode"] if found["mode"] in model.MODES else None) or "analyze"
    if mode not in model.MODES:
        raise QueueError("mode is one of %s" % ", ".join(model.MODES))
    if company:
        company_profile(company)            # must exist
    if q.conn.execute("SELECT 1 FROM bid_projects WHERE id=?", (project_id,)).fetchone():
        raise QueueError("bid project '%s' already exists" % project_id)
    try:
        plan_store.load(q, project_id)
    except QueueError:
        plan_store.create(q, project_id, title or "Bid %s" % project_id, "new",
                          summary="Solution for the tender of bid project %s" % project_id)
    stamp = util.now_iso()
    document = new_document()
    with q.tx():
        q.conn.execute("INSERT INTO bid_projects(id, plan_id, title, request, mode, company, document, "
                       "revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                       (project_id, project_id, title or "Bid %s" % project_id, request, mode,
                        company, json.dumps(document), stamp, stamp))
        _revision(q, project_id, document, "created: %s mode" % mode)
    return get(q, project_id)


def next_id(q):
    ensure_schema(q)
    number = 1
    while q.conn.execute("SELECT 1 FROM bid_projects WHERE id=?", ("b%d" % number,)).fetchone():
        number += 1
    return "b%d" % number


def get(q, project_id):
    ensure_schema(q)
    row = q.conn.execute("SELECT * FROM bid_projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise QueueError("no bid project '%s' (moa7amed-queue bid list)" % project_id)
    out = dict(row)
    out["document"] = json.loads(row["document"])
    return out


def projects(q):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT id, title, mode, company, revision, updated_at FROM bid_projects ORDER BY created_at")]


def _revision(q, project_id, document, note):
    number = (q.conn.execute("SELECT MAX(number) AS n FROM bid_revisions WHERE project=?",
                             (project_id,)).fetchone()["n"] or 0) + 1
    q.conn.execute("INSERT INTO bid_revisions(project, number, note, sha256, content, at) "
                   "VALUES(?,?,?,?,?,?)", (project_id, number, note[:300], _content_hash(document),
                                           json.dumps(document, ensure_ascii=False), util.now_iso()))
    return number


def revisions(q, project_id):
    ensure_schema(q)
    return [dict(row) for row in q.conn.execute(
        "SELECT number, note, sha256, at FROM bid_revisions WHERE project=? ORDER BY number",
        (project_id,))]


def save(q, project_id, document, note):
    with q.tx():
        number = _revision(q, project_id, document, note)
        q.conn.execute("UPDATE bid_projects SET document=?, revision=?, updated_at=? WHERE id=?",
                       (json.dumps(document, ensure_ascii=False), number, util.now_iso(), project_id))
    return number


def configure(q, project_id, mode=None, company=None, title=None):
    get(q, project_id)
    if mode and mode not in model.MODES:
        raise QueueError("mode is one of %s" % ", ".join(model.MODES))
    if company:
        company_profile(company)
    with q.tx():
        for key, value in (("mode", mode), ("company", company), ("title", title)):
            if value:
                q.conn.execute("UPDATE bid_projects SET %s=? WHERE id=?" % key, (value, project_id))
    return get(q, project_id)


def _list_key(item):
    if isinstance(item, dict):
        for name in ("id", "title", "name"):
            if item.get(name):
                return (name, item[name])
    return None


def _merge_object(target, value):
    """Merge one level of an object section. Lists of entries that have an id, title or
    name (sections, items, components, milestones) are upserted entry by entry, so a stage
    that sends one section does not drop the others; other values are replaced. An entry
    is dropped only with an explicit "remove": true."""
    for sub, subvalue in value.items():
        current = target.get(sub)
        if isinstance(subvalue, list) and isinstance(current, list) and subvalue                 and all(_list_key(item) for item in subvalue) and all(_list_key(item) for item in current):
            index = {_list_key(item): position for position, item in enumerate(current)}
            merged = list(current)
            for item in subvalue:
                ident = _list_key(item)
                if item.get("remove") is True:
                    merged = [entry for entry in merged if _list_key(entry) != ident]
                    index = {_list_key(entry): position for position, entry in enumerate(merged)}
                elif ident in index:
                    merged[index[ident]] = dict(merged[index[ident]], **item)
                else:
                    index[ident] = len(merged)
                    merged.append(dict(item))
            target[sub] = merged
        else:
            target[sub] = subvalue


def apply(q, project_id, fragment, note="updated"):
    """Merge a JSON fragment. List sections are upserted by id; requirements are never
    deleted (a changed requirement is superseded by a new one, with the reason)."""
    if not isinstance(fragment, dict):
        raise QueueError("the fragment must be a JSON object")
    project = get(q, project_id)
    document = project["document"]
    for key, value in fragment.items():
        if key in SECTIONS and isinstance(value, list):
            current = document.get(key) or []
            index = {item.get("id") or item.get("name"): position
                     for position, item in enumerate(current) if isinstance(item, dict)}
            for item in value:
                if not isinstance(item, dict):
                    raise QueueError("%s entries must be objects" % key)
                ident = item.get("id") or item.get("name")
                if key == "requirements" and ident in index:
                    old = current[index[ident]]
                    changed = {k for k in ("text", "quote", "mandatory", "source")
                               if k in item and item[k] != old.get(k)}
                    if changed and not item.get("correction"):
                        raise QueueError("%s already exists: a changed requirement is a new "
                                         "requirement that supersedes it (with the addendum as "
                                         "its source), or mark the edit as a correction of an "
                                         "extraction mistake with \"correction\": \"<why>\"" % ident)
                if ident in index:
                    merged = dict(current[index[ident]])
                    merged.update(item)
                    current[index[ident]] = merged
                else:
                    index[ident] = len(current)
                    current.append(dict(item))
            document[key] = current
        elif key in OBJECTS and isinstance(value, dict):
            _merge_object(document.setdefault(key, {}), value)
        elif key in SCALARS:
            document[key] = value
        elif key == "remove":
            for section, ids in value.items():
                if section == "requirements":
                    raise QueueError("requirements are never removed: supersede them")
                if section in SECTIONS:
                    document[section] = [item for item in document.get(section) or []
                                         if (item.get("id") or item.get("name")) not in ids]
                if section == "pricing_items":
                    pricing_doc = document.setdefault("pricing", {})
                    pricing_doc["items"] = [item for item in pricing_doc.get("items") or []
                                            if item.get("id") not in ids]
        else:
            raise QueueError("unknown section '%s'" % key)
    # supersession is recorded on both sides
    by_id = model.by_id(document.get("requirements"))
    for req in document.get("requirements") or []:
        old = req.get("supersedes")
        if old and old in by_id and by_id[old].get("superseded_by") != req["id"]:
            by_id[old]["superseded_by"] = req["id"]
    number = save(q, project_id, document, note)
    return {"revision": number, "findings": model.validate(document, texts(q, project_id),
                                                           company_profile(project["company"])
                                                           if project["company"] else None)}


# --------------------------------------------------------------------------- documents

TEXT_TYPES = (".txt", ".md", ".markdown", ".csv")


def _next_doc_id(q, project_id):
    numbers = [int(row["id"][1:]) for row in q.conn.execute(
        "SELECT id FROM bid_documents WHERE project=?", (project_id,)) if row["id"][1:].isdigit()]
    return "D%d" % (max(numbers) + 1 if numbers else 1)


def extract(path):
    """(text, info) for a document. info: method, pages, unreadable pages, confidence,
    limitation. Uses the libraries that are installed; never silently drops a page."""
    extension = os.path.splitext(path)[1].lower()
    info = {"method": None, "pages": None, "unreadable": [], "confidence": "high", "limitation": None}
    if extension in TEXT_TYPES:
        with open(path, "rb") as handle:
            raw = handle.read()
        for encoding in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        info["method"] = "text (%s)" % encoding
        return text, info
    if extension == ".docx":
        blocks = ooxml.docx_blocks(path)
        info["method"] = "docx (built-in reader)"
        return "\n".join(("## " if kind.startswith("heading") else "") + text
                         for kind, text in blocks), info
    if extension in (".xlsx", ".xlsm"):
        info["method"] = "xlsx (built-in reader)"
        return ooxml.xlsx_text(path), info
    if extension == ".pdf":
        return _pdf_text(path, info)
    info.update(confidence="none", limitation="no reader for %s files: read it visually or "
                "convert it to PDF, DOCX or text" % (extension or "these"))
    return "", info


def _pdf_text(path, info):
    pages = None
    try:
        import pypdf  # noqa: F401
        reader = pypdf.PdfReader(path)
        pages = [(page.extract_text() or "") for page in reader.pages]
        info["method"] = "pdf (pypdf %s)" % pypdf.__version__
    except Exception:  # noqa: BLE001 - try the next reader
        pages = None
    if pages is None:
        try:
            import fitz  # PyMuPDF
            with fitz.open(path) as pdf:
                pages = [page.get_text() for page in pdf]
            info["method"] = "pdf (PyMuPDF)"
        except Exception:  # noqa: BLE001
            pages = None
    if pages is None and shutil.which("pdftotext"):
        try:
            result = subprocess.run(["pdftotext", "-layout", path, "-"], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=120, **util.no_window_kwargs())
            pages = result.stdout.decode("utf-8", "replace").split("\f")
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            info["method"] = "pdf (pdftotext)"
        except Exception:  # noqa: BLE001
            pages = None
    if pages is None:
        info.update(confidence="none", limitation="no PDF reader is installed (pypdf, PyMuPDF or "
                    "pdftotext): read the document visually, page by page")
        return "", info
    info["pages"] = len(pages)
    info["unreadable"] = [number for number, text in enumerate(pages, 1) if len(text.strip()) < 20]
    if info["unreadable"]:
        info["confidence"] = "partial"
        info["limitation"] = ("no text layer on page(s) %s (scanned or images): those pages must "
                              "be read visually (or with OCR) and their content recorded by hand"
                              % ", ".join(str(n) for n in info["unreadable"]))
    text = "\n".join("=== page %d ===\n%s" % (number, page) for number, page in enumerate(pages, 1))
    return text, info


def page_images(path, pages, out_dir):
    """Render pages of a PDF to PNG (PyMuPDF) so they can be read visually."""
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return []
    made = []
    with fitz.open(path) as pdf:
        for number in pages:
            if 1 <= number <= len(pdf):
                target = os.path.join(out_dir, "page-%03d.png" % number)
                pdf[number - 1].get_pixmap(dpi=110).save(target)
                made.append(target)
    return made


def add_document(q, project_id, path, doc_type=None, version=None, issued=None, source=None,
                 supersedes=None, name=None, amends=None):
    get(q, project_id)
    if not os.path.isfile(path or ""):
        with q.tx():
            doc_id = _next_doc_id(q, project_id)
            q.conn.execute("INSERT INTO bid_documents(project, id, name, type, status, limitation, "
                           "added_at, source) VALUES(?,?,?,?,?,?,?,?)",
                           (project_id, doc_id, name or os.path.basename(path or "?"), doc_type,
                            "missing", "the file was not found: %s" % path, util.now_iso(), source))
        return document(q, project_id, doc_id)
    if doc_type and doc_type not in model.DOC_TYPES:
        raise QueueError("type is one of %s" % ", ".join(model.DOC_TYPES))
    sha = _sha(path)
    known = q.conn.execute("SELECT id FROM bid_documents WHERE project=? AND sha256=?",
                           (project_id, sha)).fetchone()
    if known:
        return dict(document(q, project_id, known["id"]), duplicate=True)
    for other in (supersedes, amends):
        if other:
            document(q, project_id, other)
    base = folder(q, project_id, "documents")
    with q.tx():
        doc_id = _next_doc_id(q, project_id)
        q.conn.execute("INSERT INTO bid_documents(project, id, name, status, added_at) "
                       "VALUES(?,?,?,?,?)", (project_id, doc_id, name or os.path.basename(path),
                                             "reading", util.now_iso()))
    extension = os.path.splitext(path)[1].lower()
    stored = os.path.join(base, "%s%s" % (doc_id, extension))
    shutil.copyfile(path, stored)
    text, info = extract(stored)
    text_path = os.path.join(base, "%s.txt" % doc_id)
    with open(text_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    status = "read" if info["confidence"] == "high" else (
        "partly read" if info["confidence"] == "partial" else "not read")
    with q.tx():
        q.conn.execute(
            "UPDATE bid_documents SET type=?, version=?, issued=?, source=?, path=?, sha256=?, pages=?, "
            "method=?, confidence=?, status=?, limitation=?, unreadable=?, supersedes=?, text_path=?, "
            "amends=? WHERE project=? AND id=?",
            (doc_type or _guess_type(path, text), version, issued, source or "supplied by the user",
             stored, sha, info["pages"], info["method"], info["confidence"], status,
             info["limitation"], json.dumps(info["unreadable"]), supersedes, text_path,
             amends, project_id, doc_id))
        if supersedes:
            q.conn.execute("UPDATE bid_documents SET superseded_by=? WHERE project=? AND id=?",
                           (doc_id, project_id, supersedes))
    return document(q, project_id, doc_id)


def _guess_type(path, text):
    name = os.path.basename(path).lower() + " " + (text or "")[:400].lower()
    for key, words in (("addendum", ("addendum", "addenda", "corrigendum", u"ملحق")),
                       ("clarification", ("clarification", u"استيضاح", u"استفسار")),
                       ("rfq", ("rfq", "request for quotation")),
                       ("rfp", ("rfp", "request for proposal", u"كراسة", u"كراسه")),
                       ("quotation", ("quotation", "quote no", u"عرض سعر")),
                       ("tender", ("tender", u"مناقصة", u"منافسة"))):
        if any(word in name for word in words):
            return key
    return "other"


def documents(q, project_id):
    ensure_schema(q)
    out = []
    for row in q.conn.execute("SELECT * FROM bid_documents WHERE project=? ORDER BY "
                              "CAST(substr(id, 2) AS INTEGER)", (project_id,)):
        item = dict(row)
        item["unreadable"] = json.loads(row["unreadable"] or "[]")
        out.append(item)
    return out


def document(q, project_id, doc_id):
    for item in documents(q, project_id):
        if item["id"] == doc_id:
            return item
    raise QueueError("no document %s in bid %s" % (doc_id, project_id))


def texts(q, project_id):
    out = {}
    for item in documents(q, project_id):
        text = None
        if item.get("text_path") and os.path.isfile(item["text_path"]):
            with open(item["text_path"], encoding="utf-8") as handle:
                text = handle.read()
        out[item["id"]] = {"text": text, "status": item["status"],
                           "superseded_by": item.get("superseded_by"), "amended_by": []}
    for item in documents(q, project_id):
        if item.get("amends") and item["amends"] in out:
            out[item["amends"]]["amended_by"].append(item["id"])
    return out


def record_visual_reading(q, project_id, doc_id, page, text):
    """Text of a page read visually (scanned pages): appended to the document's text with
    its page, so requirements quoted from it can be traced."""
    item = document(q, project_id, doc_id)
    with open(item["text_path"], "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n=== page %s (read visually) ===\n%s\n" % (page, text))
    remaining = [number for number in item["unreadable"] if str(number) != str(page)]
    with q.tx():
        q.conn.execute("UPDATE bid_documents SET unreadable=?, status=?, confidence=?, limitation=? "
                       "WHERE project=? AND id=?",
                       (json.dumps(remaining), "read" if not remaining else "partly read",
                        "visual" if not remaining else "partial",
                        None if not remaining else item["limitation"], project_id, doc_id))
    return document(q, project_id, doc_id)


# --------------------------------------------------------------------------- company profiles

def companies_dir():
    path = os.path.join(util.home(), "companies")
    os.makedirs(path, exist_ok=True)
    return path


def company_profile(company_id):
    if not company_id:
        return None
    path = os.path.join(companies_dir(), company_id, "profile.json")
    if not os.path.isfile(path):
        raise QueueError("no company profile '%s' (moa7amed-queue bid company new %s)"
                         % (company_id, company_id))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_company(company_id, profile):
    if not plan_store.PLAN_ID.match(company_id or ""):
        raise QueueError("a company id is lowercase letters, digits, '.', '_' or '-'")
    target = os.path.join(companies_dir(), company_id)
    os.makedirs(target, exist_ok=True)
    util.save_json_atomic(os.path.join(target, "profile.json"), profile)
    return profile


def company_new(company_id, name):
    target = os.path.join(companies_dir(), company_id, "profile.json")
    if os.path.exists(target):
        raise QueueError("company profile '%s' already exists" % company_id)
    return save_company(company_id, {"id": company_id, "legal_name": name, "items": []})


def company_apply(company_id, fragment):
    profile = company_profile(company_id)
    for key, value in fragment.items():
        if key == "items":
            items = {item.get("id"): item for item in profile.get("items") or []}
            for item in value:
                if not str(item.get("id") or "").startswith("C-"):
                    raise QueueError("company item ids start with C- (C-CERT-ISO27001)")
                items[item["id"]] = dict(items.get(item["id"]) or {}, **item)
            profile["items"] = list(items.values())
        else:
            profile[key] = value
    return save_company(company_id, profile)


def rate_card(company_id):
    path = os.path.join(companies_dir(), company_id or "-", "ratecard.json")
    if not company_id or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_rate_card(company_id, card):
    company_profile(company_id)
    util.save_json_atomic(os.path.join(companies_dir(), company_id, "ratecard.json"), card)
    return card


# --------------------------------------------------------------------------- analysis

def estimate(q, project_id):
    project = get(q, project_id)
    result = model.estimate(project["document"].get("wbs") or {})
    document_ = project["document"]
    document_["estimate"] = {"by_role": {k: str(v) for k, v in result["by_role"].items()},
                             "scenarios": {k: str(v) for k, v in result["scenarios"].items()},
                             "duration_days": result["duration_days"]}
    save(q, project_id, document_, "estimate recalculated")
    return result


def labour_from_estimate(q, project_id):
    """Pricing lines for the labour of the baseline estimate, at the company's rate card."""
    project = get(q, project_id)
    result = model.estimate(project["document"].get("wbs") or {})
    card = rate_card(project["company"])
    items = pricing.labor_items(result, card, project["document"].get("pricing", {}).get(
        "currency", "USD"))
    apply(q, project_id, {"pricing": {"items": items}}, "labour priced from the estimate")
    return items


def price(q, project_id, scenario=None):
    project = get(q, project_id)
    return pricing.compute(project["document"].get("pricing") or {}, scenario)


def decision(q, project_id):
    project = get(q, project_id)
    return model.bid_decision(project["document"].get("assessment") or {}, project["document"])


# --------------------------------------------------------------------------- approvals

def gate_digest(document_, gate):
    if gate == "pricing":
        return pricing.digest(document_.get("pricing") or {})
    if gate == "architecture":
        return _content_hash(document_.get("solution") or {})
    if gate == "compliance_commitments":
        return _content_hash([row for row in document_.get("compliance") or []])
    if gate == "licensing":
        return _content_hash([item for item in (document_.get("pricing") or {}).get("items") or []
                              if item.get("category") in ("license", "subscription")])
    if gate == "contract_exceptions":
        return _content_hash(document_.get("commercial", {}).get("exceptions") or [])
    return _content_hash(document_)


def approve(q, project_id, gate, note=None, confirmed=False):
    if gate not in model.GATES:
        raise QueueError("gate is one of %s" % ", ".join(model.GATES))
    project = get(q, project_id)
    document_ = project["document"]
    if gate == "pricing":
        result = pricing.compute(document_.get("pricing") or {})
        if result["pending"] or not result["lines"]:
            raise QueueError("prices cannot be approved while lines are pending: %s"
                             % (", ".join(result["pending"]) or "there are no lines"))
        summary = result["totals"]
        preview = "selling price %s %s, total bid price %s, gross margin %s" % (
            summary["currency"], pricing.fmt(summary["SELLING_PRICE"]),
            pricing.fmt(summary["TOTAL_BID_PRICE"]), pricing.pct(summary["GROSS_MARGIN"]))
    else:
        preview = "%s as of revision %d" % (gate, project["revision"])
    if not confirmed:
        return {"approved": False, "gate": gate, "preview": preview}
    with q.tx():
        q.conn.execute("INSERT INTO bid_approvals(project, gate, digest, note, at) VALUES(?,?,?,?,?)",
                       (project_id, gate, gate_digest(document_, gate), note, util.now_iso()))
    return {"approved": True, "gate": gate, "preview": preview}


def approval_state(q, project_id):
    """{gate: "approved" | "changed since approval" | "not approved"}"""
    project = get(q, project_id)
    out = {}
    for gate in model.GATES:
        row = q.conn.execute("SELECT digest FROM bid_approvals WHERE project=? AND gate=? "
                             "ORDER BY id DESC LIMIT 1", (project_id, gate)).fetchone()
        if row is None:
            out[gate] = "not approved"
        elif row["digest"] == gate_digest(project["document"], gate):
            out[gate] = "approved"
        else:
            out[gate] = "changed since approval"
    return out


# --------------------------------------------------------------------------- outputs

def record_output(q, project_id, kind, fmt_name, envelope, path, final, problems, revision):
    with q.tx():
        q.conn.execute("INSERT INTO bid_outputs(project, kind, format, envelope, path, sha256, revision, "
                       "final, problems, at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (project_id, kind, fmt_name, envelope, path, _sha(path), revision, int(final),
                        json.dumps(problems), util.now_iso()))


def outputs(q, project_id):
    ensure_schema(q)
    rows = [dict(row) for row in q.conn.execute(
        "SELECT * FROM bid_outputs WHERE project=? ORDER BY id", (project_id,))]
    for row in rows:
        row["problems"] = json.loads(row["problems"] or "[]")
    return rows


def latest_outputs(q, project_id):
    latest = {}
    for row in outputs(q, project_id):
        latest[(row["kind"], row["format"])] = row
    return list(latest.values())


# --------------------------------------------------------------------------- status

def status(q, project_id):
    project = get(q, project_id)
    document_ = project["document"]
    docs = documents(q, project_id)
    profile = company_profile(project["company"]) if project["company"] else None
    findings = model.validate(document_, texts(q, project_id), profile)
    requirements = [req for req in document_.get("requirements") or [] if not req.get("superseded_by")]
    matrix = model.by_id(document_.get("compliance"))
    counts = {}
    for req in requirements:
        state = (matrix.get(req["id"]) or {}).get("status", "unanswered")
        counts[state] = counts.get(state, 0) + 1
    priced = None
    try:
        result = pricing.compute(document_.get("pricing") or {})
        priced = {"status": result["status"], "pending": result["pending"],
                  "estimates": result["estimates"],
                  "selling_price": pricing.fmt(result["totals"]["SELLING_PRICE"]),
                  "total_bid_price": pricing.fmt(result["totals"]["TOTAL_BID_PRICE"]),
                  "currency": result["totals"]["currency"]}
    except pricing.PricingError as exc:
        priced = {"status": "error", "error": str(exc)}
    tasks = q.conn.execute("SELECT id, status FROM tasks WHERE source=? OR source=?",
                           (SOURCE_PREFIX + project_id, plan_store.SOURCE_PREFIX + project_id)).fetchall()
    queue_counts = {}
    for row in tasks:
        queue_counts[row["status"]] = queue_counts.get(row["status"], 0) + 1
    return {"project": project_id, "title": project["title"], "mode": project["mode"],
            "company": project["company"], "request": project["request"],
            "revision": project["revision"], "tender": document_.get("tender") or {},
            "documents": [{"id": d["id"], "name": d["name"], "type": d["type"], "status": d["status"],
                           "superseded_by": d["superseded_by"], "limitation": d["limitation"]}
                          for d in docs],
            "requirements": len(requirements),
            "mandatory": len([r for r in requirements if r.get("mandatory")]),
            "compliance": counts, "disqualifying": model.disqualifying(document_),
            "clarifications": {"open": len([c for c in document_.get("clarifications") or []
                                            if c.get("status", "open") == "open"]),
                               "total": len(document_.get("clarifications") or [])},
            "decision": model.bid_decision(document_.get("assessment") or {}, document_),
            "pricing": priced, "approvals": approval_state(q, project_id),
            "outputs": [{"kind": row["kind"], "format": row["format"], "path": row["path"],
                         "final": bool(row["final"]), "problems": len(row["problems"])}
                        for row in latest_outputs(q, project_id)],
            "queue": queue_counts,
            "findings": {severity: len([f for f in findings if f["severity"] == severity])
                         for severity in model.SEVERITIES}}


# --------------------------------------------------------------------------- generation

OUTPUT_KINDS = ("technical", "commercial", "summary", "pricing", "boq", "compliance",
                "compliance-internal", "clarifications")


def generate(q, project_id, kinds=None, final=False, pdf=True, config=None):
    """Write the documents, read each back and check it. A final commercial proposal needs
    approved prices that have not changed since; final documents need the release gate."""
    from . import bid_docs
    project = get(q, project_id)
    document_ = project["document"]
    profile = company_profile(project["company"]) if project["company"] else None
    kinds = kinds or OUTPUT_KINDS
    approvals = approval_state(q, project_id)
    if final and approvals.get("release") != "approved":
        raise QueueError("final documents need the release approval (moa7amed-queue bid approve "
                         "%s release --yes) after the review" % project_id)
    if final and "commercial" in kinds and approvals.get("pricing") != "approved":
        raise QueueError("the selling prices are %s: approve them first (moa7amed-queue bid "
                         "approve %s pricing --yes)" % (approvals.get("pricing"), project_id))
    target = folder(q, project_id, "outputs", "r%03d%s" % (project["revision"],
                                                            "-final" if final else ""))
    result = None
    try:
        result = pricing.compute(document_.get("pricing") or {})
    except pricing.PricingError as exc:
        if any(kind in kinds for kind in ("commercial", "pricing", "boq")):
            raise QueueError("pricing model error: %s" % exc)
    language = document_.get("language") or "en"
    rtl = language == "ar"
    tender = document_.get("tender") or {}
    header = ("%s %s" % (tender.get("reference") or "", tender.get("title") or project["title"])).strip()
    made = []

    def record(kind, fmt_name, envelope, path, problems):
        record_output(q, project_id, kind, fmt_name, envelope, path, final, problems,
                      project["revision"])
        made.append({"kind": kind, "format": fmt_name, "path": path, "problems": problems})

    def document_pair(kind, envelope, blocks, title, checker):
        head = header + ("" if final else " - " + bid_docs.labels(language)["draft"])
        docx = os.path.join(target, "%s-%s.docx" % (project_id, kind))
        bid_docs.write_docx(blocks, docx, title, head, rtl)
        text = ooxml.docx_text(docx)
        problems = checker(text)
        if kind == "technical":
            problems += bid_docs.check_sections(blocks, document_)
        record(kind, "docx", envelope, docx, problems)
        if not pdf:
            return
        html_path = os.path.join(target, "%s-%s.html" % (project_id, kind))
        bid_docs.write_html(blocks, html_path, title, head, rtl)
        pdf_path = os.path.join(target, "%s-%s.pdf" % (project_id, kind))
        ok, how = bid_docs.write_pdf(html_path, pdf_path, config)
        if ok:
            body, pages = bid_docs.pdf_text(pdf_path)
            pdf_problems = checker(body or "") if body is not None else []
            if not pages:
                pdf_problems.append({"severity": "HIGH", "code": "pdf-empty",
                                     "message": "the PDF has no pages"})
            record(kind, "pdf", envelope, pdf_path, pdf_problems)
        else:
            made.append({"kind": kind, "format": "pdf", "path": None,
                         "problems": [{"severity": "MEDIUM", "code": "no-pdf", "message": how}]})
        if os.path.exists(html_path):
            os.remove(html_path)

    if "technical" in kinds:
        document_pair("technical", "technical", bid_docs.technical_blocks(project, document_, profile),
                      bid_docs.labels(language)["technical"],
                      lambda text: bid_docs.check_technical(text, result))
    if "commercial" in kinds:
        document_pair("commercial", "commercial",
                      bid_docs.commercial_blocks(project, document_, profile, result, final),
                      bid_docs.labels(language)["commercial"],
                      lambda text: bid_docs.check_customer(text, result, "the commercial proposal"))
    if "summary" in kinds:
        document_pair("summary", "internal", bid_docs.summary_blocks(
            project, document_, model.bid_decision(document_.get("assessment") or {}, document_),
            result), bid_docs.labels(language)["summary"], lambda text: [])
    if "pricing" in kinds:
        path = os.path.join(target, "%s-pricing-model-INTERNAL.xlsx" % project_id)
        bid_docs.pricing_workbook(document_, result, path)
        record("pricing", "xlsx", "internal", path, [])
    if "boq" in kinds:
        path = os.path.join(target, "%s-boq.xlsx" % project_id)
        bid_docs.boq_workbook(document_, result, path)
        record("boq", "xlsx", "commercial", path,
               bid_docs.check_customer(ooxml.xlsx_text(path), result, "the BOQ"))
    if "compliance" in kinds:
        path = os.path.join(target, "%s-compliance-matrix.xlsx" % project_id)
        bid_docs.compliance_workbook(document_, path)
        text = ooxml.xlsx_text(path)
        problems = bid_docs.check_technical(text, result)
        if result is not None:
            problems += [dict(problem, code="internal-in-technical") for problem in
                         bid_docs.check_customer(text, result, "the compliance matrix")]
        record("compliance", "xlsx", "technical", path, problems)
    if "compliance-internal" in kinds:
        path = os.path.join(target, "%s-compliance-matrix-INTERNAL.xlsx" % project_id)
        bid_docs.compliance_workbook(document_, path, internal=True)
        record("compliance-internal", "xlsx", "internal", path, [])
    if "clarifications" in kinds:
        path = os.path.join(target, "%s-clarifications.xlsx" % project_id)
        bid_docs.clarification_workbook(document_, path)
        record("clarifications", "xlsx", "internal", path, [])
    return {"folder": target, "outputs": made, "final": final}


def review(q, project_id):
    """Verified findings: the model's checks, approvals, and the reconciliation of the BOQ
    with the pricing model. Advisory findings recorded by a reader are included, marked
    unverified."""
    project = get(q, project_id)
    document_ = project["document"]
    profile = company_profile(project["company"]) if project["company"] else None
    extra = []
    try:
        result = pricing.compute(document_.get("pricing") or {})
    except pricing.PricingError as exc:
        result = None
        extra.append({"severity": "BLOCKER", "code": "pricing-error", "where": "pricing",
                      "message": str(exc), "verified": True})
    findings = model.review(document_, texts(q, project_id), profile, result,
                            latest_outputs(q, project_id)) + extra
    approvals = approval_state(q, project_id)
    needed = {"technical": ("architecture",), "commercial": ("pricing",),
              "complete": ("architecture", "pricing")}.get(project["mode"], ())
    for gate in needed:
        if approvals[gate] != "approved":
            findings.append({"severity": "HIGH", "code": "approval", "where": gate,
                             "message": "%s is %s" % (gate, approvals[gate]), "verified": True})
    if result is not None:
        boq = pricing.customer_lines(result)
        reconciled = sum((line["total"] for line in boq if line["total"] is not None), pricing.ZERO)
        if reconciled != result["totals"]["SUBTOTAL"]:
            findings.append({"severity": "BLOCKER", "code": "price-reconciliation", "where": "boq",
                             "message": "BOQ lines add up to %s, the pricing model to %s"
                             % (reconciled, result["totals"]["SUBTOTAL"]), "verified": True})
    return findings


def submission_checklist(q, project_id):
    """What must be true before the package is released, each with its state."""
    project = get(q, project_id)
    document_ = project["document"]
    findings = review(q, project_id)
    approvals = approval_state(q, project_id)
    latest = {(row["kind"], row["format"]): row for row in latest_outputs(q, project_id)}
    items = []

    def add(name, ok, detail=""):
        items.append({"item": name, "state": "done" if ok else "open", "detail": detail})
    add("every document read", all(d["status"] == "read" for d in documents(q, project_id)),
        ", ".join("%s %s" % (d["id"], d["status"]) for d in documents(q, project_id)
                  if d["status"] != "read"))
    add("no blocker in the review", not [f for f in findings if f["severity"] == "BLOCKER"],
        "; ".join(f["message"] for f in findings if f["severity"] == "BLOCKER")[:300])
    add("mandatory requirements answered", not model.disqualifying(document_),
        ", ".join(item["requirement"] for item in model.disqualifying(document_)))
    for gate in model.GATES:
        if gate == "release":
            continue
        add("approval: %s" % gate, approvals[gate] == "approved", approvals[gate])
    for kind, fmt_name in (("technical", "docx"), ("technical", "pdf"), ("commercial", "docx"),
                           ("commercial", "pdf"), ("boq", "xlsx"), ("compliance", "xlsx")):
        row = latest.get((kind, fmt_name))
        add("%s %s generated and checked" % (kind, fmt_name.upper()),
            bool(row) and not row["problems"] and row["revision"] == project["revision"],
            "" if row else "not generated")
    for item in document_.get("forms") or []:
        if item.get("required"):
            add("form: %s" % item.get("name"), item.get("status") in ("prepared", "signed", "attached"),
                item.get("status") or "missing")
    add("release approved by the owner", approvals["release"] == "approved", approvals["release"])
    add("submitted", False, "BidStudio never submits a bid: the owner does")
    return items


# --------------------------------------------------------------------------- queue

PIPELINE = (
    ("T1", "RFP intake: read every document of bid %(id)s and record the tender facts and the "
           "requirements with their sources", []),
    ("T2", "Compliance analysis of bid %(id)s: answer every requirement in the compliance matrix "
           "with evidence and list the disqualifying conditions", ["T1"]),
    ("T3", "Clarification register and bid/no-bid assessment of bid %(id)s", ["T2"]),
    ("T4", "Solution architecture and infrastructure of bid %(id)s in its linked plan", ["T2"]),
    ("T5", "Work breakdown and effort estimate of bid %(id)s", ["T4"]),
    ("T6", "Commercial pricing model of bid %(id)s from the estimate, quotations and the rate card",
     ["T5"]),
    ("T7", "Draft the technical proposal sections of bid %(id)s", ["T4", "T5"]),
    ("T8", "Draft the commercial proposal texts of bid %(id)s", ["T6"]),
    ("T9", "Independent review of bid %(id)s: record advisory findings and correct verified "
           "defects", ["T7", "T8"]),
)
STAGE_SKILLS = {"T1": "bid-analyze", "T2": "bid-compliance", "T3": "bid-clarifications",
                "T4": "bid-technical", "T5": "bid-estimation", "T6": "bid-pricing",
                "T7": "bid-technical", "T8": "bid-commercial", "T9": "bid-review"}
MODE_STAGES = {"analyze": ("T1", "T2", "T3"),
               "technical": ("T1", "T2", "T3", "T4", "T5", "T7", "T9"),
               "commercial": ("T1", "T2", "T5", "T6", "T8", "T9"),
               "complete": ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"),
               "review": ("T1", "T2", "T9")}


# Model tier per stage (tiers, not model versions; the router maps them to the models that
# are configured and available). Reading tenders and drafting need a balanced model; the
# architecture decision a high-reasoning one; arithmetic is the pricing engine's.
STAGE_TIERS = {"T1": "sonnet", "T2": "sonnet", "T3": "sonnet", "T4": "opus", "T5": "sonnet",
               "T6": "sonnet", "T7": "sonnet", "T8": "sonnet", "T9": "sonnet"}
SKILL_REF = re.compile(r"`?\$\{CLAUDE_PLUGIN_ROOT\}/skills/([a-z-]+)/SKILL\.md`?")


def _skill_body(plugin_root, name):
    path = os.path.join(plugin_root, "skills", name, "SKILL.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) == 3 else text


def stage_instructions(plugin_root, skill, project_id):
    """The stage's skill, ready for an unattended task: the command written as the task's
    permissions allow it, the bid id filled in, and the skills it refers to appended (a task
    cannot read files outside its project)."""
    shim = shim_path(plugin_root)
    body = _skill_body(plugin_root, skill)
    referenced = []
    for match in SKILL_REF.finditer(body):
        if match.group(1) not in referenced and match.group(1) != skill:
            referenced.append(match.group(1))
    body = SKILL_REF.sub(lambda m: "the \"%s\" instructions (appended below)" % m.group(1), body)
    sections = [body]
    for name in referenced:
        sections.append("--- appended: %s ---\n%s" % (name, SKILL_REF.sub(
            lambda m: "the \"%s\" instructions" % m.group(1), _skill_body(plugin_root, name))))
    text = "\n\n".join(sections)
    text = text.replace('sh "${CLAUDE_PLUGIN_ROOT}/bin/moa7amed-queue"', 'sh "%s"' % shim)
    text = re.sub(r"(?m)^.*PowerShell.*\n?", "", text)
    text = text.replace("$ARGUMENTS", project_id).replace("<id>", project_id)
    return text


def queue_pipeline(q, project_id, plugin_root, stages=None):
    """Queue the stages of the bid as ordinary queue tasks with dependencies (source
    bid:<id>). Idempotent: a stage already queued (and not cancelled) is not added again."""
    project = get(q, project_id)
    stages = stages or MODE_STAGES[project["mode"]]
    existing = {row["title"]: row["id"] for row in q.conn.execute(
        "SELECT id, title FROM tasks WHERE source=? AND status!='cancelled'",
        (SOURCE_PREFIX + project_id,))}
    ids, added = {}, []
    for key, title, deps in PIPELINE:
        if key not in stages:
            continue
        full_title = "[bid %s %s] %s" % (project_id, key, title % {"id": project_id})
        if full_title[:200] in existing:
            ids[key] = existing[full_title[:200]]
            continue
        prompt = "\n".join([
            full_title, "",
            "Do stage %s of bid project %s, following the instructions below. Record every result "
            "with the `bid` and `project` commands they name (run them with the Bash tool exactly "
            "as written): the bid project is the record, not files in this folder. Read the "
            "tender documents with `bid text`. Do not submit, send or publish anything."
            % (key, project_id), "",
            stage_instructions(plugin_root, STAGE_SKILLS[key], project_id)])
        after = [ids[dep] for dep in deps if dep in ids]
        tier = None
        if util.effective_config(q.dir).get("bid_stage_models", "policy") == "policy":
            tier = STAGE_TIERS.get(key)
        with q.tx():
            task_id = q.add(prompt, acceptance="- this stage's results are recorded in bid %s\n"
                            "- the verification line names the `bid` command that shows them"
                            % project_id, title=full_title[:200], model=tier,
                            source=SOURCE_PREFIX + project_id, after=after, max_attempts=2)
        ids[key] = task_id
        added.append(task_id)
    return {"added": added, "tasks": ids}


def shim_path(plugin_root):
    """The command a task runs, written the same way in its prompt and in its permission
    rules (forward slashes, which every shell here accepts)."""
    return os.path.join(plugin_root, "bin", "moa7amed-queue").replace("\\", "/")


def task_permissions(q, task, plugin_root):
    """claude arguments for a task of a bid project: access to the bid's folder and
    permission to run the `moa7amed-queue bid` and `project` commands, nothing more."""
    source = str(task["source"] or "")
    if not source.startswith(SOURCE_PREFIX):
        return []
    project_id = source[len(SOURCE_PREFIX):]
    ensure_schema(q)
    if not q.conn.execute("SELECT 1 FROM bid_projects WHERE id=?", (project_id,)).fetchone():
        return []
    shim = shim_path(plugin_root)
    rules = ['Bash(sh "%s" bid *)' % shim, 'Bash(sh "%s" project *)' % shim]
    return ["--add-dir", folder(q, project_id), "--allowedTools"] + rules
