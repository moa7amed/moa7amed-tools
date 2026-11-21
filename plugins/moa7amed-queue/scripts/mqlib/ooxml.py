"""Word (.docx) and Excel (.xlsx) files from the standard library.

Small, deliberate writers for formal documents: headings, paragraphs, bullet lists,
tables, page breaks, a header and a page-numbered footer, right-to-left paragraphs for
Arabic; workbooks with several sheets, numbers, text, formulas (with their computed value
stored, so a reader that does not recalculate still shows the right figure), number
formats, bold headers, column widths and frozen header rows. Plus readers that pull the
text back out of .docx and .xlsx files (used for document intake and to check what was
written).

Nothing here needs a third-party package.
"""
from __future__ import annotations

import datetime
import re
import zipfile
from xml.sax.saxutils import escape

ARABIC = re.compile(u"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def _x(text):
    """XML-escape text and drop characters XML 1.0 does not allow."""
    text = re.sub(u"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "", str(text))
    return escape(text, {'"': "&quot;"})


def is_rtl(text):
    letters = [ch for ch in text or "" if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ARABIC.match(ch)) * 2 >= len(letters)


# --------------------------------------------------------------------------- Word

class Document(object):
    """A Word document built block by block, then saved."""

    def __init__(self, title="", header=None, rtl=False, font="Calibri", cs_font="Arial"):
        self.title, self.header, self.rtl = title, header, rtl
        self.font, self.cs_font = font, cs_font
        self.body = []

    # blocks ---------------------------------------------------------------

    def _direction(self, text):
        """Right to left when the text is mainly Arabic; the document's direction only for
        text without letters (numbers, codes)."""
        if any(ch.isalpha() for ch in str(text or "")):
            return is_rtl(text)
        return self.rtl

    def _run(self, text, bold=False, italic=False, size=None, rtl=False, color=None):
        props = []
        if bold:
            props.append("<w:b/><w:bCs/>")
        if italic:
            props.append("<w:i/><w:iCs/>")
        if color:
            props.append('<w:color w:val="%s"/>' % color)
        if size:
            props.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size * 2, size * 2))
        if rtl:
            props.append("<w:rtl/>")
        out = []
        for index, line in enumerate(str(text).split("\n")):
            if index:
                out.append("<w:r><w:br/></w:r>")
            out.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (
                "<w:rPr>%s</w:rPr>" % "".join(props) if props else "", _x(line)))
        return "".join(out)

    def _paragraph(self, runs, style=None, rtl=None, align=None, keep_next=False, numbered=None):
        rtl = self.rtl if rtl is None else rtl
        props = []
        if style:
            props.append('<w:pStyle w:val="%s"/>' % style)
        if keep_next:
            props.append("<w:keepNext/>")
        if numbered is not None:
            props.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="%d"/></w:numPr>' % numbered)
        if rtl:
            props.append("<w:bidi/>")
        if align:
            props.append('<w:jc w:val="%s"/>' % align)
        return "<w:p>%s%s</w:p>" % ("<w:pPr>%s</w:pPr>" % "".join(props) if props else "", runs)

    def heading(self, text, level=1):
        rtl = self._direction(text)
        self.body.append(self._paragraph(self._run(text, rtl=rtl), "Heading%d" % level, rtl,
                                         keep_next=True))

    def title_page(self, title, lines=(), note=None):
        rtl = self._direction(title)
        self.body.append(self._paragraph(self._run(title, rtl=rtl), "Title", rtl, "center"))
        for line in lines:
            self.body.append(self._paragraph(self._run(line, size=13, rtl=self._direction(line)),
                                             None, self._direction(line), "center"))
        if note:
            self.body.append(self._paragraph(self._run(note, italic=True, size=10,
                                                       rtl=self._direction(note)),
                                             None, self._direction(note), "center"))
        self.page_break()

    def paragraph(self, text, bold=False, italic=False, style=None):
        rtl = self._direction(text)
        self.body.append(self._paragraph(self._run(text, bold, italic, rtl=rtl), style, rtl))

    def bullets(self, items):
        for item in items:
            rtl = self._direction(item)
            self.body.append(self._paragraph(self._run(item, rtl=rtl), "ListBullet", rtl,
                                             numbered=1))

    def page_break(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def table(self, header, rows, widths=None, rtl=None):
        rtl = self.rtl if rtl is None else rtl
        columns = len(header)
        widths = widths or [int(9000 / max(columns, 1))] * columns
        grid = "".join('<w:gridCol w:w="%d"/>' % width for width in widths)

        def cell(text, width, head=False):
            text = "" if text is None else str(text)
            cell_rtl = self._direction(text) if any(ch.isalpha() for ch in text) else rtl
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>' if head else ""
            return ('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>' % (
                width, shade, self._paragraph(self._run(text, bold=head, size=9, rtl=cell_rtl),
                                              "TableText", cell_rtl)))
        out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/>'
               '%s<w:tblLook w:val="04A0" w:firstRow="1"/></w:tblPr><w:tblGrid>%s</w:tblGrid>'
               % ("<w:bidiVisual/>" if rtl else "", grid)]
        out.append('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>%s</w:tr>' % "".join(
            cell(text, widths[i], True) for i, text in enumerate(header)))
        for row in rows:
            out.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>%s</w:tr>' % "".join(
                cell(row[i] if i < len(row) else "", widths[i]) for i in range(columns)))
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self.body.append(self._paragraph(""))

    # files ----------------------------------------------------------------

    def save(self, path):
        sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
                '<w:footerReference w:type="default" r:id="rIdFooter"/>'
                '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1300" w:right="1200" '
                'w:bottom="1300" w:left="1200" w:header="600" w:footer="600" w:gutter="0"/>'
                '%s</w:sectPr>' % ("<w:bidi/>" if self.rtl else ""))
        document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<w:body>%s%s</w:body></w:document>' % ("".join(self.body), sect))
        header_text = self.header if self.header is not None else self.title
        header = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">%s</w:hdr>'
                  % self._paragraph(self._run(header_text, size=8, color="595959",
                                              rtl=self.rtl or is_rtl(header_text)),
                                    None, self.rtl or is_rtl(header_text), "end"))
        footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="16"/></w:rPr>'
                  '<w:t xml:space="preserve">Page </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                  '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                  '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
                  '<w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:rPr><w:sz w:val="16"/></w:rPr>'
                  '<w:t xml:space="preserve"> of </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                  '<w:r><w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>'
                  '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
                  '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
        stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", _DOCX_TYPES)
            package.writestr("_rels/.rels", _ROOT_RELS)
            package.writestr("docProps/core.xml", _core(self.title, stamp))
            package.writestr("docProps/app.xml", _APP)
            package.writestr("word/document.xml", document)
            package.writestr("word/styles.xml", _styles(self.font, self.cs_font))
            package.writestr("word/numbering.xml", _NUMBERING)
            package.writestr("word/settings.xml", _SETTINGS)
            package.writestr("word/header1.xml", header)
            package.writestr("word/footer1.xml", footer)
            package.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        return path


_DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    '</Types>')
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    '</Relationships>')
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
    '<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
    '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
    '</Relationships>')
_APP = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>moa7amed-queue</Application></Properties>')
_SETTINGS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
             '<w:updateFields w:val="true"/><w:defaultTabStop w:val="720"/></w:settings>')
_NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
    '<w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


def _core(title, stamp):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>moa7amed-queue</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
            '</cp:coreProperties>' % (_x(title), stamp, stamp))


def _styles(font, cs_font):
    def style(style_id, name, size, bold=False, color=None, space_before=0, space_after=120,
              based="Normal", outline=None):
        return ('<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/>'
                '<w:basedOn w:val="%s"/><w:next w:val="Normal"/><w:qFormat/>'
                '<w:pPr><w:spacing w:before="%d" w:after="%d"/>%s</w:pPr>'
                '<w:rPr>%s%s<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr></w:style>'
                % (style_id, name, based, space_before, space_after,
                   '<w:outlineLvl w:val="%d"/>' % outline if outline is not None else "",
                   "<w:b/><w:bCs/>" if bold else "",
                   '<w:color w:val="%s"/>' % color if color else "", size * 2, size * 2))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s" '
        'w:eastAsia="%s"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-GB" '
        'w:bidi="ar-SA"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" '
        'w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:qFormat/></w:style>'
        % (font, font, cs_font, font)
        + style("Title", "Title", 26, True, "1F3864", 2400, 360)
        + style("Heading1", "heading 1", 16, True, "1F3864", 360, 120, outline=0)
        + style("Heading2", "heading 2", 13, True, "2F5496", 240, 80, outline=1)
        + style("Heading3", "heading 3", 11, True, "2F5496", 200, 60, outline=2)
        + style("ListBullet", "List Bullet", 11, space_after=60)
        + style("TableText", "Table Text", 9, space_after=0)
        + '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
          '<w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/>'
          '<w:left w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/>'
          '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/>'
          '<w:right w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/>'
          '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/>'
          '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="8EAADB"/></w:tblBorders>'
          '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>'
          '</w:tblCellMar></w:tblPr></w:style>'
        + '</w:styles>')


# --------------------------------------------------------------------------- Excel

def column_name(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


class Formula(object):
    """A cell formula (without the leading '=') and the value it computes to."""

    def __init__(self, expression, value=None):
        self.expression, self.value = expression, value


class Workbook(object):
    """Sheets of rows. A cell is a number, text, None, or a Formula. Styles: 0 plain,
    1 bold header, 2 money (#,##0.00), 3 percent (0.0%), 4 bold money, 5 wrapped text."""

    def __init__(self):
        self.sheets = []

    def sheet(self, name, rows, widths=None, styles=None, freeze=1, rtl=False):
        self.sheets.append({"name": name[:31], "rows": rows, "widths": widths or [],
                            "styles": styles or {}, "freeze": freeze, "rtl": rtl})

    def save(self, path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", _xlsx_types(len(self.sheets)))
            package.writestr("_rels/.rels", _XLSX_ROOT_RELS)
            package.writestr("docProps/core.xml", _core("workbook", datetime.datetime.utcnow()
                                                       .strftime("%Y-%m-%dT%H:%M:%SZ")))
            package.writestr("docProps/app.xml", _APP)
            package.writestr("xl/workbook.xml", self._workbook())
            package.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels())
            package.writestr("xl/styles.xml", _XLSX_STYLES)
            for number, sheet in enumerate(self.sheets, 1):
                package.writestr("xl/worksheets/sheet%d.xml" % number, self._sheet(sheet))
        return path

    def _workbook(self):
        sheets = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (
            _x(sheet["name"]), number, number) for number, sheet in enumerate(self.sheets, 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>%s</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>' % sheets)

    def _workbook_rels(self):
        rels = "".join(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (number, number)
            for number in range(1, len(self.sheets) + 1))
        rels += ('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/'
                 '2006/relationships/styles" Target="styles.xml"/>' % (len(self.sheets) + 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '%s</Relationships>' % rels)

    def _sheet(self, sheet):
        rows_xml = []
        for r_index, row in enumerate(sheet["rows"]):
            cells = []
            for c_index, value in enumerate(row):
                ref = "%s%d" % (column_name(c_index), r_index + 1)
                style = sheet["styles"].get((r_index, c_index))
                if style is None:
                    style = sheet["styles"].get(("col", c_index), 1 if r_index == 0 else 0)
                    if r_index == 0:
                        style = 1
                attr = ' s="%d"' % style if style else ""
                if value is None or value == "":
                    if style:
                        cells.append('<c r="%s"%s/>' % (ref, attr))
                    continue
                if isinstance(value, Formula):
                    cached = value.value
                    if isinstance(cached, (int, float)) and not isinstance(cached, bool):
                        cells.append('<c r="%s"%s><f>%s</f><v>%s</v></c>' % (
                            ref, attr, _x(value.expression), repr(float(cached))))
                    elif cached is None:
                        cells.append('<c r="%s"%s><f>%s</f></c>' % (ref, attr, _x(value.expression)))
                    else:
                        cells.append('<c r="%s"%s t="str"><f>%s</f><v>%s</v></c>' % (
                            ref, attr, _x(value.expression), _x(cached)))
                elif isinstance(value, bool):
                    cells.append('<c r="%s"%s t="b"><v>%d</v></c>' % (ref, attr, int(value)))
                elif isinstance(value, (int, float)):
                    cells.append('<c r="%s"%s><v>%s</v></c>' % (ref, attr, repr(value)))
                elif hasattr(value, "as_tuple"):          # Decimal
                    cells.append('<c r="%s"%s><v>%s</v></c>' % (ref, attr, str(value)))
                else:
                    cells.append('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t>'
                                 '</is></c>' % (ref, attr, _x(value)))
            rows_xml.append('<row r="%d">%s</row>' % (r_index + 1, "".join(cells)))
        cols = "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w)
                       for i, w in enumerate(sheet["widths"]) if w)
        freeze = ""
        if sheet["freeze"]:
            freeze = ('<pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>'
                      % (sheet["freeze"], sheet["freeze"] + 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView workbookViewId="0"%s>%s</sheetView></sheetViews>'
                '%s<sheetData>%s</sheetData></worksheet>' % (
                    ' rightToLeft="1"' if sheet["rtl"] else "", freeze,
                    "<cols>%s</cols>" % cols if cols else "", "".join(rows_xml)))


def _xlsx_types(count):
    sheets = "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/'
                     'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % number
                     for number in range(1, count + 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '%s'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>' % sheets)


_XLSX_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    '</Relationships>')
_XLSX_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/>'
    '<numFmt numFmtId="165" formatCode="0.0%"/></numFmts>'
    '<fonts count="2"><font><sz val="10"/><name val="Arial"/></font>'
    '<font><b/><sz val="10"/><name val="Arial"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E2F3"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment wrapText="1" vertical="top"/></xf>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


# --------------------------------------------------------------------------- readers

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def docx_blocks(path):
    """[(kind, text)] of a .docx in reading order: ("heading1".., "paragraph", "table-row")."""
    import xml.etree.ElementTree as ElementTree
    with zipfile.ZipFile(path) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    out = []
    body = root.find(_W + "body")
    for element in list(body) if body is not None else []:
        if element.tag == _W + "p":
            text = "".join(node.text or "" for node in element.iter(_W + "t"))
            style = element.find("%spPr/%spStyle" % (_W, _W))
            name = (style.get(_W + "val") if style is not None else "") or ""
            kind = "heading%s" % name[-1] if name.lower().startswith("heading") else \
                ("title" if name == "Title" else "paragraph")
            if text.strip():
                out.append((kind, text))
        elif element.tag == _W + "tbl":
            for row in element.iter(_W + "tr"):
                cells = ["".join(node.text or "" for node in cell.iter(_W + "t"))
                         for cell in row.findall(_W + "tc")]
                out.append(("table-row", " | ".join(cells)))
    return out


def docx_text(path):
    return "\n".join(text for _, text in docx_blocks(path))


def xlsx_sheets(path):
    """{sheet name: [[cell text or formula]]} of an .xlsx (shared and inline strings,
    numbers, formulas as "=..." with their cached value)."""
    import xml.etree.ElementTree as ElementTree
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(package.read("xl/sharedStrings.xml"))
            for item in root.findall(_S + "si"):
                shared.append("".join(node.text or "" for node in item.iter(_S + "t")))
        workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        out = {}
        for sheet in workbook.iter(_S + "sheet"):
            rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = targets.get(rid, "")
            target = target.lstrip("/")
            part = target if target.startswith("xl/") else "xl/" + target
            root = ElementTree.fromstring(package.read(part))
            rows = []
            for row in root.iter(_S + "row"):
                values = {}
                for cell in row.findall(_S + "c"):
                    ref = cell.get("r") or ""
                    column = 0
                    for ch in re.match(r"[A-Z]+", ref).group(0) if ref else "A":
                        column = column * 26 + (ord(ch) - 64)
                    formula = cell.find(_S + "f")
                    value = cell.find(_S + "v")
                    kind = cell.get("t")
                    if kind == "inlineStr":
                        text = "".join(node.text or "" for node in cell.iter(_S + "t"))
                    elif kind == "s" and value is not None:
                        text = shared[int(value.text)]
                    else:
                        text = value.text if value is not None else ""
                    if formula is not None and formula.text:
                        text = {"formula": "=" + formula.text, "value": text}
                    values[column - 1] = text
                rows.append([values.get(i, "") for i in range(max(values) + 1)] if values else [])
            out[sheet.get("name")] = rows
        return out


def xlsx_text(path):
    lines = []
    for name, rows in xlsx_sheets(path).items():
        lines.append("[sheet %s]" % name)
        for index, row in enumerate(rows, 1):
            cells = [cell["value"] if isinstance(cell, dict) else str(cell) for cell in row]
            if any(cell.strip() for cell in cells):
                lines.append("row %d: %s" % (index, " | ".join(cells)))
    return "\n".join(lines)
