"""BidStudio: tender intake, requirements, compliance, clarifications, bid/no-bid,
estimation, pricing, documents, review, approvals and queue integration.

Fixtures in tests/fixtures/bid are synthetic (invented organisations, references and
prices). Document checks read the generated files back; PDF checks run when a browser
is available for printing."""
import json
import os
import re
import subprocess
import sys
import unittest
import zipfile
from decimal import Decimal

from helpers import MQ, QueueTestCase

from mqlib import bid_docs, bid_model, bid_pricing, ooxml
from mqlib import bid_store as bstore
from mqlib.util import QueueError

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "bid")
REQUEST = "Draft our technical and commercial offer for this tender."

REQUIREMENTS = [
    {"id": "REQ-001", "category": "submission", "mandatory": True,
     "text": "The technical proposal and the commercial proposal shall be submitted in separate "
             "sealed envelopes.", "source": {"doc": "D1", "section": "1.2"}},
    {"id": "REQ-002", "category": "certification", "mandatory": True,
     "text": "The bidder must hold a valid ISO/IEC 27001 certificate.",
     "source": {"doc": "D1", "section": "2.2"}},
    {"id": "REQ-003", "category": "functional", "mandatory": True,
     "text": "The system shall print visitor badges with a QR code.",
     "source": {"doc": "D1", "section": "3", "table": "F-02"}},
    {"id": "REQ-004", "category": "delivery", "mandatory": True,
     "text": "The system shall be delivered and accepted within 120 calendar days of contract signature.",
     "source": {"doc": "D1", "section": "6.1"}},
    {"id": "REQ-005", "category": "non_functional", "mandatory": True,
     "text": "The system shall support 300 named users and 50 concurrent users.",
     "source": {"doc": "D1", "section": "4.1"}},
]
PROFILE_ITEMS = [
    {"id": "C-CR", "type": "registration", "text": "Commercial registration 1010000000",
     "evidence": "CR certificate (copy on file)", "approved": True, "valid_until": "2027-06-30"},
    {"id": "C-REF-VMS", "type": "reference", "text": "Visitor system for Harbour Clinic, 2024",
     "evidence": "completion certificate", "approved": True},
]
RATE_CARD = {"roles": {"developer": {"daily_cost": "1200", "currency": "SAR"},
                       "project manager": {"daily_cost": "1600", "currency": "SAR"}}}
WBS = {"packages": [
    {"id": "WP1", "name": "Analysis", "roles": [{"role": "project manager", "optimistic": 4,
                                                 "likely": 5, "pessimistic": 9}],
     "basis": "heuristic", "deliverable": "requirements", "team": 1},
    {"id": "WP2", "name": "Build", "depends_on": ["WP1"], "basis": "historical", "team": 2,
     "roles": [{"role": "developer", "optimistic": 30, "likely": 40, "pessimistic": 62}]}]}


def pricing_inputs(**extra):
    data = {"currency": "SAR", "tax_rate": "0.15", "contingency": "0.05", "discount": "0",
            "fx": {"USD": {"rate": "3.75", "status": "ASSUMPTION", "source": "planning rate"}},
            "pricing_rules": {"default": {"markup": "0.20"}, "hardware": {"margin": "0.12"}},
            "items": [
                {"id": "HW-01", "category": "hardware", "description": "Badge printer Q-7781",
                 "customer_description": "Visitor badge printer", "quantity": 4, "unit": "each",
                 "unit_cost": "610", "currency": "USD", "source": {"type": "quotation", "ref": "D4"}},
                {"id": "SRV-01", "category": "implementation", "description": "Implementation services",
                 "quantity": 1, "unit": "lot", "unit_cost": "48000", "source": {"type": "estimate",
                                                                               "ref": "WBS"}},
                {"id": "TRN-01", "category": "training", "description": "Training of 50 users",
                 "quantity": 3, "unit": "day", "unit_cost": "2500", "taxable": False,
                 "source": {"type": "rate_card", "ref": "trainer"}}]}
    data.update(extra)
    return data


class BidCase(QueueTestCase):

    def company(self, company_id="acme"):
        bstore.company_new(company_id, "Acme Systems (fictitious)")
        bstore.company_apply(company_id, {"items": PROFILE_ITEMS})
        bstore.save_rate_card(company_id, RATE_CARD)
        return company_id

    def bid(self, project_id="b1", mode="complete", company=True, docs=("rfp-visitor-management.docx",)):
        q = self.queue()
        bstore.create(q, project_id, REQUEST, mode=mode, company=self.company() if company is True
                      else company)
        for name in docs:
            bstore.add_document(q, project_id, os.path.join(FIX, name))
        return q

    def analysed(self, **kwargs):
        q = self.bid(**kwargs)
        result = bstore.apply(q, "b1", {"requirements": REQUIREMENTS,
                                        "tender": {"title": "Visitor Management System",
                                                   "reference": "RFP-2026-014",
                                                   "client": "Northbridge Municipal Authority"},
                                        "deadline": "2026-12-15", "delivery_days": 120})
        return q, result

    def codes(self, findings, severity=None):
        return {f["code"] for f in findings if severity is None or f["severity"] == severity}


# --------------------------------------------------------------------------- intent

class Intent(unittest.TestCase):

    def test_modes_from_natural_language(self):
        for text, mode in (("Analyze this tender.", "analyze"),
                           ("Prepare the technical proposal.", "technical"),
                           ("Prepare the commercial proposal.", "commercial"),
                           ("Check our price quotation against the RFP before we submit.", "review"),
                           ("Revise the proposal to reflect this clarification.", "update"),
                           ("Produce the complete bid package.", "complete"),
                           (REQUEST, "complete")):
            self.assertEqual(bid_model.intent(text)["mode"], mode, text)

    def test_arabic_and_not_tender_work(self):
        self.assertEqual(bid_model.intent(u"جهز العرض الفني والمالي لهذه المناقصة")["mode"], "complete")
        self.assertEqual(bid_model.intent(u"حلل كراسة الشروط دي")["mode"], "analyze")
        for text in ("What is an RFP?", "Prepare dinner for tonight", "Fix the login bug",
                     u"ما هي المناقصة؟"):
            self.assertIsNone(bid_model.intent(text)["intent"], text)


# --------------------------------------------------------------------------- intake (1, 3, 6, 22)

class Intake(BidCase):

    def test_documents_are_read_with_provenance(self):
        q = self.bid(docs=("rfp-visitor-management.docx", "tender-hardware-refresh.pdf",
                           "quotation-hardware.xlsx", "rfp-licensing-support.txt"))
        docs = {d["id"]: d for d in bstore.documents(q, "b1")}
        self.assertEqual(docs["D1"]["status"], "read")
        self.assertEqual(docs["D3"]["method"], "xlsx (built-in reader)")
        self.assertTrue(docs["D1"]["sha256"])
        self.assertTrue(docs["D1"]["path"].startswith(q.dir))
        text = bstore.texts(q, "b1")
        self.assertIn("ISO/IEC 27001", text["D1"]["text"])
        self.assertIn("Badge printer", text["D3"]["text"])
        self.assertIn("eight", text["D4"]["text"].replace("8", "eight"))
        self.assertTrue(bstore.add_document(q, "b1", os.path.join(FIX, "rfp-visitor-management.docx"))
                        ["duplicate"])

    def test_scanned_pages_are_flagged_and_read_visually(self):
        q = self.bid(docs=("tender-hardware-refresh.pdf",))
        doc = bstore.document(q, "b1", "D1")
        if doc["method"] is None:
            self.skipTest("no PDF reader installed")
        self.assertEqual((doc["status"], doc["unreadable"]), ("partly read", [2]))
        self.assertIn("page(s) 2", doc["limitation"])
        req = {"id": "REQ-010", "category": "warranty", "mandatory": True,
               "text": "Warranty: 5 years on-site, next business day parts.",
               "source": {"doc": "D1", "page": 2}}
        self.assertIn("quote-not-found", self.codes(bstore.apply(q, "b1", {"requirements": [req]})
                                                    ["findings"]))
        bstore.record_visual_reading(q, "b1", "D1", 2, "6. Warranty: 5 years on-site, next "
                                     "business day parts.\n7. Power: each server shall have "
                                     "redundant power supplies.")
        self.assertEqual(bstore.document(q, "b1", "D1")["status"], "read")
        result = bstore.apply(q, "b1", {"requirements": [dict(req, correction="read visually")]})
        self.assertNotIn("quote-not-found", self.codes(result["findings"]))

    def test_arabic_and_mixed_language(self):
        q = self.bid(docs=("rfp-arabic-correspondence.docx",))
        result = bstore.apply(q, "b1", {"requirements": [
            {"id": "REQ-001", "category": "eligibility", "mandatory": True,
             "text": u"يجب تقديم شهادة الزكاة والدخل سارية المفعول.", "source": {"doc": "D1", "section": u"أولاً"}},
            {"id": "REQ-002", "category": "functional", "mandatory": True,
             "text": u"يجب أن يدعم النظام التوقيع الإلكتروني المعتمد", "source": {"doc": "D1", "section": "4"}},
            {"id": "REQ-003", "category": "functional", "mandatory": True,
             "text": u"يجب ان يدعم النظام التوقيع الالكتروني المعتمد", "source": {"doc": "D1", "section": "4"}}]})
        self.assertNotIn("quote-not-found", self.codes(result["findings"]))   # spelling variants

    def test_missing_documents_are_recorded_not_invented(self):
        q = self.bid(docs=())
        item = bstore.add_document(q, "b1", os.path.join(FIX, "annex-c-missing.pdf"), "annex")
        self.assertEqual(item["status"], "missing")
        findings = bstore.apply(q, "b1", {"requirements": [
            {"id": "REQ-001", "category": "scope", "text": "Something from Annex C",
             "source": {"doc": "D9", "section": "C.1"}}]})["findings"]
        self.assertIn("no-source", self.codes(findings, "BLOCKER"))


# --------------------------------------------------------------------------- requirements (9, 21)

class Requirements(BidCase):

    def test_requirements_must_quote_their_source(self):
        q, result = self.analysed()
        self.assertEqual(self.codes(result["findings"], "BLOCKER"), set())
        bad = bstore.apply(q, "b1", {"requirements": [
            {"id": "REQ-009", "category": "functional", "mandatory": True,
             "text": "The system shall integrate with SAP.", "source": {"doc": "D1", "section": "5"}}]})
        self.assertIn("quote-not-found", self.codes(bad["findings"], "BLOCKER"))

    def test_addenda_supersede_and_never_overwrite(self):
        q, _ = self.analysed()
        bstore.add_document(q, "b1", os.path.join(FIX, "addendum-1.md"), "addendum", amends="D1",
                            issued="2026-11-05")
        findings = bstore.apply(q, "b1", {"tender": {"note": "addendum received"}})["findings"]
        self.assertIn("amended-source", self.codes(findings))
        with self.assertRaises(QueueError):
            bstore.apply(q, "b1", {"requirements": [dict(REQUIREMENTS[3], text="within 90 days")]})
        with self.assertRaises(QueueError):
            bstore.apply(q, "b1", {"remove": {"requirements": ["REQ-004"]}})
        result = bstore.apply(q, "b1", {"requirements": [
            {"id": "REQ-006", "category": "delivery", "mandatory": True, "supersedes": "REQ-004",
             "text": "The system shall be delivered and accepted within 90 calendar days of contract signature.",
             "source": {"doc": "D2", "section": "A1"}},
            {"id": "REQ-007", "category": "non_functional", "mandatory": True, "supersedes": "REQ-005",
             "text": "the system shall support 500 named users and 80 concurrent users.",
             "source": {"doc": "D2", "section": "A2"}}]
            + [dict(req, checked_against=["D2"]) for req in REQUIREMENTS[:3]]})
        self.assertNotIn("amended-source", self.codes(result["findings"]))
        document = bstore.get(q, "b1")["document"]
        by_id = bid_model.by_id(document["requirements"])
        self.assertEqual(by_id["REQ-004"]["superseded_by"], "REQ-006")
        self.assertEqual(by_id["REQ-004"]["text"], REQUIREMENTS[3]["text"])      # kept
        revisions = bstore.revisions(q, "b1")
        self.assertGreaterEqual(len(revisions), 4)
        with self.assertRaises(Exception):
            with q.tx():
                q.conn.execute("UPDATE bid_revisions SET note='x'")

    def test_conflicting_sources_and_clarifications(self):
        q, _ = self.analysed()
        result = bstore.apply(q, "b1", {
            "requirements": [{"id": "REQ-008", "class": "CONFLICTING", "category": "non_functional",
                              "text": "300 named users (4.1) versus 500 in another annex",
                              "source": {"doc": "D1", "section": "4.1"}}],
            "clarifications": [{"id": "Q-01", "requirements": ["REQ-008"], "question": "Which applies?",
                                "reason": "conflict", "priority": "high", "status": "open"}]})
        self.assertNotIn("quote-not-found", self.codes(result["findings"]))   # not an explicit quote
        self.assertEqual(bstore.status(q, "b1")["clarifications"], {"open": 1, "total": 1})


# --------------------------------------------------------------------------- compliance (10, 13, 21, 23)

class Compliance(BidCase):

    def test_compliant_needs_a_real_answer_and_evidence(self):
        q, _ = self.analysed()
        findings = bstore.apply(q, "b1", {"compliance": [
            {"id": "REQ-003", "status": "COMPLIANT",
             "response": "The system shall print visitor badges with a QR code."},
            {"id": "REQ-005", "status": "COMPLIANT", "section": "8",
             "response": "Sized for 600 named users: two application servers behind a load "
                         "balancer, tested at 100 concurrent sessions.",
             "evidence": ["SOLUTION:app-tier"]}]})["findings"]
        codes = {(f["where"], f["code"]) for f in findings}
        self.assertIn(("REQ-003", "restates-requirement"), codes)
        self.assertIn(("REQ-003", "no-evidence"), codes)
        self.assertNotIn(("REQ-005", "restates-requirement"), codes)

    def test_company_claims_need_approved_evidence(self):
        q, _ = self.analysed()
        findings = bstore.apply(q, "b1", {"compliance": [
            {"id": "REQ-002", "status": "COMPLIANT", "section": "4",
             "response": "Acme holds certificate number 12345 issued by a certification body.",
             "evidence": ["C-CERT-ISO27001"], "claim": "existing_capability"},
            {"id": "REQ-001", "status": "COMPLIANT", "section": "1", "claim": "existing_capability",
             "response": "Separate envelopes will be prepared by the bid office.",
             "evidence": ["SOLUTION:bid-office"]}]})["findings"]
        codes = {(f["where"], f["code"]) for f in findings if f["severity"] == "BLOCKER"}
        self.assertIn(("REQ-002", "unsupported-claim"), codes)
        self.assertIn(("REQ-001", "unsupported-claim"), codes)

    def test_eligibility_failures_disqualify_and_drive_the_decision(self):
        q, _ = self.analysed()
        bstore.apply(q, "b1", {"compliance": [
            {"id": "REQ-002", "status": "NON_COMPLIANT", "response": "No ISO 27001 certificate yet."}],
            "assessment": {"factors": {name: {"rating": 4, "evidence": "x"} for name in bid_model.FACTORS}}})
        decision = bstore.decision(q, "b1")
        self.assertEqual(decision["decision"], "NO_BID")
        self.assertIn("REQ-002", " ".join(decision["reasons"]))
        bstore.apply(q, "b1", {"assessment": {"conditions": ["certified partner carries REQ-002"]}})
        self.assertEqual(bstore.decision(q, "b1")["decision"], "BID_WITH_CONDITIONS")
        empty = self.queue()
        bstore.create(empty, "b2", REQUEST)
        self.assertEqual(bstore.decision(empty, "b2")["decision"], "INSUFFICIENT_INFORMATION")


# --------------------------------------------------------------------------- estimation (15)

class Estimation(BidCase):

    def test_three_point_estimate_and_schedule(self):
        q, _ = self.analysed()
        bstore.apply(q, "b1", {"wbs": WBS})
        result = bstore.estimate(q, "b1")
        self.assertEqual(result["by_role"]["developer"], Decimal("42.0"))   # (30+160+62)/6
        self.assertEqual(result["scenarios"]["optimistic"], Decimal("34.0"))
        self.assertEqual(result["scenarios"]["conservative"], Decimal("71.0"))
        self.assertEqual(result["duration_days"], 6 + 21)

    def test_a_schedule_beyond_the_delivery_period_is_a_finding(self):
        q, _ = self.analysed()
        bstore.apply(q, "b1", {"wbs": WBS, "delivery_days": 20})
        bstore.estimate(q, "b1")
        self.assertIn("timeline", self.codes(bstore.review(q, "b1")))


# --------------------------------------------------------------------------- pricing (4, 5, 12, 16-19)

class Pricing(unittest.TestCase):

    def test_markup_and_margin_are_different(self):
        self.assertEqual(bid_pricing.selling_from_cost(Decimal(100), {"markup": "0.25"}), Decimal("125"))
        self.assertEqual(bid_pricing.money(bid_pricing.selling_from_cost(Decimal(100), {"margin": "0.25"})),
                         Decimal("133.33"))
        with self.assertRaises(bid_pricing.PricingError):
            bid_pricing.selling_from_cost(Decimal(100), {"margin": "1"})

    def test_full_model_with_fx_tax_and_contingency(self):
        result = bid_pricing.compute(pricing_inputs())
        lines = {line["id"]: line for line in result["lines"]}
        self.assertEqual(lines["HW-01"]["cost"], Decimal("9150.00"))          # 4 x 610 x 3.75
        totals = result["totals"]
        self.assertEqual(totals["COST"], Decimal("64650.00"))
        self.assertEqual(totals["CONTINGENCY"], Decimal("3232.50"))
        for line in result["lines"]:                                          # BOQ reconciles
            self.assertEqual(line["unit_price"] * Decimal(str(line["item"]["quantity"])), line["selling"])
        self.assertEqual(totals["SUBTOTAL"], sum(line["selling"] for line in result["lines"]))
        taxable = lines["HW-01"]["selling"] + lines["SRV-01"]["selling"]
        self.assertEqual(totals["TAX"], bid_pricing.money(taxable * Decimal("0.15")))
        self.assertEqual(totals["TOTAL_BID_PRICE"], totals["SELLING_PRICE"] + totals["TAX"])
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["assumed_fx"], ["USD"])
        self.assertEqual(result["estimates"], ["SRV-01"])

    def test_multi_currency_and_sensitivity(self):
        inputs = pricing_inputs()
        inputs["fx"]["EUR"] = {"rate": "4.05", "status": "CONFIRMED", "source": "bank quote"}
        inputs["items"].append({"id": "LIC-01", "category": "license", "description": "DB licence",
                                "quantity": 8, "unit": "processor", "unit_cost": "1000",
                                "currency": "EUR", "source": {"type": "quotation", "ref": "D5"}})
        inputs["items"].append({"id": "SUP-01", "category": "support", "description": "Support per year",
                                "quantity": 5, "unit": "year", "unit_cost": "6000",
                                "source": {"type": "quotation", "ref": "D5"}})
        rows = {row["scenario"]: row for row in bid_pricing.sensitivity(inputs)}
        self.assertGreater(rows["fx +5%"]["COST"], rows["baseline"]["COST"])
        self.assertLess(rows["fx -5%"]["COST"], rows["baseline"]["COST"])
        self.assertGreater(rows["licences +10%"]["COST"], rows["baseline"]["COST"])
        self.assertEqual(bid_pricing.compute(inputs)["assumed_fx"], ["USD"])   # EUR is confirmed

    def test_pending_prices_block_completion(self):
        inputs = pricing_inputs()
        inputs["items"].append({"id": "LIC-09", "category": "license", "description": "?",
                                "quantity": 1, "unit_cost": None, "source": {"type": "pending"}})
        result = bid_pricing.compute(inputs)
        self.assertEqual((result["status"], result["pending"]), ("incomplete", ["LIC-09"]))

    def test_an_empty_model_is_not_complete(self):
        self.assertEqual(bid_pricing.compute({"currency": "SAR", "items": []})["status"], "not started")

    def test_discount_and_untaxed_lines(self):
        result = bid_pricing.compute(pricing_inputs(discount="0.10", contingency="0"))
        totals = result["totals"]
        self.assertEqual(totals["DISCOUNT"], bid_pricing.money(totals["SUBTOTAL"] * Decimal("0.10")))
        self.assertEqual(totals["SELLING_PRICE"], totals["SUBTOTAL"] - totals["DISCOUNT"])


def evaluate_workbook(path):
    """A small evaluator for the formulas the pricing workbook uses (cell references, ranges,
    + - * /, ROUND, SUM, SUMIF, IF, comparisons), to check them independently of the engine."""
    sheets = ooxml.xlsx_sheets(path)
    cache = {}

    def cell(sheet, ref):
        key = (sheet, ref)
        if key in cache:
            return cache[key]
        match = re.match(r"([A-Z]+)(\d+)", ref)
        column = 0
        for ch in match.group(1):
            column = column * 26 + ord(ch) - 64
        rows = sheets[sheet]
        row = int(match.group(2)) - 1
        value = rows[row][column - 1] if row < len(rows) and column - 1 < len(rows[row]) else ""
        if isinstance(value, dict):
            value = run(value["formula"][1:], sheet)
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                pass
        cache[key] = value
        return value

    def rng(sheet, a, b):
        ca, ra = re.match(r"([A-Z]+)(\d+)", a).groups()
        cb, rb = re.match(r"([A-Z]+)(\d+)", b).groups()
        return [cell(sheet, "%s%d" % (ca, r)) for r in range(int(ra), int(rb) + 1)]

    def run(expression, sheet):
        def ref(match):
            target = match.group(1) or sheet
            if match.group(4):
                return "RANGE(%r,%r,%r)" % (target, match.group(2), match.group(4))
            return "CELL(%r,%r)" % (target, match.group(2))
        code = re.sub(r"(?:(\w+)!)?\b([A-Z]{1,2}\d+)\b(:([A-Z]{1,2}\d+))?",
                      lambda m: ref(type("M", (), {"group": lambda self, i: {1: m.group(1), 2: m.group(2),
                                                                             4: m.group(4)}[i]})()),
                      expression)
        code = code.replace("<>", "!=").replace("=", "==").replace("!==", "!=").replace("<==", "<=") \
            .replace(">==", ">=")
        env = {"CELL": cell, "RANGE": rng, "ROUND": lambda v, n: float(Decimal(str(v)).quantize(
            Decimal(1).scaleb(-int(n)), rounding="ROUND_HALF_UP")),
               "SUM": lambda *values: sum(v for group in values for v in (group if isinstance(group, list)
                                                                         else [group]) if isinstance(v, float)),
               "SUMIF": lambda cond, crit, values: sum(v for c, v in zip(cond, values) if c == crit
                                                       and isinstance(v, float)),
               "IF": lambda c, a, b: a if c else b}
        return eval(code, env)  # noqa: S307 - test-only evaluator of our own formulas
    return cell


class Documents(BidCase):

    def full(self, language="en", company=True):
        q, _ = self.analysed(company=company)
        bstore.apply(q, "b1", {
            "language": language,
            "compliance": [{"id": "REQ-003", "status": "COMPLIANT", "section": "8. Functional Scope",
                            "response": "Badges are printed on thermal printers with a QR code "
                                        "carrying the visit identifier.",
                            "evidence": ["SOLUTION:badge-service"]}],
            "technical": {"required_sections": ["Executive Summary", "Compliance Matrix"],
                          "sections": [{"title": "Executive Summary",
                                        "content": "We propose a web-based visitor system.\n\n"
                                                   "- Active Directory sign-in\n- QR badges"},
                                       {"title": "Compliance Matrix", "auto": "compliance"},
                                       {"title": "Risks and Mitigations", "auto": "risks"}]},
            "risks": [{"id": "RK-1", "risk": "Late AD access", "impact": "delay", "mitigation": "early request"}],
            "commercial": {"cover_letter": "We are pleased to submit our commercial proposal.",
                           "payment_milestones": [{"name": "Design approval", "percent": 30},
                                                  {"name": "Acceptance", "percent": 50},
                                                  {"name": "End of warranty", "percent": 20}],
                           "validity": "Valid for 90 days.", "exclusions": ["Civil works"]},
            "pricing": pricing_inputs(),
            "forms": [{"name": "Form A", "required": True, "signature": True, "status": "missing"}]})
        return q

    def outputs(self, result):
        return {(item["kind"], item["format"]): item for item in result["outputs"]}

    def test_documents_are_written_read_back_and_clean(self):
        q = self.full()
        result = bstore.generate(q, "b1", pdf=False)
        made = self.outputs(result)
        for key in (("technical", "docx"), ("commercial", "docx"), ("summary", "docx"),
                    ("pricing", "xlsx"), ("boq", "xlsx"), ("compliance", "xlsx"),
                    ("clarifications", "xlsx")):
            self.assertIn(key, made)
            self.assertEqual(made[key]["problems"], [], key)
            self.assertTrue(zipfile.is_zipfile(made[key]["path"]), key)
        technical = ooxml.docx_text(made[("technical", "docx")]["path"])
        self.assertIn("Executive Summary", technical)
        self.assertIn("REQ-003", technical)
        with zipfile.ZipFile(made[("technical", "docx")]["path"]) as package:
            self.assertIn("DRAFT", package.read("word/header1.xml").decode("utf-8"))
        commercial = ooxml.docx_text(made[("commercial", "docx")]["path"])
        price = bid_pricing.compute(pricing_inputs())
        self.assertIn(bid_pricing.fmt(price["totals"]["TOTAL_BID_PRICE"]), commercial)
        for value in bid_pricing.internal_values(price):
            self.assertNotIn(value, commercial)
        self.assertNotIn("Q-7781", commercial)           # supplier reference stays internal
        boq = ooxml.xlsx_text(made[("boq", "xlsx")]["path"])
        self.assertIn("Visitor badge printer", boq)

    def test_a_stage_sending_one_section_keeps_the_others(self):
        # found in the live run: a later stage's single section replaced the whole list
        q = self.full()
        bstore.apply(q, "b1", {"technical": {"sections": [
            {"title": "Implementation Schedule", "content": "Three phases."}]}})
        bstore.apply(q, "b1", {"technical": {"sections": [
            {"title": "Executive Summary", "content": "Revised summary."}]}})
        sections = bstore.get(q, "b1")["document"]["technical"]["sections"]
        titles = [s["title"] for s in sections]
        self.assertEqual(titles, ["Executive Summary", "Compliance Matrix", "Risks and Mitigations",
                                  "Implementation Schedule"])
        self.assertEqual(sections[0]["content"], "Revised summary.")
        bstore.apply(q, "b1", {"technical": {"sections": [{"title": "Risks and Mitigations",
                                                           "remove": True}]}})
        self.assertEqual(len(bstore.get(q, "b1")["document"]["technical"]["sections"]), 3)

    def test_sections_follow_the_mandated_then_usual_order(self):
        technical = {"sections": [{"title": "Implementation Schedule"}, {"title": "Annex X"},
                                  {"title": "Executive Summary"}, {"title": "Pricing Approach Note",
                                                                   "order": 0}]}
        self.assertEqual([s["title"] for s in bid_docs.ordered_sections(technical)],
                         ["Pricing Approach Note", "Executive Summary", "Implementation Schedule",
                          "Annex X"])
        technical["required_sections"] = ["Implementation Schedule", "Executive Summary"]
        self.assertEqual([s["title"] for s in bid_docs.ordered_sections(technical)][1:3],
                         ["Implementation Schedule", "Executive Summary"])

    def test_the_customer_compliance_matrix_has_no_internal_columns(self):
        q = self.full()
        bstore.apply(q, "b1", {"compliance": [{"id": "REQ-005", "status": "COMPLIANT", "section": "9",
                                               "response": "Sized for 600 users on two servers.",
                                               "evidence": ["D4 Quotation row 3"], "owner": "architect",
                                               "risk": "medium"}]})
        made = self.outputs(bstore.generate(q, "b1", kinds=["compliance", "compliance-internal"],
                                            pdf=False))
        customer = ooxml.xlsx_text(made[("compliance", "xlsx")]["path"])
        internal = ooxml.xlsx_text(made[("compliance-internal", "xlsx")]["path"])
        self.assertNotIn("Quotation row 3", customer)
        self.assertNotIn("architect", customer)
        self.assertIn("Quotation row 3", internal)
        self.assertIn("INTERNAL", made[("compliance-internal", "xlsx")]["path"])
        out = self.mq("bid", "outputs", "b1", "--text", "compliance")[1]
        self.assertIn("REQ-005", out)

    def test_independent_readers_open_the_files(self):
        q = self.full()
        made = self.outputs(bstore.generate(q, "b1", pdf=False))
        try:
            import docx
            import openpyxl
        except ImportError:
            self.skipTest("python-docx/openpyxl not installed")
        document = docx.Document(made[("technical", "docx")]["path"])
        self.assertTrue(any(p.style.name.startswith("Heading") for p in document.paragraphs))
        self.assertGreaterEqual(len(document.tables), 1)
        book = openpyxl.load_workbook(made[("pricing", "xlsx")]["path"])
        self.assertEqual(book.sheetnames, ["Assumptions", "Lines", "Summary", "Scenarios"])
        self.assertTrue(str(book["Summary"]["B8"].value).startswith("="))

    def test_workbook_formulas_reproduce_the_engine(self):
        q = self.full()
        made = self.outputs(bstore.generate(q, "b1", kinds=["pricing", "boq"], pdf=False))
        result = bid_pricing.compute(pricing_inputs())
        cell = evaluate_workbook(made[("pricing", "xlsx")]["path"])
        for row, key in ((2, "COST"), (3, "CONTINGENCY"), (4, "SUBTOTAL"), (6, "SELLING_PRICE"),
                         (7, "TAX"), (8, "TOTAL_BID_PRICE")):
            self.assertAlmostEqual(cell("Summary", "B%d" % row), float(result["totals"][key]), places=2,
                                   msg=key)
        self.assertAlmostEqual(cell("Summary", "B10"), float(result["totals"]["GROSS_MARGIN"]), places=6)
        boq_cell = evaluate_workbook(made[("boq", "xlsx")]["path"])
        sheet = list(ooxml.xlsx_sheets(made[("boq", "xlsx")]["path"]))[0]
        rows = ooxml.xlsx_sheets(made[("boq", "xlsx")]["path"])[sheet]
        total_row = len(rows)
        self.assertAlmostEqual(boq_cell(sheet, "F%d" % total_row),
                               float(result["totals"]["TOTAL_BID_PRICE"]), places=2)

    def test_envelope_separation_is_enforced(self):
        q = self.full()
        price = bid_pricing.compute(pricing_inputs())
        bstore.apply(q, "b1", {"technical": {"sections": [
            {"title": "Executive Summary", "content": "Our price of SAR %s is competitive."
             % bid_pricing.fmt(price["totals"]["TOTAL_BID_PRICE"])}]}})
        made = self.outputs(bstore.generate(q, "b1", kinds=["technical"], pdf=False))
        codes = {p["code"] for p in made[("technical", "docx")]["problems"]}
        self.assertIn("price-in-technical", codes)
        self.assertIn("price-in-technical", self.codes(bstore.review(q, "b1"), "BLOCKER")
                      | {p["code"] for p in made[("technical", "docx")]["problems"]})
        bstore.apply(q, "b1", {"commercial": {"notes": ["Our markup is modest."]}})
        made = self.outputs(bstore.generate(q, "b1", kinds=["commercial"], pdf=False))
        self.assertIn("internal-term", {p["code"] for p in made[("commercial", "docx")]["problems"]})

    def test_arabic_documents_are_right_to_left(self):
        q = self.full(language="ar")
        made = self.outputs(bstore.generate(q, "b1", kinds=["commercial", "boq"], pdf=False))
        with zipfile.ZipFile(made[("commercial", "docx")]["path"]) as package:
            xml = package.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:bidi/>", xml)
        self.assertIn(u"العرض المالي", xml)
        with zipfile.ZipFile(made[("boq", "xlsx")]["path"]) as package:
            sheet = package.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn('rightToLeft="1"', sheet)

    def test_pdf_output_is_real_and_readable(self):
        tools = bid_docs.pdf_tools()
        if not tools["browser"]:
            self.skipTest("no browser to print PDFs")
        q = self.full(language="ar")
        made = self.outputs(bstore.generate(q, "b1", kinds=["technical", "commercial"]))
        if ("technical", "pdf") not in made:
            self.skipTest("the browser could not print here")
        for kind in ("technical", "commercial"):
            path = made[(kind, "pdf")]["path"]
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(5), b"%PDF-")
            text, pages = bid_docs.pdf_text(path)
            self.assertGreaterEqual(pages, 2)
            self.assertEqual(made[(kind, "pdf")]["problems"], [])
            if text is not None:
                self.assertIn("RFP-2026-014", text)
        try:
            import fitz
        except ImportError:
            return
        with fitz.open(made[("commercial", "pdf")]["path"]) as pdf:
            pixmap = pdf[1].get_pixmap(dpi=40)
            samples = pixmap.samples
            self.assertLess(samples.count(b"\xff") / float(len(samples)), 0.995)   # not blank


# --------------------------------------------------------------------------- approvals (20, 30)

class Approvals(Documents):

    def test_prices_need_approval_and_changes_void_it(self):
        q = self.full()
        with self.assertRaises(QueueError):
            bstore.generate(q, "b1", kinds=["commercial"], final=True, pdf=False)
        preview = bstore.approve(q, "b1", "pricing")
        self.assertFalse(preview["approved"])
        bstore.approve(q, "b1", "pricing", confirmed=True)
        self.assertEqual(bstore.approval_state(q, "b1")["pricing"], "approved")
        inputs = pricing_inputs()
        inputs["items"][0]["unit_cost"] = "650"
        bstore.apply(q, "b1", {"pricing": inputs})
        self.assertEqual(bstore.approval_state(q, "b1")["pricing"], "changed since approval")
        inputs["items"].append({"id": "X", "category": "other", "quantity": 1, "unit_cost": None,
                                "source": {"type": "pending"}})
        bstore.apply(q, "b1", {"pricing": inputs})
        with self.assertRaises(QueueError):
            bstore.approve(q, "b1", "pricing", confirmed=True)

    def test_submission_checklist_and_final_release(self):
        q = self.full()
        bstore.generate(q, "b1", pdf=False)
        items = {item["item"]: item for item in bstore.submission_checklist(q, "b1")}
        self.assertEqual(items["submitted"]["state"], "open")
        self.assertEqual(items["form: Form A"]["state"], "open")
        self.assertEqual(items["release approved by the owner"]["state"], "open")
        for gate in ("pricing", "release"):
            bstore.approve(q, "b1", gate, confirmed=True)
        result = bstore.generate(q, "b1", kinds=["commercial"], final=True, pdf=False)
        path = result["outputs"][0]["path"]
        self.assertIn("-final", path)
        self.assertNotIn("DRAFT", ooxml.docx_text(path))
        with zipfile.ZipFile(path) as package:
            self.assertNotIn("DRAFT", package.read("word/header1.xml").decode("utf-8"))

    def test_review_lists_verified_defects(self):
        q = self.full()
        findings = bstore.review(q, "b1")
        codes = self.codes(findings)
        self.assertTrue({"missing-form", "signature", "estimated-price", "assumed-fx", "approval",
                         "disqualifying"} <= codes, codes)
        bstore.apply(q, "b1", {"advisory": [{"id": "A-1", "severity": "LOW", "code": "tone",
                                             "where": "summary", "message": "too long"}]})
        advisory = [f for f in bstore.review(q, "b1") if f["code"] == "tone"]
        self.assertFalse(advisory[0]["verified"])


# --------------------------------------------------------------------------- isolation, queue, recovery (24-26)

class Isolation(BidCase):

    def test_company_data_stays_outside_projects(self):
        q = self.bid()
        folder = bstore.companies_dir()
        self.assertTrue(folder.startswith(self.home))
        self.assertFalse(os.path.relpath(folder, q.dir).startswith(".."
                                                                   ) and False)
        self.assertFalse(any(name.startswith("ratecard") for _, _, files in os.walk(self.project)
                             for name in files))
        bstore.company_new("other", "Other Co")
        self.assertEqual(bstore.company_profile("other")["items"], [])
        self.assertIsNone(bstore.rate_card("other"))

    def test_bids_do_not_leak_into_each_other(self):
        q = self.bid()
        bstore.apply(q, "b1", {"requirements": [REQUIREMENTS[1]]})
        bstore.create(q, "b2", "Analyze this tender.", company="acme")
        result = bstore.generate(q, "b2", kinds=["compliance"], pdf=False)
        self.assertNotIn("ISO/IEC 27001", ooxml.xlsx_text(result["outputs"][0]["path"]))
        self.assertNotEqual(bstore.folder(q, "b1"), bstore.folder(q, "b2"))

    def test_pipeline_is_queued_once_with_dependencies_and_scoped_permissions(self):
        q = self.bid()
        root = os.path.dirname(HERE)
        first = bstore.queue_pipeline(q, "b1", root)
        self.assertEqual(len(first["added"]), 9)
        self.assertEqual(bstore.queue_pipeline(q, "b1", root)["added"], [])
        task7 = q.get(first["tasks"]["T7"])
        deps = {row[0] for row in q.conn.execute("SELECT depends_on FROM deps WHERE task_id=?",
                                                 (task7["id"],))} if q.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='deps'").fetchone() else None
        if deps is not None:
            self.assertEqual(deps, {first["tasks"]["T4"], first["tasks"]["T5"]})
        prompt = task7["prompt"]
        self.assertIn("# Solution and technical proposal", prompt)      # instructions inline
        self.assertIn("appended: infrastructure-assess", prompt)
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", prompt)
        self.assertIn('sh "%s" bid' % bstore.shim_path(root), prompt)
        tiers = {key: self.routing(task_id)["selectedModel"] for key, task_id in first["tasks"].items()}
        self.assertEqual(tiers["T4"], "opus")
        self.assertEqual(tiers["T1"], "sonnet")
        args = bstore.task_permissions(q, task7, root)
        self.assertIn("--allowedTools", args)
        self.assertTrue(all(" bid *)" in rule or " project *)" in rule
                            for rule in args[args.index("--allowedTools") + 1:]))
        ordinary = q.get(self.add("Fix the typo in the README."))
        self.assertEqual(bstore.task_permissions(q, ordinary, root), [])
        bstore.create(q, "b2", "Analyze this tender.")
        second = bstore.queue_pipeline(q, "b2", root)
        self.assertEqual(len(second["added"]), 3)

    def test_state_survives_reopening(self):
        q, _ = self.analysed()
        q.close()
        reopened = self.queue()
        self.assertEqual(len(bstore.get(reopened, "b1")["document"]["requirements"]), 5)
        self.assertEqual(bstore.document(reopened, "b1", "D1")["status"], "read")


class Hook(BidCase):

    def hook(self, prompt):
        payload = json.dumps({"session_id": "s", "cwd": self.project, "prompt": prompt}).encode()
        result = subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.tmp)
        return json.loads(result.stdout.decode("utf-8"))

    def test_queued_tender_request_becomes_a_bid_project(self):
        answer = self.hook("queue: Draft the complete bid package for this tender")
        self.assertIn("bid project 'b1'", answer["reason"])
        self.assertEqual(self.queue().tasks(), [])
        self.assertIn("NOT saved again", self.hook("queue: Draft the complete bid package for "
                                                   "this tender")["reason"])


class Cli(BidCase):

    def test_round_trip(self):
        self.mq("bid", "company", "new", "acme", "--name", "Acme")
        self.mq("bid", "new", "--request", REQUEST, "--company", "acme")
        self.assertIn("Recorded D1", self.mq("bid", "doc", "b1", os.path.join(FIX, "rfp-visitor-management.docx"))[1])
        self.mq("bid", "apply", "b1", "--data", json.dumps({"requirements": REQUIREMENTS[:2]}))
        self.mq("bid", "apply", "b1", "--data", json.dumps({"pricing": pricing_inputs()}))
        out = self.mq("bid", "price", "b1", "--scenarios")[1]
        self.assertIn("GROSS_MARGIN", out)
        self.assertIn("fx +5%", out)
        status = self.mq_json("bid", "status", "b1")
        self.assertEqual(status["requirements"], 2)
        self.assertIn("approval: pricing", self.mq("bid", "checklist", "b1")[1])
        self.mq("bid", "company", "ratecard", "acme", "--data", json.dumps(RATE_CARD))
        self.assertIn("project manager", self.mq("bid", "company", "ratecard", "acme")[1])
        code, out, _ = self.mq("bid", "generate", "b1", "--no-pdf", "--kind", "boq", check=False)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
