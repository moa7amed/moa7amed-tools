"""BidStudio documents: technical and commercial proposals, executive summary (DOCX, PDF),
pricing model, BOQ, compliance matrix and clarification register (XLSX).

Content is assembled as a list of blocks, then written as Word (ooxml.Document) and as
HTML that a browser prints to PDF. Every file is read back after it is written and
checked: required sections present, no price in a technical envelope, no internal cost,
margin or supplier figure in a customer-facing document, workbook totals equal to the
pricing engine's.
"""
from __future__ import annotations

import datetime
import html
import json
import os
import re
import subprocess
import sys

from . import bid_model as model
from . import bid_pricing as pricing
from . import ooxml, util

DEFAULT_TECHNICAL = (
    "Executive Summary", "Understanding of Requirements", "Company Profile", "Relevant Experience",
    "Proposed Solution", "Solution Architecture", "Functional Scope", "Technical Specifications",
    "Infrastructure and Hardware", "Security and Integration", "Implementation Methodology",
    "Project Governance", "Project Team", "Implementation Schedule", "Testing and Acceptance",
    "Training and Knowledge Transfer", "Warranty and Support", "Compliance Matrix",
    "Assumptions and Dependencies", "Risks and Mitigations", "Required Appendices")
LABELS = {
    "en": {"prepared_for": "Prepared for", "prepared_by": "Submitted by", "reference": "Tender reference",
           "date": "Date", "draft": "DRAFT - not approved for release", "technical": "Technical Proposal",
           "commercial": "Commercial Proposal", "summary": "Executive Summary",
           "compliance": "Compliance Matrix", "pricing_summary": "Pricing Summary", "boq": "Bill of Quantities",
           "no": "No.", "description": "Description", "qty": "Qty", "unit": "Unit",
           "unit_price": "Unit price", "total": "Total", "subtotal": "Subtotal", "discount": "Discount",
           "tax": "Tax", "grand_total": "Total bid price", "milestones": "Payment Milestones",
           "milestone": "Milestone", "share": "Share", "amount": "Amount",
           "assumptions": "Commercial Assumptions", "exclusions": "Exclusions",
           "validity": "Proposal Validity", "notes": "Contractual Notes", "cover": "Commercial Cover Letter",
           "req": "Requirement", "source": "Source", "status": "Status", "response": "Response",
           "section": "Proposal section", "pending": "pending confirmation",
           "risks": "Risks and Mitigations", "assumptions_t": "Assumptions and Dependencies"},
    "ar": {"prepared_for": u"مقدم إلى", "prepared_by": u"مقدم من", "reference": u"رقم المنافسة",
           "date": u"التاريخ", "draft": u"مسودة - غير معتمدة للإصدار", "technical": u"العرض الفني",
           "commercial": u"العرض المالي", "summary": u"الملخص التنفيذي", "compliance": u"مصفوفة الامتثال",
           "pricing_summary": u"ملخص الأسعار", "boq": u"جدول الكميات", "no": u"م",
           "description": u"الوصف", "qty": u"الكمية", "unit": u"الوحدة", "unit_price": u"سعر الوحدة",
           "total": u"الإجمالي", "subtotal": u"المجموع الفرعي", "discount": u"الخصم",
           "tax": u"ضريبة القيمة المضافة", "grand_total": u"إجمالي قيمة العرض",
           "milestones": u"دفعات السداد", "milestone": u"المرحلة", "share": u"النسبة",
           "amount": u"المبلغ", "assumptions": u"الافتراضات التجارية", "exclusions": u"الاستثناءات",
           "validity": u"صلاحية العرض", "notes": u"ملاحظات تعاقدية", "cover": u"خطاب العرض المالي",
           "req": u"المتطلب", "source": u"المصدر", "status": u"الحالة", "response": u"الاستجابة",
           "section": u"القسم في العرض", "pending": u"بانتظار التأكيد",
           "risks": u"المخاطر وخطط المعالجة", "assumptions_t": u"الافتراضات والاعتماديات"},
}
MONEY_PATTERN = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+\.\d{2}(?!\d)|(?<![\d.,])\d{3,}\.\d{2}(?![\d])")
CURRENCY_WORDS = re.compile(r"\b(SAR|USD|EUR|AED|EGP|GBP|QAR|KWD|OMR|BHD|JOD)\s?\d|(?:\$|€|£)\s?\d",
                            re.I)


def labels(language):
    return LABELS.get(language if language in LABELS else "en")


# --------------------------------------------------------------------------- blocks

def _blocks_from_content(content):
    """Section content as written by the drafting step: a string (paragraphs separated by
    blank lines, '- ' bullets) or a list of blocks {"p": ..} {"bullets": [..]}
    {"table": {"header": [..], "rows": [[..]]}} {"h": .., "level": 2}."""
    if isinstance(content, str):
        out, bullets = [], []
        for part in re.split(r"\n\s*\n", content.strip()):
            lines = part.strip().splitlines()
            if lines and all(line.strip().startswith(("- ", "* ")) for line in lines):
                out.append(("bullets", [line.strip()[2:] for line in lines]))
            elif part.strip():
                out.append(("para", part.strip()))
        return out
    out = []
    for item in content or []:
        if isinstance(item, str):
            out.append(("para", item))
        elif "p" in item:
            out.append(("para", item["p"]))
        elif "bullets" in item:
            out.append(("bullets", item["bullets"]))
        elif "table" in item:
            out.append(("table", item["table"].get("header") or [], item["table"].get("rows") or []))
        elif "h" in item:
            out.append(("heading", item.get("level", 2), item["h"]))
    return out


def ordered_sections(technical):
    """Sections in the tender's mandated order (required_sections) when it gives one,
    otherwise in the usual order; an explicit "order" wins; others keep their place after."""
    sections = list(technical.get("sections") or [])
    mandated = [title.lower() for title in technical.get("required_sections") or []]
    usual = [title.lower() for title in DEFAULT_TECHNICAL]

    def rank(pair):
        position, section = pair
        title = str(section.get("title") or "").lower()
        if section.get("order") is not None:
            return (0, float(section["order"]), position)
        for index, name in enumerate(mandated):
            if name and (name == title or name in title):
                return (1, index, position)
        for index, name in enumerate(usual):
            if name == title or name in title:
                return (2, index, position)
        return (3, 0, position)
    return [section for _, section in sorted(enumerate(sections), key=rank)]


def technical_blocks(project, document, profile):
    """The technical envelope. It is built without the pricing model: no figure from the
    commercial side can reach it."""
    tender = document.get("tender") or {}
    language = document.get("language") or "en"
    text = labels(language)
    technical = document.get("technical") or {}
    sections = ordered_sections(technical)
    company = (profile or {}).get("legal_name") or "-"
    blocks = [("title", tender.get("title") or project["title"], [
        text["technical"], "%s: %s" % (text["prepared_for"], tender.get("client") or "-"),
        "%s: %s" % (text["prepared_by"], company),
        "%s: %s" % (text["reference"], tender.get("reference") or "-"),
        "%s: %s" % (text["date"], technical.get("date") or datetime.date.today().isoformat())])]
    for number, section in enumerate(sections, 1):
        blocks.append(("heading", 1, "%d. %s" % (number, section.get("title"))))
        if section.get("auto") == "compliance":
            blocks += compliance_blocks(document, language)
        elif section.get("auto") == "risks":
            rows = [[risk.get("id") or "", risk.get("risk") or risk.get("text") or "",
                     risk.get("impact") or "", risk.get("mitigation") or ""]
                    for risk in document.get("risks") or []]
            blocks.append(("table", ["ID", "Risk", "Impact", "Mitigation"], rows))
        elif section.get("auto") == "assumptions":
            items = [item.get("text") if isinstance(item, dict) else str(item)
                     for item in technical.get("assumptions") or []]
            blocks.append(("bullets", items or ["-"]))
        else:
            blocks += _blocks_from_content(section.get("content"))
    return blocks


def compliance_blocks(document, language="en"):
    text = labels(language)
    matrix = model.by_id(document.get("compliance"))
    rows = []
    for req in document.get("requirements") or []:
        if req.get("superseded_by") or req.get("class", "EXPLICIT_REQUIREMENT") != "EXPLICIT_REQUIREMENT":
            continue
        row = matrix.get(req["id"]) or {}
        source = req.get("source") or {}
        where = ", ".join(str(source[key]) for key in ("doc", "section", "page", "table")
                          if source.get(key))
        rows.append([req["id"], where, req.get("text") or "",
                     "M" if req.get("mandatory") else "O",
                     (row.get("status") or "PENDING_VERIFICATION").replace("_", " "),
                     row.get("response") or "", row.get("section") or ""])
    return [("table", [text["req"], text["source"], text["description"], "M/O", text["status"],
                       text["response"], text["section"]], rows)]


def commercial_blocks(project, document, profile, result, final):
    """The customer-facing commercial proposal. It is built from customer lines only:
    descriptions, quantities, units and selling prices."""
    tender = document.get("tender") or {}
    language = document.get("language") or "en"
    text = labels(language)
    commercial = document.get("commercial") or {}
    currency = result["totals"]["currency"]
    company = (profile or {}).get("legal_name") or "-"
    blocks = [("title", tender.get("title") or project["title"], [
        text["commercial"], "%s: %s" % (text["prepared_for"], tender.get("client") or "-"),
        "%s: %s" % (text["prepared_by"], company),
        "%s: %s" % (text["reference"], tender.get("reference") or "-"),
        "%s: %s" % (text["date"], commercial.get("date") or datetime.date.today().isoformat())])]
    if commercial.get("cover_letter"):
        blocks.append(("heading", 1, text["cover"]))
        blocks += _blocks_from_content(commercial["cover_letter"])
    totals = result["totals"]
    blocks.append(("heading", 1, text["pricing_summary"]))
    summary = [[text["subtotal"], "%s %s" % (currency, pricing.fmt(totals["SUBTOTAL"]))]]
    if totals["DISCOUNT"]:
        summary.append([text["discount"], "- %s %s" % (currency, pricing.fmt(totals["DISCOUNT"]))])
    summary.append([text["tax"], "%s %s" % (currency, pricing.fmt(totals["TAX"]))])
    summary.append([text["grand_total"], "%s %s" % (currency, pricing.fmt(totals["TOTAL_BID_PRICE"]))])
    blocks.append(("table", [text["description"], text["amount"]], summary))
    blocks.append(("heading", 1, text["boq"]))
    rows = []
    for number, line in enumerate(pricing.customer_lines(result), 1):
        rows.append([str(number), line["description"] or "", str(line["quantity"]), line["unit"] or "",
                     pricing.fmt(line["unit_price"]) if line["unit_price"] is not None else text["pending"],
                     pricing.fmt(line["total"]) if line["total"] is not None else text["pending"]])
    blocks.append(("table", [text["no"], text["description"], text["qty"], text["unit"],
                             "%s (%s)" % (text["unit_price"], currency),
                             "%s (%s)" % (text["total"], currency)], rows))
    milestones = commercial.get("payment_milestones") or []
    if milestones:
        blocks.append(("heading", 1, text["milestones"]))
        blocks.append(("table", [text["milestone"], text["share"], text["amount"]],
                       [[item.get("name"), "%s%%" % item.get("percent"),
                         "%s %s" % (currency, pricing.fmt(item["amount"]))] for item in
                        payment_schedule(milestones, totals["TOTAL_BID_PRICE"])]))
    for key, label in (("assumptions", text["assumptions"]), ("exclusions", text["exclusions"]),
                       ("validity", text["validity"]), ("notes", text["notes"])):
        value = commercial.get(key)
        if value:
            blocks.append(("heading", 1, label))
            blocks += _blocks_from_content(value) if not isinstance(value, list) or (
                value and isinstance(value[0], dict)) else [("bullets", value)]
    if result["estimates"] or result["assumed_fx"]:
        blocks.append(("para", "Prices marked as planning estimates or based on assumed exchange "
                       "rates are subject to confirmation." if language != "ar" else
                       u"الأسعار المبنية على تقديرات أو أسعار صرف مفترضة خاضعة للتأكيد."))
    del final
    return blocks


def payment_schedule(milestones, total):
    """Amounts of the payment milestones; the last one takes the rounding remainder so the
    schedule adds up to the total exactly."""
    from decimal import Decimal
    shares = [Decimal(str(item.get("percent", 0))) for item in milestones]
    if sum(shares) != Decimal(100):
        raise pricing.PricingError("payment milestones add up to %s%%, not 100%%" % sum(shares))
    out, allocated = [], Decimal(0)
    for index, item in enumerate(milestones):
        if index == len(milestones) - 1:
            amount = total - allocated
        else:
            amount = pricing.money(total * shares[index] / 100)
            allocated += amount
        out.append({"name": item.get("name"), "percent": item.get("percent"), "amount": amount})
    return out


def summary_blocks(project, document, decision, result):
    tender = document.get("tender") or {}
    language = document.get("language") or "en"
    text = labels(language)
    blocks = [("title", tender.get("title") or project["title"], [text["summary"],
              "%s: %s" % (text["reference"], tender.get("reference") or "-")])]
    blocks.append(("heading", 1, "Tender"))
    blocks.append(("table", ["Item", "Value"], [[key.replace("_", " ").title(), str(value)]
                                                for key, value in sorted(tender.items())
                                                if not isinstance(value, (dict, list))]))
    blocks.append(("heading", 1, "Recommendation"))
    blocks.append(("para", "%s (%s)" % (decision["decision"], decision["note"])))
    if decision["reasons"]:
        blocks.append(("bullets", decision["reasons"]))
    if decision["disqualifying"]:
        blocks.append(("heading", 2, "Disqualifying conditions to resolve"))
        blocks.append(("bullets", ["%s (%s): %s" % (item["requirement"], item["status"],
                                                   item["text"]) for item in decision["disqualifying"]]))
    del result
    return blocks


# --------------------------------------------------------------------------- writers

def write_docx(blocks, path, title, header, rtl=False):
    document = ooxml.Document(title=title, header=header, rtl=rtl)
    for block in blocks:
        kind = block[0]
        if kind == "title":
            document.title_page(block[1], block[2])
        elif kind == "heading":
            document.heading(block[2], block[1])
        elif kind == "para":
            document.paragraph(block[1])
        elif kind == "bullets":
            document.bullets(block[1])
        elif kind == "table":
            widths = None
            columns = len(block[1])
            if columns:
                widths = [int(9500 / columns)] * columns
            document.table(block[1], block[2], widths)
        elif kind == "pagebreak":
            document.page_break()
    return document.save(path)


def write_html(blocks, path, title, header, rtl=False):
    direction = "rtl" if rtl else "ltr"
    parts = ["<!doctype html><html lang='%s' dir='%s'><head><meta charset='utf-8'><title>%s</title>"
             "<style>@page{size:A4;margin:22mm 18mm}body{font-family:Calibri,Arial,'Segoe UI',"
             "Tahoma,sans-serif;font-size:10.5pt;color:#111;line-height:1.4}h1{font-size:15pt;"
             "color:#1F3864;margin:18pt 0 6pt;page-break-after:avoid}h2{font-size:12.5pt;color:#2F5496}"
             ".title{text-align:center;margin-top:120pt;page-break-after:always}.title h1{font-size:24pt}"
             ".title p{font-size:12.5pt;margin:4pt}table{border-collapse:collapse;width:100%%;"
             "font-size:9pt;margin:6pt 0 10pt}th,td{border:1px solid #8EAADB;padding:3pt 4pt;"
             "vertical-align:top;text-align:start}th{background:#D9E2F3}tr{page-break-inside:avoid}"
             ".header{font-size:8pt;color:#595959;text-align:end;border-bottom:1px solid #ccc;"
             "margin-bottom:8pt}</style></head><body>"
             % ("ar" if rtl else "en", direction, html.escape(title))]
    parts.append("<div class='header'>%s</div>" % html.escape(header or title))
    for block in blocks:
        kind = block[0]
        if kind == "title":
            parts.append("<div class='title'><h1>%s</h1>%s</div>" % (
                html.escape(block[1]), "".join("<p>%s</p>" % html.escape(line) for line in block[2])))
        elif kind == "heading":
            parts.append("<h%d>%s</h%d>" % (min(block[1], 3), html.escape(block[2]), min(block[1], 3)))
        elif kind == "para":
            parts.append("<p dir='auto'>%s</p>" % html.escape(block[1]).replace("\n", "<br>"))
        elif kind == "bullets":
            parts.append("<ul>%s</ul>" % "".join("<li dir='auto'>%s</li>" % html.escape(str(item))
                                                 for item in block[1]))
        elif kind == "table":
            parts.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % html.escape(str(cell)) for cell in block[1]),
                "".join("<tr>%s</tr>" % "".join("<td dir='auto'>%s</td>" % html.escape(
                    "" if cell is None else str(cell)) for cell in row) for row in block[2])))
        elif kind == "pagebreak":
            parts.append("<div style='page-break-after:always'></div>")
    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(parts))
    return path


def pdf_tools(config=None):
    """How PDFs can be made here: the browser (Playwright) or Microsoft Word."""
    from . import replica_store
    found = replica_store.tools(config or {})
    word = None
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            for version in ("root\\Office16", "Office16", "root\\Office15"):
                path = os.path.join(root or "", "Microsoft Office", version, "WINWORD.EXE")
                if root and os.path.isfile(path):
                    word = path
    return {"browser": bool(found.get("playwright")), "python": found.get("python"), "word": word}


def write_pdf(html_path, pdf_path, config=None):
    """Print the HTML twin to PDF with the browser. Returns (ok, method or error)."""
    tools = pdf_tools(config)
    if not tools["browser"]:
        return False, "no PDF engine: install Playwright for %s (the DOCX is complete and can be " \
                      "saved as PDF from Word)" % tools["python"]
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "replica_browser.py")
    request = {"pdf": {"html": html_path, "out": pdf_path}}
    try:
        result = subprocess.run([tools["python"], "-B", script, json.dumps(request)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                                **util.no_window_kwargs())
        data = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return False, "the browser could not print the PDF: %s" % str(exc)[:200]
    if not data.get("ok") or not os.path.isfile(pdf_path):
        return False, data.get("error") or "the browser produced no PDF"
    return True, "browser print (%s)" % data.get("browser")


def pdf_text(path):
    """Text of a PDF, with the reader that is installed (for validation)."""
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)
    except Exception:  # noqa: BLE001
        pass
    try:
        import fitz
        with fitz.open(path) as pdf:
            return "\n".join(page.get_text() for page in pdf), len(pdf)
    except Exception:  # noqa: BLE001
        pass
    with open(path, "rb") as handle:
        data = handle.read()
    return None, len(re.findall(rb"/Type\s*/Page[^s]", data))


# --------------------------------------------------------------------------- workbooks

def pricing_workbook(document, result, path):
    """The internal pricing model: assumptions, every line with formulas that reproduce the
    engine's figures, the summary and the sensitivity table. RESTRICTED: not for customers."""
    F = ooxml.Formula
    model_ = document.get("pricing") or {}
    currency = result["totals"]["currency"]
    book = ooxml.Workbook()
    assumptions = [["Assumption", "Value", "Status / source"],
                   ["Base currency", currency, ""],
                   ["Contingency (share of cost)", float(pricing.D(model_.get("contingency", 0)) or 0), ""],
                   ["Discount (share of subtotal)", float(pricing.D(model_.get("discount", 0)) or 0), ""],
                   ["Tax rate", float(pricing.D(model_.get("tax_rate", 0)) or 0),
                    model_.get("tax_note") or ""]]
    for code, entry in sorted((model_.get("fx") or {}).items()):
        assumptions.append(["FX %s -> %s" % (code, currency), float(pricing.D(entry.get("rate"))),
                            "%s %s" % (entry.get("status") or "ASSUMPTION", entry.get("source") or "")])
    assumptions.append(["RESTRICTED", "Internal costs and margins. Never send this workbook to a "
                                      "customer.", ""])
    book.sheet("Assumptions", assumptions, widths=[34, 18, 40],
               styles={(2, 1): 3, (3, 1): 3, (4, 1): 3})
    header = ["ID", "Category", "Description", "Qty", "Unit", "Unit cost", "Currency", "FX",
              "Cost", "Rule", "Rate", "Selling before contingency", "Contingency cost",
              "Contingency selling", "Unit price", "Selling", "Markup", "Gross margin", "Taxable",
              "Source", "Reference"]
    rows = [header]
    priced = [line for line in result["lines"] if not line["pending"]]
    first, last = 2, 1 + len(priced)
    cost_range = "I%d:I%d" % (first, max(first, last))
    for index, line in enumerate(priced):
        r = index + 2
        item = line["item"]
        rule = "markup" if "markup" in line["rule"] else "margin"
        rate = float(pricing.D(line["rule"][rule]))
        fx = float(line["fx"])
        cost_raw = float(line["selling_before_contingency"]) if False else None
        rows.append([
            item.get("id"), item.get("category"), item.get("description"),
            float(pricing.D(item.get("quantity", 1))), item.get("unit") or "",
            float(line["unit_cost"]), item.get("currency") or currency, fx,
            F("D%d*F%d*H%d" % (r, r, r), float(line["cost"])),
            rule, rate,
            F('IF(J%d="markup",I%d*(1+K%d),I%d/(1-K%d))' % (r, r, r, r, r),
              float(line["selling_before_contingency"])),
            F("IF(SUM(%s)=0,0,I%d/SUM(%s)*Summary!B3)" % (cost_range, r, cost_range),
              float(line["contingency_cost"])),
            F('IF(J%d="markup",M%d*(1+K%d),M%d/(1-K%d))' % (r, r, r, r, r),
              float(line["contingency_selling"])),
            F("ROUND((L%d+N%d)/D%d,2)" % (r, r, r), float(line["unit_price"])),
            F("ROUND(O%d*D%d,2)" % (r, r), float(line["selling"])),
            F("IF(ROUND(I%d,2)+ROUND(M%d,2)=0,0,P%d/(ROUND(I%d,2)+ROUND(M%d,2))-1)" % (r, r, r, r, r),
              float(line["markup"]) if line["markup"] is not None else 0),
            F("IF(P%d=0,0,(P%d-(ROUND(I%d,2)+ROUND(M%d,2)))/P%d)" % (r, r, r, r, r),
              float(line["margin"]) if line["margin"] is not None else 0),
            "yes" if item.get("taxable", True) else "no",
            line["source"] + (" (PLANNING ESTIMATE)" if line["source"] == "estimate" else ""),
            (item.get("source") or {}).get("ref") or ""])
        del cost_raw
    for line in result["lines"]:
        if line["pending"]:
            item = line["item"]
            rows.append([item.get("id"), item.get("category"), item.get("description"),
                         float(pricing.D(item.get("quantity", 1))), item.get("unit") or "", None,
                         item.get("currency") or currency, None, None, None, None, None, None, None,
                         None, None, None, None, "", "PENDING", (item.get("source") or {}).get("ref") or ""])
    styles = {}
    for r in range(1, len(rows)):
        for c in (5, 8, 11, 12, 13, 14, 15):
            styles[(r, c)] = 2
        for c in (10, 16, 17):
            styles[(r, c)] = 3
    book.sheet("Lines", rows, widths=[10, 13, 36, 8, 10, 12, 9, 8, 13, 9, 8, 14, 12, 12, 12, 14,
                                      9, 9, 8, 22, 22], styles=styles)
    totals = result["totals"]
    end = max(first, last)
    summary = [
        ["Figure", "Value", "Meaning"],
        ["COST", F("ROUND(SUM(Lines!I%d:I%d),2)" % (first, end), float(totals["COST"])),
         "what the bidder pays"],
        ["CONTINGENCY", F("ROUND(SUM(Lines!I%d:I%d)*Assumptions!B3,2)" % (first, end),
                          float(totals["CONTINGENCY"])), "risk provision, built into the prices"],
        ["SUBTOTAL", F("SUM(Lines!P%d:P%d)" % (first, end), float(totals["SUBTOTAL"])),
         "sum of line selling prices"],
        ["DISCOUNT", F("ROUND(B4*Assumptions!B4,2)", float(totals["DISCOUNT"])), ""],
        ["SELLING_PRICE", F("B4-B5", float(totals["SELLING_PRICE"])), "before tax"],
        ["TAX", F('ROUND((SUMIF(Lines!S%d:S%d,"yes",Lines!P%d:P%d)-ROUND(SUMIF(Lines!S%d:S%d,"yes",'
                  'Lines!P%d:P%d)*Assumptions!B4,2))*Assumptions!B5,2)'
                  % (first, end, first, end, first, end, first, end), float(totals["TAX"])),
         "on the taxable selling price after discount"],
        ["TOTAL_BID_PRICE", F("B6+B7", float(totals["TOTAL_BID_PRICE"])), "selling price + tax"],
        ["MARKUP", F("IF(B2+B3=0,0,(B6-(B2+B3))/(B2+B3))", float(totals["MARKUP"] or 0)),
         "(selling - cost) / cost"],
        ["GROSS_MARGIN", F("IF(B6=0,0,(B6-(B2+B3))/B6)", float(totals["GROSS_MARGIN"] or 0)),
         "(selling - cost) / selling"],
        ["Status", result["status"], "complete / estimated / incomplete"],
        ["Pending lines", ", ".join(result["pending"]) or "-", ""],
        ["Planning estimates", ", ".join(result["estimates"]) or "-", ""],
    ]
    book.sheet("Summary", summary, widths=[22, 18, 44],
               styles={(r, 1): 4 if r < 8 else 3 for r in range(1, 10)})
    scenarios = [["Scenario", "Cost", "Selling price", "Gross margin", "Total bid price"]]
    for row in pricing.sensitivity(document.get("pricing") or {}):
        scenarios.append([row["scenario"], float(row["COST"]), float(row["SELLING_PRICE"]),
                          float(row["GROSS_MARGIN"] or 0), float(row["TOTAL_BID_PRICE"])])
    book.sheet("Scenarios", scenarios, widths=[18, 16, 16, 14, 18],
               styles=dict([((r, c), 2) for r in range(1, len(scenarios)) for c in (1, 2, 4)]
                           + [((r, 3), 3) for r in range(1, len(scenarios))]))
    return book.save(path)


def boq_workbook(document, result, path):
    """The customer BOQ: description, quantity, unit, unit price, total (= qty x unit price,
    as a formula) and the totals. Nothing internal."""
    F = ooxml.Formula
    currency = result["totals"]["currency"]
    rtl = (document.get("language") == "ar")
    text = labels(document.get("language") or "en")
    rows = [[text["no"], text["description"], text["qty"], text["unit"],
             "%s (%s)" % (text["unit_price"], currency), "%s (%s)" % (text["total"], currency)]]
    for number, line in enumerate(pricing.customer_lines(result), 1):
        r = number + 1
        quantity = float(pricing.D(line["quantity"]))
        if line["unit_price"] is None:
            rows.append([number, line["description"], quantity, line["unit"], text["pending"], None])
        else:
            rows.append([number, line["description"], quantity, line["unit"], float(line["unit_price"]),
                         F("ROUND(C%d*E%d,2)" % (r, r), float(line["total"]))])
    end = len(rows)
    totals = result["totals"]
    rows.append([])
    rows.append(["", text["subtotal"], "", "", "", F("SUM(F2:F%d)" % end, float(totals["SUBTOTAL"]))])
    rows.append(["", text["discount"], "", "", "", float(-totals["DISCOUNT"])])
    rows.append(["", text["tax"], "", "", "", float(totals["TAX"])])
    rows.append(["", text["grand_total"], "", "", "",
                 F("F%d+F%d+F%d" % (end + 2, end + 3, end + 4), float(totals["TOTAL_BID_PRICE"]))])
    styles = {(r, c): 2 for r in range(1, len(rows)) for c in (4, 5)}
    styles.update({(r, 5): 4 for r in range(end + 1, len(rows))})
    book = ooxml.Workbook()
    book.sheet(text["boq"][:31], rows, widths=[6, 52, 8, 12, 16, 18], styles=styles, rtl=rtl)
    return book.save(path)


def compliance_workbook(document, path, internal=False):
    """The compliance matrix. The customer version (technical envelope) has the current
    requirements with status, response and proposal section; the internal version adds
    evidence references, owner, risk, clarifications, superseded requirements and the
    disqualifying conditions."""
    matrix = model.by_id(document.get("compliance"))
    if not internal:
        language = document.get("language") or "en"
        text = labels(language)
        rows = [[text["req"], text["source"], text["description"], "M/O", text["status"],
                 text["response"], text["section"]]]
        for req in document.get("requirements") or []:
            if req.get("superseded_by") or req.get("class", "EXPLICIT_REQUIREMENT") != "EXPLICIT_REQUIREMENT":
                continue
            row = matrix.get(req["id"]) or {}
            source = req.get("source") or {}
            rows.append([req["id"], ", ".join(str(source[key]) for key in ("section", "page", "table")
                                              if source.get(key)),
                         req.get("text"), "M" if req.get("mandatory") else "O",
                         (row.get("status") or "PENDING_VERIFICATION").replace("_", " "),
                         row.get("response") or "", row.get("section") or ""])
        book = ooxml.Workbook()
        book.sheet(text["compliance"][:31], rows, widths=[13, 16, 50, 6, 20, 60, 22],
                   styles={(r, c): 5 for r in range(1, len(rows)) for c in (2, 5)},
                   rtl=(language == "ar"))
        return book.save(path)
    rows = [["Requirement ID", "RFP section/page", "Requirement", "Mandatory", "Category",
             "Compliance status", "Proposed response", "Evidence", "Proposal section", "Owner",
             "Risk", "Clarification", "Superseded by"]]
    for req in document.get("requirements") or []:
        row = matrix.get(req["id"]) or {}
        source = req.get("source") or {}
        rows.append([req["id"], ", ".join(str(source[key]) for key in ("doc", "section", "page", "table",
                                                                     "sheet") if source.get(key)),
                     req.get("text"), "yes" if req.get("mandatory") else "no", req.get("category"),
                     row.get("status") or ("superseded" if req.get("superseded_by") else "unanswered"),
                     row.get("response") or "", ", ".join(row.get("evidence") or []),
                     row.get("section") or "", row.get("owner") or "", row.get("risk") or "",
                     row.get("clarification") or "", req.get("superseded_by") or ""])
    disq = [["Requirement", "Category", "Status", "Text"]]
    for item in model.disqualifying(document):
        disq.append([item["requirement"], item["category"], item["status"], item["text"]])
    book = ooxml.Workbook()
    styles = {(r, c): 5 for r in range(1, len(rows)) for c in (2, 6)}
    book.sheet("Compliance", rows, widths=[13, 18, 50, 10, 14, 20, 50, 20, 18, 12, 12, 14, 12],
               styles=styles)
    book.sheet("Disqualifying", disq, widths=[14, 14, 20, 70])
    return book.save(path)


def clarification_workbook(document, path):
    rows = [["Question ID", "Related requirement", "Question", "Reason", "Technical impact",
             "Pricing impact", "Priority", "Status", "Client response", "Updated assumption"]]
    for item in document.get("clarifications") or []:
        rows.append([item.get("id"), ", ".join(item.get("requirements") or []), item.get("question"),
                     item.get("reason"), item.get("technical_impact"), item.get("pricing_impact"),
                     item.get("priority"), item.get("status", "open"), item.get("response") or "",
                     item.get("updated_assumption") or ""])
    book = ooxml.Workbook()
    book.sheet("Clarifications", rows, widths=[11, 16, 50, 34, 24, 24, 10, 10, 34, 34],
               styles={(r, c): 5 for r in range(1, len(rows)) for c in (2, 3)})
    return book.save(path)


# --------------------------------------------------------------------------- checks

def find_money(text):
    return set(MONEY_PATTERN.findall(text or ""))


def check_technical(text, result):
    """A technical envelope must carry no price."""
    problems = []
    if result is not None:
        figures = {pricing.fmt(result["totals"][key]) for key in
                   ("SELLING_PRICE", "TOTAL_BID_PRICE", "SUBTOTAL", "TAX", "COST")}
        for line in result["lines"]:
            if not line["pending"]:
                figures.update({pricing.fmt(line["selling"]), pricing.fmt(line["unit_price"])})
        leaked = sorted(value for value in figures if value != "0.00" and value in (text or ""))
        if leaked:
            problems.append({"severity": "BLOCKER", "code": "price-in-technical",
                             "message": "the technical envelope contains prices: %s" % ", ".join(leaked[:5])})
    if CURRENCY_WORDS.search(text or ""):
        problems.append({"severity": "HIGH", "code": "currency-in-technical",
                         "message": "the technical envelope mentions a currency amount (%s)"
                         % CURRENCY_WORDS.search(text).group(0)})
    return problems


def check_customer(text, result, kind):
    """Customer-facing documents must not show internal figures or words."""
    problems = []
    leaked = sorted(value for value in pricing.internal_values(result) if value in (text or ""))
    if leaked:
        problems.append({"severity": "BLOCKER", "code": "internal-figure",
                         "message": "%s shows internal cost figures: %s" % (kind, ", ".join(leaked[:5]))})
    for word in ("markup", "gross margin", "unit cost", "supplier price", "contingency", "RESTRICTED"):
        if re.search(r"\b%s\b" % re.escape(word), text or "", re.I):
            problems.append({"severity": "BLOCKER", "code": "internal-term",
                             "message": "%s contains the internal term '%s'" % (kind, word)})
    return problems


def check_sections(blocks, document):
    """Sections the RFP requires that the technical proposal does not have."""
    required = [item for item in (document.get("technical") or {}).get("required_sections") or []]
    titles = " | ".join(block[2].lower() for block in blocks if block[0] == "heading")
    missing = [item for item in required if item.lower() not in titles]
    return [{"severity": "HIGH", "code": "missing-section",
             "message": "required section missing: %s" % item} for item in missing]
