#!/usr/bin/env python3
"""Build the synthetic tender documents used by the BidStudio tests.

Everything here is invented: organisations, references, requirements, prices. Run it
again only to change the fixtures (it needs Pillow and reportlab for the PDF with a
scanned page); the tests use the files it wrote and need neither.

    python tests/fixtures/bid/make_fixtures.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))

from mqlib import ooxml  # noqa: E402

SOFTWARE_RFP = [
    ("title", "Request for Proposal RFP-2026-014: Visitor Management System"),
    ("p", "Issued by: Northbridge Municipal Authority (a fictitious organisation). Issue date: 2026-10-01."),
    ("h", "1. Submission Instructions"),
    ("p", "1.1 Proposals must be received no later than 2026-12-15 12:00 local time."),
    ("p", "1.2 The technical proposal and the commercial proposal shall be submitted in separate "
          "sealed envelopes. No price information shall appear in the technical proposal."),
    ("p", "1.3 The technical proposal shall not exceed 40 pages excluding appendices."),
    ("h", "2. Eligibility"),
    ("p", "2.1 The bidder must hold a valid commercial registration."),
    ("p", "2.2 The bidder must hold a valid ISO/IEC 27001 certificate."),
    ("p", "2.3 The bidder must have delivered at least two similar systems in the last five years."),
    ("h", "3. Functional Requirements"),
    ("table", ["ID", "Requirement", "Priority"], [
        ["F-01", "The system shall register visitors with name, organisation, host and visit purpose.", "Mandatory"],
        ["F-02", "The system shall print visitor badges with a QR code.", "Mandatory"],
        ["F-03", "The system shall notify the host by e-mail when the visitor arrives.", "Mandatory"],
        ["F-04", "The system should provide a self-service kiosk mode.", "Optional"],
        ["F-05", "The system shall provide reports of visits per day, department and host.", "Mandatory"]]),
    ("h", "4. Non-Functional Requirements"),
    ("p", "4.1 The system shall support 300 named users and 50 concurrent users."),
    ("p", "4.2 The system shall be available 99.5% of the time during business hours."),
    ("p", "4.3 The system shall support Arabic and English user interfaces, including right-to-left layout."),
    ("h", "5. Security and Integration"),
    ("p", "5.1 Users shall authenticate against the Authority's Active Directory."),
    ("p", "5.2 All personal data shall be stored within the country."),
    ("h", "6. Delivery, Training and Warranty"),
    ("p", "6.1 The system shall be delivered and accepted within 120 calendar days of contract signature."),
    ("p", "6.2 The bidder shall train 10 administrators and 40 reception staff."),
    ("p", "6.3 A warranty of 12 months after acceptance is required."),
    ("p", "6.4 Support response time for critical incidents shall not exceed 4 hours."),
    ("h", "7. Commercial Terms"),
    ("p", "7.1 Prices shall be quoted in Saudi Riyals (SAR) inclusive of all costs and exclusive of VAT."),
    ("p", "7.2 Payment: 30% on design approval, 50% on acceptance, 20% after the warranty period."),
    ("p", "7.3 Delay penalty: 0.5% of the contract value per week, capped at 10%."),
    ("p", "7.4 Proposals shall remain valid for 90 days."),
    ("h", "8. Evaluation"),
    ("p", "8.1 Technical 70%, commercial 30%. Bidders scoring below 60% technically are not evaluated commercially."),
    ("h", "9. Forms"),
    ("p", "9.1 Form A (Bidder Declaration) must be completed and signed by an authorised signatory."),
]

ARABIC_RFP = [
    ("title", u"كراسة الشروط والمواصفات: منافسة رقم 45/2026 لتوريد وتشغيل نظام إدارة المراسلات"),
    ("p", u"الجهة: هيئة الواحة للخدمات (جهة افتراضية لأغراض الاختبار)."),
    ("h", u"أولاً: شروط الأهلية"),
    ("p", u"1. يجب أن يكون المتقدم حاصلاً على سجل تجاري ساري المفعول."),
    ("p", u"2. يجب تقديم شهادة الزكاة والدخل سارية المفعول."),
    ("h", u"ثانياً: المتطلبات الفنية"),
    ("p", u"3. يجب أن يدعم النظام تسجيل المراسلات الواردة والصادرة وأرشفتها إلكترونياً."),
    ("p", u"4. يجب أن يدعم النظام التوقيع الإلكتروني المعتمد."),
    ("p", u"5. يجب أن تكون واجهة النظام باللغة العربية مع دعم اللغة الإنجليزية."),
    ("h", u"ثالثاً: مدة التنفيذ والضمان"),
    ("p", u"6. مدة التنفيذ 90 يوماً من تاريخ توقيع العقد."),
    ("p", u"7. الضمان لمدة سنتين بعد الاستلام النهائي."),
    ("h", u"رابعاً: الشروط المالية"),
    ("p", u"8. تقدم الأسعار بالريال السعودي شاملة ضريبة القيمة المضافة 15٪."),
]


def write_docx(blocks, path, rtl=False):
    document = ooxml.Document(title=blocks[0][1], header=blocks[0][1], rtl=rtl)
    for block in blocks:
        if block[0] == "title":
            document.title_page(block[1])
        elif block[0] == "h":
            document.heading(block[1], 1)
        elif block[0] == "p":
            document.paragraph(block[1])
        elif block[0] == "table":
            document.table(block[1], block[2], [1200, 6400, 1600])
    document.save(path)


def write_pdf_with_scan(path):
    """An infrastructure tender: page 1 and 3 have text, page 2 is an image (a scan)."""
    from io import BytesIO

    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    lines = ["Tender T-HW-2026-07: Data Centre Hardware Refresh (fictitious)",
             "Issued by: Eastport Health Services. Closing date: 2026-12-20.",
             "1. Scope: supply, install and commission server and storage hardware.",
             "2. The bidder must be an authorised partner of each hardware manufacturer offered.",
             "3. Supply 6 rack servers, each with at least 2 CPUs of 24 cores and 512 GB RAM.",
             "4. Supply one storage array with at least 200 TB usable capacity.",
             "5. Delivery within 60 days of the purchase order."]
    y = height - 72
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 22
    pdf.showPage()
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(["6. Warranty: 5 years on-site, next business day parts.",
                                  "7. Power: each server shall have redundant power supplies.",
                                  "(this page is a scanned image without a text layer)"]):
        draw.text((120, 200 + index * 60), line, fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0)
    pdf.drawImage(ImageReader(buffer), 0, 0, width, height)
    pdf.showPage()
    y = height - 72
    for line in ["8. Prices in US dollars, delivered duty paid.",
                 "9. Evaluation: lowest technically compliant price."]:
        pdf.drawString(60, y, line)
        y -= 22
    pdf.showPage()
    pdf.save()


def main():
    write_docx(SOFTWARE_RFP, os.path.join(HERE, "rfp-visitor-management.docx"))
    write_docx(ARABIC_RFP, os.path.join(HERE, "rfp-arabic-correspondence.docx"), rtl=True)
    write_pdf_with_scan(os.path.join(HERE, "tender-hardware-refresh.pdf"))
    with open(os.path.join(HERE, "addendum-1.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("# Addendum No. 1 to RFP-2026-014 (issued 2026-11-05)\n\n"
                     "A1. Clause 6.1 is replaced: The system shall be delivered and accepted within "
                     "90 calendar days of contract signature.\n\n"
                     "A2. Clause 4.1 is clarified: the system shall support 500 named users and 80 "
                     "concurrent users.\n\n"
                     "A3. The submission deadline remains 2026-12-15.\n")
    with open(os.path.join(HERE, "rfp-licensing-support.txt"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("RFQ-LS-2026-03: Database licences and five-year support (fictitious)\n\n"
                     "1. Supply 8 processor licences of an enterprise relational database.\n"
                     "2. Provide five years of vendor support and updates, priced per year.\n"
                     "3. Provide 24x7 support with a 2-hour response for severity 1 incidents.\n"
                     "4. Prices in EUR. Payment annually in advance.\n"
                     "5. The bidder must be an authorised reseller of the database vendor.\n")
    book = ooxml.Workbook()
    book.sheet("Quotation", [
        ["Vendor quotation Q-7781 (fictitious) for RFP-2026-014", "", "", ""],
        ["Item", "Qty", "Unit price (USD)", "Total (USD)"],
        ["Badge printer, thermal, QR capable", 4, 610.0, ooxml.Formula("B3*C3", 2440.0)],
        ["Visitor kiosk tablet with stand", 2, 890.0, ooxml.Formula("B4*C4", 1780.0)],
        ["Valid until 2027-01-31", "", "", ""]], widths=[44, 8, 18, 16])
    book.save(os.path.join(HERE, "quotation-hardware.xlsx"))
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
