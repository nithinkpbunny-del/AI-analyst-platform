"""Provenance of every tool result and the number check (BUILD_SPEC 7.7, R14, P4, P5).

* Every tool result gets a ``result_id`` (``r1``, ``r2``, ...) and a provenance record: the tool, the tables with
  their connection, dataset and fetch time, the filters or query, and the row count. The UI shows it behind each
  figure (SourceMark).
* The answer cites results after each figure: ``₹28.0 L [r3]``. ``check_numbers`` finds every figure in the
  answer and verifies it matches, within the rounding it is written with, a value in a cited result (or, when the
  model forgot the citation, in any result of the question; the citation is then added). Lakh/crore, thousands,
  percentages and "x" ratios are understood. Dates, years, quarters and small counting words ("top 3") are not
  figures.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

CITATION = re.compile(r"\s*\[(r\d+(?:\s*,\s*r\d+)*)\]")
_MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|"
    "september|october|november|december"
)
# Minus signs, dashes and the multiplication sign are what people write, so the pattern accepts them.
_NUMBER = re.compile(
    r"(?P<sign>[-−–+])?\s?(?P<rupee>₹|rs\.?\s?|inr\s?)?(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"  # noqa: RUF001
    r"(?P<unit>\s?(?:%|pp\b|percentage points?|lakh|lakhs|l\b|crore|crores|cr\b|k\b|thousand|x\b|×|times))?",  # noqa: RUF001
    re.IGNORECASE,
)
_DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}(?:-\d{{2}})?\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b|\bq[1-4]\b|\bh[12]\b|\bfy\s?\d{{2,4}}\b|"
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s(?:{_MONTHS})\b|\b(?:{_MONTHS})\.?\s\d{{1,2}}(?:st|nd|rd|th)?\b|"
    rf"\b(?:{_MONTHS})\.?\s\d{{4}}\b|\b\d{{1,2}}:\d{{2}}\b",
    re.IGNORECASE,
)
SMALL_COUNT = 12  # "top 3", "5 regions", "12 months": counting words, not figures from the data


@dataclass
class Provenance:
    result_id: str
    tool: str
    tables: list[dict[str, Any]]
    inputs: dict[str, Any]
    sql: list[str]
    row_count: int
    fetched_at: str | None = None  # the oldest fetch of the tables used

    def as_json(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "tool": self.tool,
            "tables": self.tables,
            "inputs": self.inputs,
            "sql": self.sql,
            "row_count": self.row_count,
            "fetched_at": self.fetched_at,
        }


@dataclass
class ResultStore:
    """Every result of one question, by id."""

    provenance: dict[str, Provenance] = field(default_factory=dict)
    values: dict[str, list[float]] = field(default_factory=dict)
    data: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(
        self,
        tool: str,
        content: dict[str, Any],
        *,
        tables: list[dict[str, Any]],
        inputs: dict[str, Any],
        sql: list[str],
        row_count: int,
    ) -> str:
        result_id = f"r{len(self.provenance) + 1}"
        fetched = [t["fetched_at"] for t in tables if t.get("fetched_at")]
        self.provenance[result_id] = Provenance(
            result_id, tool, tables, inputs, sql[-10:], row_count, min(fetched) if fetched else None
        )
        self.values[result_id] = list(numbers_in(content))
        self.data[result_id] = content
        return result_id

    def known(self, result_id: str) -> bool:
        return result_id in self.provenance


def numbers_in(value: Any) -> Iterator[float]:
    """Every number inside a result (nested dicts/lists), plus the usual derived forms (% from a share)."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int | float):
        if math.isfinite(float(value)):
            yield float(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from numbers_in(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            yield from numbers_in(item)
        return
    if isinstance(value, str):
        # Numbers inside strings, e.g. a label "Q3 2026" carries no figures we check; amounts in text do.
        return


# ----------------------------------------------------------------------------------------- the check
@dataclass(frozen=True)
class Figure:
    text: str  # as written, e.g. "₹12.4 L"
    start: int
    end: int
    candidates: tuple[float, ...]  # the values it may stand for
    tolerance: float
    cited: tuple[str, ...]


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def figures(text: str) -> list[Figure]:
    """The figures in a piece of answer text, each with the values it may mean and its rounding."""
    blocked = [(m.start(), m.end()) for m in _DATE.finditer(text)]
    blocked += [(m.start(), m.end()) for m in CITATION.finditer(text)]
    found: list[Figure] = []
    for match in _NUMBER.finditer(text):
        start, end = match.start("num"), match.end()
        if any(lo <= start < hi for lo, hi in blocked):
            continue
        begin = match.start()
        while begin < start and text[begin].isspace():
            begin += 1
        before = text[max(0, begin - 1) : begin]
        if before.isalpha() or before in ("_", "#", "/"):
            continue  # part of a word or an id ("Q3", "r12", "#1004")
        raw = match.group("num")
        unit = (match.group("unit") or "").strip().lower()
        rupee = bool(match.group("rupee"))
        number = float(raw.replace(",", ""))
        decimals = _decimals(raw)
        if not unit and not rupee and decimals == 0:
            if number <= SMALL_COUNT:
                continue
            if 1900 <= number <= 2100 and "," not in raw:
                continue  # a year
        step = 10.0 ** (-decimals)
        if unit in ("lakh", "lakhs", "l"):
            scale = 1e5
        elif unit in ("crore", "crores", "cr"):
            scale = 1e7
        elif unit in ("k", "thousand"):
            scale = 1e3
        else:
            scale = 1.0
        value = number * scale
        tolerance = step * scale / 2 + 1e-9 + abs(value) * 1e-6
        if unit in ("%", "pp", "percentage point", "percentage points"):
            candidates: tuple[float, ...] = (value, value / 100.0)
        else:
            candidates = (value,)
        cited_match = _nearby_citation(text, end)
        cited = tuple(c.strip() for c in cited_match.group(1).split(",")) if cited_match else ()
        found.append(Figure(text[begin:end].strip(), begin, end, candidates, tolerance, cited))
    return found


CITATION_REACH = 32  # "30 orders [r1]": a citation a few words after the figure still belongs to it


def _nearby_citation(text: str, end: int) -> re.Match[str] | None:
    """The citation right after a figure, or a few words later with no other figure or sentence end between."""
    found = CITATION.match(text, end)
    if found:
        return found
    window = text[end : end + CITATION_REACH]
    bracket = window.find("[")
    if bracket < 0 or any(ch.isdigit() or ch in ".;!?" for ch in window[:bracket]):
        return None
    return CITATION.match(
        text, end + bracket - (1 if bracket and window[bracket - 1] == " " else 0)
    )


def _matches(figure: Figure, values: Iterable[float]) -> bool:
    for value in values:
        for candidate in figure.candidates:
            # A share stored as a fraction (0.124) written as 12.4%: compare on the % scale.
            tolerance = (
                figure.tolerance if candidate == figure.candidates[0] else figure.tolerance / 100.0
            )
            if abs(abs(value) - abs(candidate)) <= tolerance:
                return True
    return False


@dataclass
class NumberCheck:
    unsupported: list[str] = field(default_factory=list)
    cited_added: int = 0
    unknown_citations: list[str] = field(default_factory=list)


def check_text(text: str, store: ResultStore, report: NumberCheck) -> str:
    """Check one text; returns the text with missing citations added where a figure matched a result."""
    out = text
    shift = 0
    for figure in figures(text):
        unknown = [c for c in figure.cited if not store.known(c)]
        report.unknown_citations += unknown
        cited = [c for c in figure.cited if store.known(c)]
        if cited and _matches(figure, (v for c in cited for v in store.values[c])):
            continue
        owner = next((rid for rid, vals in store.values.items() if _matches(figure, vals)), None)
        if owner is None:
            report.unsupported.append(figure.text)
            continue
        if not figure.cited:
            position = figure.end + shift
            out = out[:position] + f" [{owner}]" + out[position:]
            shift += len(owner) + 3
            report.cited_added += 1
    return out


def remove_unsupported(text: str, unsupported: list[str]) -> str:
    """Drop every sentence that still carries a figure no result supports."""
    if not unsupported:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not any(u in s for u in unsupported)]
    return " ".join(kept).strip()


def citations_in(text: str) -> set[str]:
    out: set[str] = set()
    for match in CITATION.finditer(text):
        out |= {c.strip() for c in match.group(1).split(",")}
    return out
