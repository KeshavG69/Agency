"""The capability matrix as an Excel workbook people can sort, filter and edit in SharePoint."""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from models.capability import RUBRIC
from utils.office_props import clean

_SCORE_FILL = {5: "C6EFCE", 4: "DDEFD5", 3: "FFF2CC", 2: "FCE4D6", 1: "F8CBAD"}
_HEAD = PatternFill("solid", fgColor="1F3864")
_WRAP = Alignment(wrap_text=True, vertical="top")


def _sheet(ws, headers: list[str], rows: list[list], widths: list[int], score_col: int | None = None):
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEAD
        cell.alignment = _WRAP
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = _WRAP
        if score_col is not None:
            cell = row[score_col]
            if isinstance(cell.value, int) and cell.value in _SCORE_FILL:
                cell.fill = PatternFill("solid", fgColor=_SCORE_FILL[cell.value])
                cell.alignment = Alignment(horizontal="center", vertical="top")
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions


def _evidence(r: dict) -> str:
    lines = [f"{e.get('name') or e.get('file')}: {e.get('claim')}"
             for e in r.get("evidence") or [] if e.get("verified")]
    return "\n".join(lines)


def workbook(matrix: dict, company: str, solicitation: str | None, title: str | None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Capability Matrix"
    rows = []
    for a in matrix.get("areas") or []:
        for r in a.get("requirements") or []:
            rows.append([a.get("name"), r.get("text"), r.get("source_ref") or "", r.get("score"),
                         _evidence(r), r.get("rationale") or "",
                         r.get("gap") or "", r.get("action") or ""])
    _sheet(ws, ["Area", "Requirement", "Section", "Score (1-5)", "Our proof", "Why this score",
                "Gap", "How to close it"], rows, [20, 48, 12, 10, 48, 40, 32, 32], score_col=3)

    gaps = [[row[0], row[1], row[3], row[6], row[7]] for row in rows if isinstance(row[3], int) and row[3] <= 2]
    _sheet(wb.create_sheet("Gaps"), ["Area", "Requirement", "Score", "Gap", "How to close it"],
           gaps, [20, 48, 8, 40, 40], score_col=2)

    contacts = [[c.get("name"), c.get("role") or "", c.get("email") or "", c.get("phone") or ""]
                for c in matrix.get("contacts") or []]
    _sheet(wb.create_sheet("Government Contacts"), ["Name", "Role", "Email", "Phone"],
           contacts, [28, 28, 34, 18])

    legend = wb.create_sheet("How scores work")
    legend.append([f"{company} — {solicitation or ''} {title or ''}".strip()])
    legend["A1"].font = Font(bold=True, size=12)
    legend.append([])
    legend.append(["Score", "Meaning"])
    for k in sorted(RUBRIC, reverse=True):
        legend.append([k, RUBRIC[k]])
        legend.cell(row=legend.max_row, column=1).fill = PatternFill("solid", fgColor=_SCORE_FILL[k])
    legend.append([])
    legend.append(["Every piece of proof was checked against its own file in your library. A score "
                   "can never be higher than its checked proof allows."])
    legend.column_dimensions["A"].width = 10
    legend.column_dimensions["B"].width = 80

    buf = io.BytesIO()
    wb.save(buf)
    return clean(buf.getvalue(), company)
