"""`moa7amed-queue bid ...`: the command line of BidStudio."""
from __future__ import annotations

import json
import os

from . import bid_model as model
from . import bid_pricing as pricing
from . import bid_store as bstore
from . import util
from .util import QueueError

NAME = "moa7amed-queue"


def add_parser(command, common):
    p = command("bid", "tender analysis and proposals: documents, requirements, compliance, "
                       "clarifications, estimate, pricing, documents, review")
    actions = p.add_subparsers(dest="bid_action", metavar="ACTION")
    actions.required = True

    def action(name, help_text):
        return actions.add_parser(name, help=help_text, description=help_text, parents=[common])

    a = action("intent", "is this text tender work? (no side effects)")
    a.add_argument("text", nargs="+")
    action("tools", "document readers and PDF engines available on this machine")
    a = action("new", "start a bid project for a request (kept verbatim)")
    a.add_argument("id", nargs="?")
    a.add_argument("--request", required=True)
    a.add_argument("--mode", choices=list(model.MODES))
    a.add_argument("--company", help="company profile id")
    a.add_argument("--title")
    action("list", "list the bid projects")
    a = action("show", "print a bid project as JSON (internal: includes costs)")
    a.add_argument("id")
    a.add_argument("--section")
    a = action("config", "change mode, company or title")
    a.add_argument("id")
    a.add_argument("--mode", choices=list(model.MODES))
    a.add_argument("--company")
    a.add_argument("--title")
    a = action("doc", "add a tender document (or list them)")
    a.add_argument("id")
    a.add_argument("file", nargs="?")
    a.add_argument("--type", choices=list(model.DOC_TYPES))
    a.add_argument("--version")
    a.add_argument("--date")
    a.add_argument("--source")
    a.add_argument("--supersedes", help="id of the document this one replaces entirely")
    a.add_argument("--amends", help="id of the document this addendum changes in part")
    a.add_argument("--name")
    a = action("text", "print the extracted text of a document (with page markers)")
    a.add_argument("id")
    a.add_argument("doc")
    a = action("pages", "render PDF pages to images, to read pages without a text layer")
    a.add_argument("id")
    a.add_argument("doc")
    a.add_argument("--pages", help="e.g. 3,4 (default: the unreadable ones)")
    a = action("read", "record the text of a page read visually")
    a.add_argument("id")
    a.add_argument("doc")
    a.add_argument("page")
    a.add_argument("--data", dest="json_text", metavar="TEXT")
    a.add_argument("--file")
    a = action("apply", "merge a JSON fragment (tender, requirements, compliance, clarifications, "
                        "assessment, wbs, solution, technical, commercial, pricing, forms, risks)")
    a.add_argument("id")
    a.add_argument("--data", dest="json_text", metavar="JSON")
    a.add_argument("--file")
    a.add_argument("--note")
    a = action("check", "validate requirements, compliance, claims and clarifications")
    a.add_argument("id")
    a = action("decision", "bid / no-bid recommendation from the recorded assessment")
    a.add_argument("id")
    a = action("estimate", "compute the effort estimate from the work breakdown")
    a.add_argument("id")
    a = action("labour", "add pricing lines for the estimated labour at the rate card")
    a.add_argument("id")
    a = action("price", "compute the pricing model (internal figures)")
    a.add_argument("id")
    a.add_argument("--scenarios", action="store_true")
    a = action("approve", "approve a gate: architecture, compliance_commitments, licensing, "
                          "pricing, contract_exceptions, release")
    a.add_argument("id")
    a.add_argument("gate", choices=list(model.GATES))
    a.add_argument("--yes", action="store_true", help="confirm (without it: show what is approved)")
    a.add_argument("--note")
    a = action("generate", "write and check the documents (DOCX, PDF, XLSX)")
    a.add_argument("id")
    a.add_argument("--kind", action="append", choices=list(bstore.OUTPUT_KINDS))
    a.add_argument("--final", action="store_true")
    a.add_argument("--no-pdf", action="store_true")
    a = action("outputs", "the generated documents; --text KIND prints one document's text")
    a.add_argument("id")
    a.add_argument("--text", metavar="KIND[/FORMAT]", help="e.g. technical, commercial/pdf, boq")
    a = action("review", "verified findings of the package (and recorded advisory ones)")
    a.add_argument("id")
    a = action("checklist", "the submission checklist")
    a.add_argument("id")
    a = action("queue", "queue the stages of the bid as tasks (intake, compliance, ...)")
    a.add_argument("id")
    a = action("revisions", "the revision history")
    a.add_argument("id")
    a = action("status", "where a bid project stands")
    a.add_argument("id", nargs="?")
    a = action("company", "company profiles: new, show, apply, ratecard")
    a.add_argument("company_action", choices=["new", "show", "apply", "ratecard", "list"])
    a.add_argument("company_id", nargs="?")
    a.add_argument("--name")
    a.add_argument("--data", dest="json_text", metavar="JSON")
    a.add_argument("--file")


def _read(args, kind="JSON"):
    if args.json_text is not None:
        text = args.json_text
    elif args.file:
        text = util.read_text_file(args.file)
    else:
        text = util.read_stdin_text()
    if kind != "JSON":
        return text
    try:
        return json.loads(text)
    except ValueError as exc:
        raise QueueError("not valid JSON: %s" % exc)


def _findings(findings, limit=40):
    return "\n".join("  [%s%s] %s: %s" % (item["severity"], "" if item.get("verified", True)
                                          else ", advisory", item["where"], item["message"])
                     for item in findings[:limit]) or "  no findings"


def _plugin_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(q, args, emit):
    action = args.bid_action
    config = util.effective_config(q.dir)
    if action == "intent":
        found = model.intent(" ".join(args.text))
        emit(args, found, "%s: %s" % (found["intent"] or "not tender work", found["reason"]))
        return 0
    if action == "tools":
        from . import bid_docs
        readers = {}
        for name in ("pypdf", "fitz", "pdfplumber"):
            try:
                __import__(name)
                readers[name] = True
            except Exception:  # noqa: BLE001
                readers[name] = False
        import shutil
        readers["pdftotext"] = bool(shutil.which("pdftotext"))
        engines = bid_docs.pdf_tools(config)
        data = {"pdf_readers": readers, "pdf_engine": engines}
        emit(args, data, "PDF reading: %s\nPDF writing: %s\nDOCX/XLSX: built in (reading and writing)"
             % (", ".join(name for name, ok in readers.items() if ok) or "none - read PDFs visually",
                "browser (Playwright)" if engines["browser"] else "none - DOCX only (save as PDF "
                "from Word)"))
        return 0
    if action == "company":
        return _company(args, emit)
    if action == "new":
        project_id = args.id or bstore.next_id(q)
        project = bstore.create(q, project_id, args.request, title=args.title, mode=args.mode,
                                company=args.company)
        emit(args, {"id": project_id, "mode": project["mode"], "company": project["company"]},
             "Created bid project %s (%s mode%s). Next: add the tender documents (`%s bid doc %s "
             "<file>`)." % (project_id, project["mode"], ", company %s" % project["company"]
                            if project["company"] else ", no company profile yet", NAME, project_id))
        return 0
    if action == "list":
        rows = bstore.projects(q)
        emit(args, rows, "\n".join("  %-10s %-11s %-14s r%-3d %s" % (
            row["id"], row["mode"], row["company"] or "-", row["revision"], row["title"])
            for row in rows) or "no bid projects")
        return 0
    if action == "status" and not args.id:
        rows = bstore.projects(q)
        if len(rows) != 1:
            emit(args, rows, "\n".join("  %s" % row["id"] for row in rows) or "no bid projects")
            return 0
        args.id = rows[0]["id"]
    project_id = args.id
    if action == "show":
        project = bstore.get(q, project_id)
        data = project["document"] if not args.section else {
            args.section: project["document"].get(args.section)}
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if action == "config":
        project = bstore.configure(q, project_id, args.mode, args.company, args.title)
        emit(args, {"mode": project["mode"], "company": project["company"]},
             "Bid %s: %s mode, company %s" % (project_id, project["mode"], project["company"] or "-"))
        return 0
    if action == "doc":
        if not args.file:
            rows = bstore.documents(q, project_id)
            emit(args, rows, "\n".join(
                "  %-4s %-14s %-11s %-12s %s%s%s" % (
                    row["id"], row["type"] or "-", row["status"], row["method"] or "-", row["name"],
                    " (superseded by %s)" % row["superseded_by"] if row["superseded_by"] else "",
                    "\n       limitation: %s" % row["limitation"] if row["limitation"] else "")
                for row in rows) or "no documents yet")
            return 0
        item = bstore.add_document(q, project_id, args.file, args.type, args.version, args.date,
                                   args.source, args.supersedes, args.name, args.amends)
        emit(args, item, "%s %s: %s, %s, %s page(s), read with %s%s" % (
            "Already recorded as" if item.get("duplicate") else "Recorded", item["id"], item["name"],
            item["status"], item["pages"] or "-", item["method"] or "-",
            "\n  limitation: %s" % item["limitation"] if item["limitation"] else ""))
        return 0 if item["status"] != "missing" else 1
    if action == "text":
        item = bstore.document(q, project_id, args.doc)
        with open(item["text_path"], encoding="utf-8") as handle:
            print(handle.read())
        return 0
    if action == "pages":
        item = bstore.document(q, project_id, args.doc)
        pages = [int(p) for p in args.pages.split(",")] if args.pages else item["unreadable"]
        made = bstore.page_images(item["path"], pages, bstore.folder(q, project_id, "pages", args.doc))
        emit(args, made, "\n".join(made) or "no images (PyMuPDF is needed to render PDF pages)")
        return 0
    if action == "read":
        item = bstore.record_visual_reading(q, project_id, args.doc, args.page, _read(args, "TEXT"))
        emit(args, item, "%s page %s recorded; %s" % (args.doc, args.page, item["status"]))
        return 0
    if action == "apply":
        result = bstore.apply(q, project_id, _read(args), args.note or "applied")
        emit(args, result, "Revision %d\n%s" % (result["revision"], _findings(result["findings"])))
        return 1 if any(f["severity"] == "BLOCKER" for f in result["findings"]) else 0
    if action == "check":
        project = bstore.get(q, project_id)
        findings = model.validate(project["document"], bstore.texts(q, project_id),
                                  bstore.company_profile(project["company"]) if project["company"] else None)
        emit(args, findings, _findings(findings))
        return 1 if any(f["severity"] == "BLOCKER" for f in findings) else 0
    if action == "decision":
        data = bstore.decision(q, project_id)
        emit(args, data, "%s (average rating %s)\n%s\n%s" % (
            data["decision"], data["average_rating"], "\n".join("  - " + r for r in data["reasons"]),
            data["note"]))
        return 0
    if action == "estimate":
        data = bstore.estimate(q, project_id)
        lines = ["  %-6s %-40s %8s %8s %8s  %s" % ("WP", "name", "opt.", "base", "cons.", "basis")]
        for item in data["packages"]:
            lines.append("  %-6s %-40s %8s %8s %8s  %s" % (item["id"], (item["name"] or "")[:40],
                                                           item["optimistic"], item["baseline"],
                                                           item["conservative"], item["basis"]))
        lines.append("  by role: %s" % ", ".join("%s %s" % kv for kv in sorted(data["by_role"].items())))
        lines.append("  scenarios (person-days): %s" % ", ".join("%s %s" % kv for kv in data["scenarios"].items()))
        lines.append("  schedule: %s working days" % data["duration_days"])
        lines += ["  [%s] %s: %s" % (i["level"], i["where"], i["message"]) for i in data["issues"]]
        emit(args, pricing.jsonable(data), "\n".join(lines) + "\n  " + data["note"])
        return 0
    if action == "labour":
        items = bstore.labour_from_estimate(q, project_id)
        emit(args, items, "\n".join("  %s %s days at %s" % (i["id"], i["quantity"],
                                                            "rate card" if i["unit_cost"] is not None
                                                            else "PENDING (no rate)") for i in items))
        return 0
    if action == "price":
        result = bstore.price(q, project_id)
        totals = result["totals"]
        lines = ["INTERNAL - costs and margins (%s)" % totals["currency"]]
        for line in result["lines"]:
            if line["pending"]:
                lines.append("  %-14s PENDING" % line["id"])
            else:
                lines.append("  %-14s cost %12s  selling %12s  markup %6s  margin %6s%s" % (
                    line["id"], pricing.fmt(line["cost"]), pricing.fmt(line["selling"]),
                    pricing.pct(line["markup"]), pricing.pct(line["margin"]),
                    "  (estimate)" if line["estimate"] else ""))
        for key in ("COST", "CONTINGENCY", "SUBTOTAL", "DISCOUNT", "SELLING_PRICE", "TAX",
                    "TOTAL_BID_PRICE"):
            lines.append("  %-16s %14s" % (key, pricing.fmt(totals[key])))
        lines.append("  %-16s %14s" % ("MARKUP", pricing.pct(totals["MARKUP"])))
        lines.append("  %-16s %14s" % ("GROSS_MARGIN", pricing.pct(totals["GROSS_MARGIN"])))
        lines.append("  status: %s%s" % (result["status"], "; pending: %s" % ", ".join(result["pending"])
                                         if result["pending"] else ""))
        for issue in result["issues"]:
            if issue["level"] != "pending":
                lines.append("  [%s] %s: %s" % (issue["level"], issue["item"], issue["message"]))
        data = pricing.jsonable({"totals": totals, "status": result["status"],
                                 "pending": result["pending"], "estimates": result["estimates"],
                                 "issues": result["issues"]})
        if args.scenarios:
            rows = pricing.sensitivity(bstore.get(q, project_id)["document"].get("pricing") or {})
            data["scenarios"] = pricing.jsonable(rows)
            lines.append("  scenarios:")
            lines += ["    %-14s selling %14s  margin %6s  total %14s" % (
                row["scenario"], pricing.fmt(row["SELLING_PRICE"]), pricing.pct(row["GROSS_MARGIN"]),
                pricing.fmt(row["TOTAL_BID_PRICE"])) for row in rows]
        emit(args, data, "\n".join(lines))
        return 0
    if action == "approve":
        result = bstore.approve(q, project_id, args.gate, args.note, confirmed=args.yes)
        emit(args, result, ("Approved %s: %s" if result["approved"] else
                            "Would approve %s: %s\nConfirm with --yes.") % (args.gate, result["preview"]))
        return 0
    if action == "generate":
        result = bstore.generate(q, project_id, args.kind, final=args.final, pdf=not args.no_pdf,
                                 config=config)
        lines = ["Written to %s%s" % (result["folder"], " (FINAL)" if result["final"] else " (draft)")]
        for item in result["outputs"]:
            lines.append("  %-14s %-5s %s%s" % (item["kind"], item["format"], item["path"] or "-",
                                                "".join("\n      [%s] %s" % (p["severity"], p["message"])
                                                        for p in item["problems"])))
        emit(args, result, "\n".join(lines))
        return 1 if any(p["severity"] == "BLOCKER" for item in result["outputs"]
                        for p in item["problems"]) else 0
    if action == "outputs":
        rows = bstore.latest_outputs(q, project_id)
        if not args.text:
            emit(args, rows, "\n".join("  %-20s %-5s r%-3s %s%s" % (
                row["kind"], row["format"], row["revision"], row["path"],
                "".join("\n      [%s] %s" % (p["severity"], p["message"]) for p in row["problems"]))
                for row in rows) or "nothing generated yet (bid generate)")
            return 0
        kind, _, fmt_name = args.text.partition("/")
        matches = [row for row in rows if row["kind"] == kind and (not fmt_name or row["format"] == fmt_name)]
        if not matches:
            raise QueueError("no generated %s document (bid outputs %s)" % (args.text, project_id))
        row = sorted(matches, key=lambda item: {"docx": 0, "xlsx": 1, "pdf": 2}.get(item["format"], 3))[0]
        from . import bid_docs, ooxml
        if row["format"] == "docx":
            text = ooxml.docx_text(row["path"])
        elif row["format"] == "xlsx":
            text = ooxml.xlsx_text(row["path"])
        else:
            body, pages = bid_docs.pdf_text(row["path"])
            text = "(%s pages)\n%s" % (pages, body if body is not None else "(no PDF text reader)")
        print(text)
        return 0
    if action == "review":
        findings = bstore.review(q, project_id)
        emit(args, findings, _findings(findings, 80))
        return 1 if any(f["severity"] == "BLOCKER" for f in findings) else 0
    if action == "checklist":
        items = bstore.submission_checklist(q, project_id)
        emit(args, items, "\n".join("  [%s] %s%s" % ("x" if item["state"] == "done" else " ",
                                                     item["item"], " - " + item["detail"]
                                                     if item["detail"] else "") for item in items))
        return 0
    if action == "queue":
        result = bstore.queue_pipeline(q, project_id, _plugin_root())
        emit(args, result, "Queued %s; stages: %s. Run the queue to work through them."
             % (", ".join("#%d" % i for i in result["added"]) or "nothing new",
                ", ".join("%s #%d" % kv for kv in sorted(result["tasks"].items()))))
        return 0
    if action == "revisions":
        rows = bstore.revisions(q, project_id)
        emit(args, rows, "\n".join("  r%-4d %s  %s  %s" % (row["number"], row["at"][:19],
                                                          row["sha256"][:12], row["note"]) for row in rows))
        return 0
    if action == "status":
        data = bstore.status(q, project_id)
        emit(args, pricing.jsonable(data), status_text(data))
        return 0
    raise QueueError("unknown bid action %s" % action)


def _company(args, emit):
    act, company = args.company_action, args.company_id
    if act == "list":
        folder = bstore.companies_dir()
        rows = sorted(name for name in os.listdir(folder) if os.path.isdir(os.path.join(folder, name)))
        emit(args, rows, "\n".join("  " + row for row in rows) or "no company profiles")
        return 0
    if not company:
        raise QueueError("usage: %s bid company %s <company id>" % (NAME, act))
    if act == "new":
        profile = bstore.company_new(company, args.name or company)
        emit(args, profile, "Created company profile %s (stored in %s, never in a project or "
             "package)" % (company, os.path.join(bstore.companies_dir(), company)))
    elif act == "show":
        profile = bstore.company_profile(company)
        print(json.dumps(profile, indent=2, ensure_ascii=False))
    elif act == "apply":
        profile = bstore.company_apply(company, _read(args))
        emit(args, profile, "Company %s: %d items" % (company, len(profile.get("items") or [])))
    elif act == "ratecard" and args.json_text is None and not args.file:
        card = bstore.rate_card(company)
        if card is None:
            raise QueueError("company %s has no rate card" % company)
        print(json.dumps(card, indent=2, ensure_ascii=False))
    elif act == "ratecard":
        card = bstore.save_rate_card(company, _read(args))
        emit(args, {"roles": sorted((card.get("roles") or {}).keys())},
             "Rate card of %s saved (restricted: %d roles)" % (company, len(card.get("roles") or {})))
    return 0


def status_text(data):
    tender = data["tender"]
    lines = ["Bid %s - %s" % (data["project"], tender.get("title") or data["title"]),
             "  Request:      %s" % data["request"][:160],
             "  Mode:         %s, company %s, revision %d" % (data["mode"], data["company"] or "-",
                                                             data["revision"]),
             "  Tender:       %s; client %s; deadline %s" % (tender.get("reference") or "-",
                                                             tender.get("client") or "-",
                                                             tender.get("deadline") or "-"),
             "  Documents:    %s" % (", ".join("%s %s" % (d["id"], d["status"]) for d in data["documents"])
                                     or "none yet"),
             "  Requirements: %d (%d mandatory); compliance: %s" % (
                 data["requirements"], data["mandatory"],
                 ", ".join("%d %s" % (v, k) for k, v in sorted(data["compliance"].items())) or "-"),
             "  Disqualifying: %s" % (", ".join(item["requirement"] for item in data["disqualifying"])
                                      or "none"),
             "  Clarifications: %d open of %d" % (data["clarifications"]["open"],
                                                  data["clarifications"]["total"]),
             "  Recommendation: %s" % data["decision"]["decision"]]
    priced = data["pricing"] or {}
    if priced.get("status") == "error":
        lines.append("  Pricing:      error: %s" % priced["error"])
    else:
        lines.append("  Pricing:      %s; selling %s %s; total bid price %s%s" % (
            priced.get("status"), priced.get("currency"), priced.get("selling_price"),
            priced.get("total_bid_price"),
            "; pending: %s" % ", ".join(priced["pending"]) if priced.get("pending") else ""))
    lines.append("  Approvals:    %s" % ", ".join("%s %s" % (k, v) for k, v in data["approvals"].items()))
    lines.append("  Findings:     %s" % ", ".join("%d %s" % (v, k) for k, v in data["findings"].items()))
    if data["queue"]:
        lines.append("  Queue:        %s" % ", ".join("%d %s" % (v, k) for k, v in sorted(data["queue"].items())))
    for item in data["outputs"]:
        lines.append("  Output:       %s %s%s%s" % (item["kind"], item["format"],
                                                    " FINAL" if item["final"] else "",
                                                    " (%d problem(s))" % item["problems"]
                                                    if item["problems"] else ""))
    for doc in data["documents"]:
        if doc["limitation"]:
            lines.append("  Limitation:   %s: %s" % (doc["id"], doc["limitation"]))
    return "\n".join(lines)
