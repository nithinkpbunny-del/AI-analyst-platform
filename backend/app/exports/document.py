"""One document shape for every export (BUILD_SPEC X3): an answer, a long task's report or a briefing becomes a
``Document`` that the PDF and Excel writers render the same way.

Citations in the text (``₹28.0 L [r3]``) become numbered source marks (``[3]``) that point to the Sources list,
which carries each result's tables, connection, fetch time and query: the printed face of provenance (R14).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.exports.text import note_sentence, quality_sentence

CITATION = re.compile(r"\s*\[(r\d+(?:\s*,\s*r\d+)*)\]")
MAX_TABLE_ROWS = 200


@dataclass
class Source:
    number: int
    result_id: str
    tool: str
    tables: str
    fetched_at: str
    rows: int
    query: str


@dataclass
class DataTable:
    name: str
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int


@dataclass
class Section:
    heading: str
    items: list[str]


@dataclass
class Document:
    title: str
    subtitle: str
    org_name: str
    created_at: str
    lead: str = ""
    key_figure: str | None = None
    question: str | None = None
    sections: list[Section] = field(default_factory=list)
    recommendation: str | None = None
    charts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    tables: list[DataTable] = field(default_factory=list)

    def numbers(self) -> dict[str, int]:
        return {s.result_id: s.number for s in self.sources}

    def cite(self, text: str) -> list[tuple[str, list[int]]]:
        """``text`` split into pieces, each followed by the source numbers cited right after it."""
        numbers = self.numbers()
        out: list[tuple[str, list[int]]] = []
        last = 0
        for match in CITATION.finditer(text):
            ids = [i.strip() for i in match.group(1).split(",")]
            out.append((text[last : match.start()], [numbers[i] for i in ids if i in numbers]))
            last = match.end()
        out.append((text[last:], []))
        return out

    def plain(self, text: str) -> str:
        """``text`` with citations as ``[3]`` (Excel, plain-text email)."""
        return "".join(
            piece + "".join(f" [{n}]" for n in nums) for piece, nums in self.cite(text)
        ).strip()


def when(value: datetime | str | None, timezone: str) -> str:
    if value is None:
        return ""
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    local = stamp.astimezone(ZoneInfo(timezone))
    return f"{local.day} {local:%b %Y, %H:%M}"


def _sources(provenance: dict[str, Any], cited: list[str], timezone: str) -> list[Source]:
    order = [r for r in cited if r in provenance] or sorted(
        provenance, key=lambda r: int(r[1:]) if r[1:].isdigit() else 0
    )
    out = []
    for number, rid in enumerate(order, start=1):
        item = provenance[rid]
        tables = ", ".join(
            f"{t.get('name') or t.get('table')}"
            + (f" ({t['connection']})" if t.get("connection") else "")
            for t in item.get("tables") or []
        )
        sql = item.get("sql") or []
        out.append(
            Source(
                number=number,
                result_id=rid,
                tool=str(item.get("tool") or ""),
                tables=tables,
                fetched_at=when(item.get("fetched_at"), timezone),
                rows=int(item.get("row_count") or 0),
                query=str(sql[-1]) if sql else _inputs(item.get("inputs") or {}),
            )
        )
    return out


def _inputs(inputs: dict[str, Any]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in inputs.items())[:500]


def _tables(provenance: dict[str, Any], sources: list[Source]) -> list[DataTable]:
    out = []
    for source in sources:
        data = (provenance.get(source.result_id) or {}).get("data")
        if not isinstance(data, dict) or not data.get("columns"):
            continue
        out.append(
            DataTable(
                name=f"Source {source.number} ({source.tool})",
                columns=[str(c) for c in data["columns"]],
                rows=[list(r) for r in (data.get("rows") or [])[:MAX_TABLE_ROWS]],
                total_rows=int(data.get("total_rows") or 0),
            )
        )
    return out


def from_answer(
    *,
    title: str,
    subtitle: str,
    org_name: str,
    timezone: str,
    created_at: datetime,
    question: str | None,
    answer: dict[str, Any],
    provenance: dict[str, Any],
    quality: list[dict[str, Any]],
) -> Document:
    sources = _sources(provenance, list(answer.get("cited") or []), timezone)
    sections = [Section("Evidence", list(answer.get("evidence") or []))]
    if answer.get("why_it_matters"):
        sections.append(Section("Why it matters", [str(answer["why_it_matters"])]))
    rec = answer.get("recommendation") or None
    notes = [
        *[str(n) for n in answer.get("data_notes") or []],
        *[note_sentence(n) for n in answer.get("notes") or []],
        *[quality_sentence(q) for q in quality],
    ]
    return Document(
        title=title,
        subtitle=subtitle,
        org_name=org_name,
        created_at=when(created_at, timezone),
        lead=str(answer.get("lead") or ""),
        key_figure=answer.get("key_figure"),
        question=question,
        sections=[s for s in sections if s.items],
        recommendation=str(rec["text"]) if isinstance(rec, dict) and rec.get("text") else None,
        charts=list(answer.get("charts") or []),
        notes=list(dict.fromkeys(notes)),
        next_questions=list(answer.get("next_questions") or []),
        sources=sources,
        tables=_tables(provenance, sources),
    )


BRIEFING_HEADINGS = {
    "alerts": "Today's alerts",
    "unusual": "Unusual numbers",
    "suggested": "Suggested changes",
    "growth": "Working well",
    "goals": "Goals",
    "risks": "Upcoming risks",
    "tasks": "Finished long tasks",
    "health": "Business health",
}


def from_briefing(
    *,
    title: str,
    org_name: str,
    timezone: str,
    created_at: datetime,
    body: dict[str, Any],
    provenance: dict[str, Any],
    quality: list[dict[str, Any]],
) -> Document:
    sections = [
        Section(
            BRIEFING_HEADINGS.get(str(s.get("key")), str(s.get("key"))), list(s.get("items") or [])
        )
        for s in body.get("sections") or []
        if s.get("items")
    ]
    cited: list[str] = []
    for text in [str(body.get("headline") or ""), *[i for s in sections for i in s.items]]:
        for match in CITATION.finditer(text):
            cited += [i.strip() for i in match.group(1).split(",") if i.strip() not in cited]
    sources = _sources(provenance, cited, timezone)
    as_of = body.get("data_as_of")
    notes = [
        *[note_sentence(n) for n in body.get("notes") or []],
        *[quality_sentence(q) for q in quality],
    ]
    return Document(
        title=title,
        subtitle=f"Data up to {as_of}" if as_of else "",
        org_name=org_name,
        created_at=when(created_at, timezone),
        lead=str(body.get("headline") or ""),
        key_figure=body.get("key_figure"),
        sections=sections,
        notes=list(dict.fromkeys(notes)),
        sources=sources,
        tables=_tables(provenance, sources),
    )
