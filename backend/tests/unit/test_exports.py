"""PDF and Excel exports (BUILD_SPEC X3): the document shape, source marks, the haldi mark, charts, safety."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from app.exports import charts, document, excel, pdf, text

LOCALES = Path(__file__).resolve().parents[3] / "frontend" / "src" / "locales" / "en.ts"

ANSWER: dict[str, Any] = {
    "lead": "South revenue fell -17.7% [r2] in Q3 while every other region grew.",
    "key_figure": "-17.7%",
    "evidence": [
        "Repeat customers in the South ordered 45% [r3] less often.",
        "New customers held steady.",
    ],
    "why_it_matters": "The South is 30% [r2] of revenue.",
    "recommendation": {"text": "Review the Electronics price hike.", "impact_inr": 120000.0},
    "data_notes": ["October data is incomplete."],
    "notes": [
        {"code": "dictionary_not_confirmed"},
        {"code": "data_ends", "detail": "2026-09-30:Q4"},
    ],
    "next_questions": ["Which products drove it?"],
    "charts": [
        {
            "chart_id": "c1",
            "type": "bar",
            "title": "South fell while others grew",
            "x": {"name": "region", "values": ["North", "South"]},
            "series": [
                {"name": "Q2", "data": [100000, 90000]},
                {"name": "Q3", "data": [110000, 74000]},
            ],
            "result_id": "r2",
            "unit": "INR",
        }
    ],
    "cited": ["r2", "r3"],
}
PROVENANCE = {
    "r1": {
        "result_id": "r1",
        "tool": "list_sources",
        "tables": [],
        "inputs": {},
        "sql": [],
        "row_count": 0,
    },
    "r2": {
        "result_id": "r2",
        "tool": "driver_analysis",
        "tables": [
            {"table": "orders", "name": "Orders", "connection": "Shopify", "connection_id": "x"}
        ],
        "inputs": {"metric": "revenue"},
        "sql": ["SELECT region, SUM(total_price) FROM orders GROUP BY 1"],
        "row_count": 4,
        "fetched_at": "2026-11-12T09:00:00+00:00",
        "data": {
            "columns": ["region", "change_pct"],
            "rows": [["South", -17.7], ["=cmd|' /C calc'!A0", 1]],
            "total_rows": 2,
        },
    },
    "r3": {
        "result_id": "r3",
        "tool": "kpi_summary",
        "tables": [],
        "inputs": {},
        "sql": [],
        "row_count": 1,
    },
}


def _doc() -> document.Document:
    return document.from_answer(
        title="Why did South fall?",
        subtitle="Answer from Analyst",
        org_name="UrbanCart",
        timezone="Asia/Kolkata",
        created_at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
        question="Why did South fall?",
        answer=ANSWER,
        provenance=PROVENANCE,
        quality=[{"table": "orders", "name": "Orders", "message": "The week of 9 Mar has 4 rows."}],
    )


class TestDocument:
    def test_sources_are_numbered_in_citation_order(self) -> None:
        doc = _doc()
        assert [(s.number, s.result_id) for s in doc.sources] == [(1, "r2"), (2, "r3")]
        assert doc.sources[0].tables == "Orders (Shopify)"
        assert doc.sources[0].fetched_at == "12 Nov 2026, 14:30"  # IST
        assert doc.sources[0].query.startswith("SELECT region")

    def test_citations_become_source_numbers(self) -> None:
        doc = _doc()
        assert (
            doc.plain(ANSWER["lead"])
            == "South revenue fell -17.7% [1] in Q3 while every other region grew."
        )

    def test_notes_are_sentences(self) -> None:
        doc = _doc()
        assert any(n.startswith("The business dictionary is not confirmed") for n in doc.notes)
        assert "The data ends on 2026-09-30, so the period Q4 is incomplete." in doc.notes
        assert "Orders: The week of 9 Mar has 4 rows." in doc.notes
        assert "October data is incomplete." in doc.notes

    def test_cited_tables_come_along(self) -> None:
        doc = _doc()
        assert len(doc.tables) == 1 and doc.tables[0].columns == ["region", "change_pct"]


class TestPdf:
    def test_html_has_the_mark_source_marks_and_sources(self) -> None:
        html = pdf.html(_doc())
        assert '<span class="mark">-17.7%</span>' in html
        assert "<sup>1</sup>" in html and "<sup>2</sup>" in html
        assert "Orders (Shopify)" in html and "<svg" in html
        assert html.count('class="mark"') == 1  # one haldi mark per document (BUILD_SPEC 11a.5)

    def test_text_from_the_model_is_escaped(self) -> None:
        doc = _doc()
        doc.lead = '<img src="http://evil.example/x.png"> fell 5% [r2]'
        html = pdf.html(doc)
        assert "<img" not in html and "&lt;img" in html

    def test_renders_a_pdf_and_never_fetches(self) -> None:
        doc = _doc()
        data = pdf.render(doc)
        assert data.startswith(b"%PDF")
        with pytest.raises(ValueError):
            pdf._no_fetch("http://example.com/x.png")

    def test_hindi_text_renders(self) -> None:
        doc = _doc()
        doc.lead = "दक्षिण में बिक्री -17.7% [r2] घटी"
        assert pdf.render(doc).startswith(b"%PDF")


class TestExcel:
    def test_sheets_and_formula_safety(self) -> None:
        data = excel.render(_doc())
        book = openpyxl.load_workbook(io.BytesIO(data))
        assert book.sheetnames[:2] == ["Summary", "Sources"]
        assert any(name.startswith("Source 1") for name in book.sheetnames)
        assert "Chart 1" in book.sheetnames
        table = book[next(n for n in book.sheetnames if n.startswith("Source 1"))]
        cells = [c.value for row in table.iter_rows() for c in row]
        assert "'=cmd|' /C calc'!A0" in cells  # stored as text, never as a formula
        summary = [c.value for row in book["Summary"].iter_rows() for c in row if c.value]
        assert "South revenue fell -17.7% [1] in Q3 while every other region grew." in summary

    @pytest.mark.parametrize("value", ["=1+1", "+1", "-1+2", "@SUM(A1)", "\tx"])
    def test_safe_prefixes_formula_text(self, value: str) -> None:
        assert excel.safe(value) == "'" + value

    def test_numbers_stay_numbers(self) -> None:
        assert excel.safe(-17.7) == -17.7 and excel.safe(None) is None


class TestCharts:
    def test_svg_and_summary(self) -> None:
        spec = ANSWER["charts"][0]
        svg = charts.svg(spec)
        assert svg.startswith("<svg") and svg.count("<rect") >= 4
        assert (
            charts.summary(spec)
            == "Q2: ₹1.0 L (North) to ₹90,000 (South); Q3: ₹1.1 L (North) to ₹74,000 (South)"
        )

    @pytest.mark.parametrize("kind", ["line", "stacked_bar", "horizontal_bar"])
    def test_every_kind_draws(self, kind: str) -> None:
        spec = {**ANSWER["charts"][0], "type": kind}
        assert "<svg" in charts.svg(spec)

    def test_labels_are_escaped(self) -> None:
        spec = {**ANSWER["charts"][0], "x": {"name": "x", "values": ["<script>", "b"]}}
        assert "<script>" not in charts.svg(spec)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (8450, "₹8,450"),
            (1240000, "₹12.4 L"),
            (12400000, "₹1.24 Cr"),
            (-150000, "-₹1.5 L"),
            (99999, "₹99,999"),
        ],
    )
    def test_format_inr(self, value: float, expected: str) -> None:
        assert charts.format_inr(value) == expected

    def test_indian_grouping(self) -> None:
        assert charts.indian_grouping(12345678) == "1,23,45,678"


def test_note_sentences_match_the_web_app() -> None:
    """``app.exports.text.NOTES`` copies ``ask.notes`` in locales/en.ts (D41): every key must exist there."""
    source = LOCALES.read_text(encoding="utf-8")
    block = source[source.index("    notes: {\n      dictionary_not_confirmed") :]
    block = block[: block.index("\n    },")]
    web_keys = set(re.findall(r"^\s{6}([a-z_]+):", block, flags=re.MULTILINE))
    assert set(text.NOTES) <= web_keys
    assert web_keys - set(text.NOTES) <= {"unknown", "briefing_data_old", "briefing_no_metrics"}


def test_unknown_notes_still_read() -> None:
    assert text.note_sentence({"code": "something_new", "detail": "x"}) == "Note: something new (x)"
