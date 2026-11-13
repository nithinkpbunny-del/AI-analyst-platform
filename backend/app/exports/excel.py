"""Excel export (BUILD_SPEC X3, 3: openpyxl).

Sheets: **Summary** (title, question, answer, evidence, recommendation, notes, next questions), **Sources** (each
numbered source with its tables, fetch time, rows and query), one sheet per **data table** a source showed, and the
data behind each **chart**. Cells hold values, never formulas: any text starting with ``=``, ``+``, ``-``, ``@``
(or a tab/CR) is stored with a leading apostrophe so a spreadsheet cannot run it (formula injection).
"""

from __future__ import annotations

import io
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from app.exports.document import Document

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
INK = "1E1C3A"
NEEL = "3A36A8"


def safe(value: Any) -> Any:
    if isinstance(value, str):
        text = value[:32_000]
        return "'" + text if text.startswith(_FORMULA_START) else text
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return value
    return safe(str(value))


def _sheet_name(name: str, used: set[str]) -> str:
    base = _BAD_SHEET_CHARS.sub(" ", name).strip()[:28] or "Sheet"
    candidate, n = base, 2
    while candidate.lower() in used:
        candidate = f"{base[:25]} {n}"
        n += 1
    used.add(candidate.lower())
    return candidate


def _row(sheet: Worksheet, *values: Any, bold: bool = False) -> None:
    sheet.append([safe(v) for v in values])
    if bold:
        for cell in sheet[sheet.max_row]:
            cell.font = Font(bold=True, color=INK)


def render(document: Document) -> bytes:
    book = Workbook()
    summary = book.active
    assert summary is not None
    summary.title = "Summary"
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 100
    summary.append([safe(document.title)])
    summary["A1"].font = Font(bold=True, size=14, color=NEEL)
    _row(summary, "Workspace", document.org_name)
    _row(summary, "Made", document.created_at)
    if document.subtitle:
        _row(summary, "About", document.subtitle)
    if document.question:
        _row(summary, "Question", document.question)
    if document.lead:
        _row(summary, "Answer", document.plain(document.lead), bold=True)
    if document.key_figure:
        _row(summary, "Key figure", document.key_figure)
    for section in document.sections:
        summary.append([])
        _row(summary, section.heading, bold=True)
        for item in section.items:
            _row(summary, "", document.plain(item))
    if document.recommendation:
        summary.append([])
        _row(summary, "Recommendation", document.plain(document.recommendation), bold=True)
    if document.notes:
        summary.append([])
        _row(summary, "Data notes", bold=True)
        for note in document.notes:
            _row(summary, "", note)
    if document.next_questions:
        summary.append([])
        _row(summary, "Next questions", bold=True)
        for q in document.next_questions:
            _row(summary, "", q)
    for row in summary.iter_rows(min_col=2, max_col=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    used = {"summary"}
    if document.sources:
        sources = book.create_sheet(_sheet_name("Sources", used))
        _row(sources, "#", "Data", "Fetched", "Rows", "Computed with", "Query", bold=True)
        for s in document.sources:
            _row(sources, s.number, s.tables, s.fetched_at, s.rows, s.tool, s.query)
        for column, width in zip("ABCDEF", (5, 40, 20, 10, 18, 80), strict=True):
            sources.column_dimensions[column].width = width
    for table in document.tables:
        sheet = book.create_sheet(_sheet_name(table.name, used))
        _row(sheet, *table.columns, bold=True)
        for values in table.rows:
            _row(sheet, *values)
        if table.total_rows > len(table.rows):
            _row(sheet, f"Showing {len(table.rows)} of {table.total_rows} rows.")
    for n, chart in enumerate(document.charts, start=1):
        sheet = book.create_sheet(_sheet_name(f"Chart {n}", used))
        _row(sheet, chart.get("title") or f"Chart {n}", bold=True)
        series = chart.get("series") or []
        _row(
            sheet,
            (chart.get("x") or {}).get("name") or "",
            *[s.get("name") for s in series],
            bold=True,
        )
        for i, x in enumerate((chart.get("x") or {}).get("values") or []):
            _row(
                sheet,
                x,
                *[
                    (s.get("data") or [None] * (i + 1))[i] if i < len(s.get("data") or []) else None
                    for s in series
                ],
            )
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
