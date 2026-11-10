"""Render a written proposal document to Word, in the layout a government proposal uses.

Formatting is done here rather than by the model because it is a compliance rule, not a style
choice: a solicitation that says "Times New Roman 12, one-inch margins" means it. The house layout
— cover page with a "Submitted by" table and the FAR 52.215-1(e) restriction legend, a running
header and footer, numbered tables, a reference block per contract and a biographical table per
person — is built here too, from records rather than prose, which is most of what makes a real
proposal read like one. Every company detail comes from that company's own record.
"""
from __future__ import annotations

import io
import re
from datetime import date

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK, WD_COLOR_INDEX, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

_GAP = re.compile(r"(\[(?:NEEDS|CHECK):[^\]]*\])")
_DEFAULT_FONT, _DEFAULT_SIZE = "Times New Roman", 12
_NAVY = RGBColor(0x1F, 0x38, 0x64)
_NAVY_HEX, _PALE_HEX = "1F3864", "DCE3EF"

# FAR 52.215-1(e), the standard legend for proposal data (public regulation text).
_LEGEND = ("This proposal includes data that shall not be disclosed outside the Government and "
           "shall not be duplicated, used, or disclosed—in whole or in part—for any purpose other "
           "than to evaluate this proposal. If, however, a contract is awarded to this offeror as a "
           "result of—or in connection with—the submission of this data, the Government shall have "
           "the right to duplicate, use, or disclose the data to the extent provided in the "
           "resulting contract. This restriction does not limit the Government's right to use "
           "information contained in this data if it is obtained from another source without "
           "restriction. The data subject to this restriction are contained in all sheets of this "
           "proposal.")
_SHEET_NOTE = ("Use or disclosure of data contained on this sheet is subject to the restriction on "
               "the title page of this proposal.")


def font_from(rule: str | None) -> tuple[str, int]:
    """("Times New Roman", 12) from a rule like 'Times New Roman 12-point'. Falls back to that."""
    text = rule or ""
    name = next((f for f in ("Times New Roman", "Arial", "Calibri", "Courier New", "Garamond", "Cambria")
                 if f.lower() in text.lower()), _DEFAULT_FONT)
    size = re.search(r"(\d{1,2})[ -]*(?:pt|point|font)?", text)
    points = int(size.group(1)) if size and 8 <= int(size.group(1)) <= 16 else _DEFAULT_SIZE
    return name, points


def margin_from(rule: str | None) -> float:
    """Inches. One inch unless the rule states a number."""
    m = re.search(r"(\d(?:\.\d+)?)\s*(?:\"|in|inch)", rule or "", re.I)
    return float(m.group(1)) if m else 1.0


def _excluded(counting: str | None, what: str) -> bool:
    """Does the page-counting rule say `what` (cover, table of contents) doesn't count?"""
    text = (counting or "").lower()
    return what in text and bool(re.search(r"not count|do not count|does not count|exclud|not includ", text))


_NO_LIMIT = re.compile(r"\bno\s+(?:page\s+)?limit|\bnot\s+(?:stated|specified|limited)|\bunlimited\b|^\s*none\b", re.I)
_PAGES = re.compile(r"(\d+)\s*-?\s*pages?\b", re.I)
_PART_PAGES = re.compile(r"\b(?:part|volume|vol\.?|section|tab|factor)\s+[A-Z0-9]{1,4}\b[^;\n\d]*?(\d+)\s*-?\s*pages?\b", re.I)


def page_total(page_limit: str | None) -> int | None:
    """The pages a document may run to. Separate limits for its parts add up ("Part A: 1 page;
    Part B: 5 pages; Part C: 5 pages" is 11); otherwise the first number. None when there is none."""
    text = page_limit or ""
    if not re.search(r"\d", text) or _NO_LIMIT.search(text):
        return None
    parts = _PART_PAGES.findall(text)
    if len(parts) >= 2:
        return sum(int(n) for n in parts)
    # "2 pages each" is a per-person cap, not a part of the total.
    chunks = [m.group(1) for c in re.split(r"[;\n]", text)
              if (m := _PAGES.search(c)) and not re.search(r"\beach\b|\bper\b", c, re.I)]
    if len(chunks) >= 2:
        return sum(int(n) for n in chunks)
    return int(re.search(r"\d+", text).group())


def front_matter(page_limit: str | None, counting: str | None) -> tuple[bool, bool]:
    """(cover page, contents page). Both only where they cost the bid no counted pages."""
    limited = bool(page_limit) and not _NO_LIMIT.search(page_limit)
    if not limited:
        return True, True
    return _excluded(counting, "cover"), _excluded(counting, "table of contents") or _excluded(counting, "toc")


# ---- low-level helpers -----------------------------------------------------------------
def _runs(paragraph, text: str, bold: bool = False, size: int | None = None, color=None) -> None:
    """Add text, highlighting every [NEEDS: …] and [CHECK: …] so none is missed on a read-through."""
    for piece in _GAP.split(text or ""):
        if not piece:
            continue
        run = paragraph.add_run(piece)
        run.bold = bold or None
        if size:
            run.font.size = Pt(size)
        if color is not None:
            run.font.color.rgb = color
        if _GAP.fullmatch(piece):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.bold = True


def _shade(cell, hex_fill: str) -> None:
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc.append(shd)


def _field(paragraph, code: str) -> None:
    """A Word field (PAGE), which Word, Word for the web and LibreOffice all fill in."""
    for kind, text in (("begin", None), (None, code), ("separate", None), (None, "1"), ("end", None)):
        run = paragraph.add_run()
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        elif text == code:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {code} "
        else:
            el = OxmlElement("w:t")
            el.text = text
        run._r.append(el)


def _tabs(paragraph, width: float, centre: bool = False) -> None:
    stops = paragraph.paragraph_format.tab_stops
    if centre:
        stops.add_tab_stop(Inches(width / 2), WD_TAB_ALIGNMENT.CENTER)
    stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)


class _Builder:
    def __init__(self, font: str, size: int):
        self.d = Document()
        self.size = size
        self.tables = 0
        normal = self.d.styles["Normal"]
        normal.font.name, normal.font.size = font, Pt(size)
        normal.paragraph_format.space_after = Pt(6)
        for name, extra in (("Title", 6), ("Heading 1", 2), ("Heading 2", 1), ("Heading 3", 0)):
            st = self.d.styles[name]
            st.font.name, st.font.size, st.font.bold = font, Pt(size + extra), True
            st.font.color.rgb = _NAVY
            st.paragraph_format.space_before, st.paragraph_format.space_after = Pt(10), Pt(4)
        self.d.styles["List Bullet"].font.name = font

    def para(self, text: str, **kw):
        p = self.d.add_paragraph()
        _runs(p, text, **kw)
        return p

    def caption(self, title: str) -> str:
        self.tables += 1
        title = re.sub(r"^\s*(?:table|figure|exhibit)\s*\d+[\s:,.–-]*", "", title or "", flags=re.I)
        return f"Table {self.tables}: {title}" if title else f"Table {self.tables}"

    def grid(self, title: str, headers: list[str], rows: list[list[str]]) -> None:
        """A table with a navy caption row and a pale header row, like the evaluator expects."""
        cols = max(len(headers), max((len(r) for r in rows), default=1), 1)
        t = self.d.add_table(rows=0, cols=cols)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        cap = t.add_row().cells
        merged = cap[0].merge(cap[-1]) if cols > 1 else cap[0]
        merged.paragraphs[0].alignment = 1
        _runs(merged.paragraphs[0], self.caption(title), bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        _shade(merged, _NAVY_HEX)
        if headers:
            for cell, text in zip(t.add_row().cells, headers + [""] * (cols - len(headers))):
                _runs(cell.paragraphs[0], str(text), bold=True)
                _shade(cell, _PALE_HEX)
        for row in rows:
            for cell, text in zip(t.add_row().cells, list(row) + [""] * (cols - len(row))):
                _runs(cell.paragraphs[0], str(text), size=self.size - 1)
        self.d.add_paragraph()

    def kv(self, title: str | None, pairs: list[tuple[str, str]], label_width: float = 1.8) -> None:
        """Label | value rows, labels shaded — the 'Contract Information' and 'Biographical Data'
        shape. `title` None = no numbered caption (the cover's "Submitted by" table)."""
        t = self.d.add_table(rows=0, cols=2)
        t.style = "Table Grid"
        if title is not None:
            cap = t.add_row().cells
            merged = cap[0].merge(cap[1])
            _runs(merged.paragraphs[0], self.caption(title), bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
            _shade(merged, _NAVY_HEX)
        for label, value in pairs:
            a, b = t.add_row().cells
            _runs(a.paragraphs[0], label, bold=True, size=self.size - 1)
            _shade(a, _PALE_HEX)
            for i, line in enumerate(str(value or "").split("\n")):
                _runs(b.paragraphs[0] if i == 0 else b.add_paragraph(), line, size=self.size - 1)
        for row in t.rows:
            row.cells[0].width = Inches(label_width)
        self.d.add_paragraph()

    def box(self, text: str, italic: bool = True) -> None:
        t = self.d.add_table(rows=1, cols=1)
        t.style = "Table Grid"
        cell = t.rows[0].cells[0]
        _shade(cell, "F2F2F2")
        run = cell.paragraphs[0].add_run(text)
        run.italic, run.font.size = italic, Pt(max(9, self.size - 2))


# ---- the document ----------------------------------------------------------------------
def render(doc, solicitation: str | None, format_rules: dict | None, meta: dict | None = None,
           page_limit: str | None = None) -> bytes:
    """`doc` is a grounded WrittenDocument. `meta` carries the bid and company details for the
    cover, header and footer (see tasks/proposal_tasks.py). Returns the .docx bytes."""
    rules = format_rules or {}
    meta = meta or {}
    font, size = font_from(rules.get("font"))
    margin = margin_from(rules.get("margins"))
    cover, contents = front_matter(page_limit, rules.get("page_counting"))
    company = meta.get("company") or {}
    company_name = company.get("name") or "[NEEDS: company name]"
    bid = meta.get("bid_title") or ""
    volume = meta.get("volume") or doc.title  # the checklist's short name, not the model's long title
    short_bid = bid if len(bid) <= 60 else bid[:57].rstrip() + "…"
    sol = solicitation or ""
    today = meta.get("date") or date.today().strftime("%B %-d, %Y")

    b = _Builder(font, size)
    d = b.d
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Inches(margin)
    sec.top_margin = sec.bottom_margin = Inches(margin)
    width = sec.page_width.inches - 2 * margin

    # Running header and footer: company and volume on the left, bid and number on the right.
    h = sec.header.paragraphs[0]
    _tabs(h, width)
    _runs(h, f"{company_name}\t{short_bid}", size=size - 2)
    h2 = sec.header.add_paragraph()
    _tabs(h2, width)
    _runs(h2, f"{volume}\t{sol}", size=size - 2)
    # The disclosure marking is the writer's call (WrittenDocument.disclosure_marking): a proposal
    # carrying the company's own data usually wants one, but many RFIs and sources-sought notices
    # say not to submit proprietary information at all, and then any marking is wrong.
    marking = (getattr(doc, "disclosure_marking", "") or "").strip()
    f = sec.footer.paragraphs[0]
    _tabs(f, width, centre=True)
    _runs(f, f"{today}\t{marking}\tPage ", size=size - 2)
    _field(f, "PAGE")
    if cover and marking:
        note = sec.footer.add_paragraph()
        note.alignment = 1
        r = note.add_run(_SHEET_NOTE)
        r.italic, r.font.size = True, Pt(max(8, size - 3))

    if cover:
        sec.different_first_page_header_footer = True
        cf = sec.first_page_footer.paragraphs[0]
        _tabs(cf, width, centre=True)
        _runs(cf, f"{today}\t{marking}", size=size - 2)
        for _ in range(3):
            d.add_paragraph()
        b.para(bid or doc.title, bold=True, size=size + 14, color=_NAVY)
        if sol:
            b.para(f"Solicitation Number: {sol}", bold=True, size=size + 2, color=_NAVY)
        b.para(volume, bold=True, size=size + 2, color=_NAVY)
        if meta.get("agency"):
            b.para(meta["agency"], size=size)
        if company.get("designation"):
            b.para(company["designation"], bold=True, size=size, color=_NAVY)
        d.add_paragraph()
        poc = meta.get("poc") or "[NEEDS: company point of contact: name, phone, email]"
        submitted = "\n".join(x for x in (company_name, *company.get("address_lines", []),
                                          company.get("website")) if x)
        pairs = [("Submitted by", submitted), ("Company POC", poc),
                 ("CAGE Code", company.get("cage") or "[NEEDS: CAGE code]"),
                 ("Unique Entity ID", company.get("uei") or "[NEEDS: UEI]")]
        if meta.get("vehicle"):
            pairs.append(("Contract vehicle", meta["vehicle"]))
        b.kv(None, pairs)
        legend = " ".join(x for x in (meta.get("validity"), _LEGEND if marking else None) if x)
        if legend:
            b.box(legend)
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    if contents:
        b.para("Table of Contents", bold=True, size=size + 2, color=_NAVY)
        for s in doc.sections:
            if s.heading:
                p = d.add_paragraph(style="List Bullet")
                _runs(p, s.heading)
        if doc.assumptions:
            _runs(d.add_paragraph(style="List Bullet"), "Assumptions")
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    d.add_heading(doc.title, level=1)
    refs = people = 0
    for s in doc.sections:
        if s.heading:
            d.add_heading(s.heading, level=2)
        for text in s.paragraphs:
            b.para(text)
        for text in s.bullets:
            _runs(d.add_paragraph(style="List Bullet"), text)
        if s.table and (s.table.headers or s.table.rows):
            b.grid(s.table.caption or s.heading, s.table.headers, s.table.rows)
        for r in s.references:
            refs += 1
            facts = dict(r._facts)
            title = facts.get("Contract title") or r.contract_number
            role = facts.get("Role") or ""
            d.add_heading(f"Past Performance {refs}: {title}" + (f" ({role})" if role and "[" not in role else ""),
                          level=3)
            b.kv(f"Past Performance {refs} – Contract Information", r._facts)
            if r.scope.text:
                b.grid("Scope of the Contract", [], [[r.scope.text]])
            if r.work:
                b.grid(f"Relevant Work Performed on {title}", ["Work performed", f"Relevance to {bid or 'this requirement'}"],
                       [[w.work.text, w.relevance] for w in r.work])
            for c in r.results:
                _runs(d.add_paragraph(style="List Bullet"), c.text)
        if s.people:
            b.grid("Proposed Key Personnel", ["Proposed position", "Proposed individual", "Clearance"],
                   [[dict(p._facts).get("Proposed position", p.proposed_position), p.name,
                     dict(p._facts).get("Security clearance", "")] for p in s.people])
            # A full biographical table belongs in the resume document; elsewhere the summary
            # table names the person without spending the page budget twice.
            for p in (s.people if meta.get("kind") in (None, "resume") else []):
                people += 1
                pairs = list(p._facts)
                if p.summary:
                    pairs.insert(3, ("Summary", " ".join(c.text for c in p.summary)))
                pairs.append(("Employment history", "\n".join(c.text for c in p.employment)
                              or "[NEEDS: employment history with employers and dates]"))
                b.kv(f"Biographical Data – {p.name}", pairs)
                if p.requirements:
                    b.grid(f"{p.proposed_position or p.name}: Requirements and Experience",
                           ["Requirement", "Experience"],
                           [[q.requirement, q.experience.text] for q in p.requirements])
    if doc.assumptions:
        d.add_heading("Assumptions", level=2)
        for i, a in enumerate(doc.assumptions, 1):
            p = d.add_paragraph()
            _runs(p, f"Assumption {i}: ", bold=True)
            _runs(p, a)

    buf = io.BytesIO()
    d.save(buf)
    from utils.office_props import clean

    # The file's hidden author is the company, never the library that built it.
    return clean(buf.getvalue(), company.get("name") or "")


def estimate_pages(content: bytes, skip_cover: bool) -> int:
    """Pages the evaluator will count, measured on the rendered file: paragraphs and table rows
    wrapped at the text width, headings and spacing included, the cover left out when the
    solicitation doesn't count it. Close to Word's count, not exact — the warning has margin."""
    import math

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Document(io.BytesIO(content))
    sec = d.sections[0]
    width_in = (sec.page_width - sec.left_margin - sec.right_margin) / 914400
    height_in = (sec.page_height - sec.top_margin - sec.bottom_margin) / 914400
    size = (d.styles["Normal"].font.size or Pt(12)).pt
    per_line = width_in * 72 / (size * 0.5)        # Times-like fonts average half an em per character
    lines_per_page = height_in * 72 / (size * 1.15)
    pages, lines = [], 0.0
    for el in d.element.body.iterchildren():
        if el.tag.endswith("}p"):
            p = Paragraph(el, d)
            if 'w:type="page"' in el.xml:  # a page break starts a new page
                pages.append(lines)
                lines = 0.0
                continue
            text = p.text.strip()
            heading = p.style.name.startswith(("Heading", "Title"))
            lines += (1.9 if heading else max(1, math.ceil(len(text) / per_line)) + 0.40) if text else 1
        elif el.tag.endswith("}tbl"):
            t = Table(el, d)
            cols = max(1, len(t.columns))
            for row in t.rows:
                seen, row_lines = set(), 1.0
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    span = sum(1 for c in row.cells if c._tc is cell._tc)
                    chars = per_line * span / cols * 0.9
                    cell_lines = sum(max(1, math.ceil(len(x.text) / max(chars, 8))) for x in cell.paragraphs)
                    row_lines = max(row_lines, cell_lines)
                lines += row_lines + 0.25
            lines += 0.5
    pages.append(lines)
    if skip_cover and len(pages) > 1:
        pages = pages[1:]
    return sum(max(1, math.ceil(p / lines_per_page)) for p in pages if p > 0.5)
