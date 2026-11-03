"""Render a written proposal document to Word, applying the solicitation's format rules.

Formatting is done here rather than by the model because it is a compliance rule, not a style
choice: a solicitation that says "Times New Roman 12, one-inch margins" means it.
"""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Inches, Pt

_GAP = re.compile(r"(\[NEEDS:[^\]]*\])")
_DEFAULT_FONT, _DEFAULT_SIZE = "Times New Roman", 12


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


def _runs(paragraph, text: str) -> None:
    """Add text, highlighting every [NEEDS: ...] so a gap can't be missed on a read-through."""
    for piece in _GAP.split(text):
        if not piece:
            continue
        run = paragraph.add_run(piece)
        if _GAP.fullmatch(piece):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.bold = True


def render(doc, solicitation: str | None, format_rules: dict | None) -> bytes:
    """`doc` is a WrittenDocument. Returns the .docx bytes."""
    rules = format_rules or {}
    font, size = font_from(rules.get("font"))
    margin = margin_from(rules.get("margins"))

    d = Document()
    style = d.styles["Normal"]
    style.font.name, style.font.size = font, Pt(size)
    for name in ("Title", "Heading 1"):
        d.styles[name].font.name = font
    d.styles["Heading 1"].font.size = Pt(size + 1)
    section = d.sections[0]
    section.left_margin = section.right_margin = Inches(margin)
    section.top_margin = section.bottom_margin = Inches(margin)
    if solicitation:
        section.header.paragraphs[0].text = str(solicitation)

    title = d.add_paragraph()
    run = title.add_run(doc.title)
    run.bold, run.font.size = True, Pt(size + 3)

    for s in doc.sections:
        if s.heading:
            d.add_heading(s.heading, level=1)
        for text in s.paragraphs:
            _runs(d.add_paragraph(), text)
        for text in s.bullets:
            _runs(d.add_paragraph(style="List Bullet"), text)
        if s.table and s.table.headers:
            table = d.add_table(rows=1, cols=len(s.table.headers))
            table.style = "Table Grid"
            for cell, text in zip(table.rows[0].cells, s.table.headers):
                cell.text = str(text)
            for row in s.table.rows:
                cells = table.add_row().cells
                for cell, text in zip(cells, row):
                    _runs(cell.paragraphs[0], str(text))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
