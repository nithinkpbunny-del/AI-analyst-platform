"""Writing the briefing from its facts (BUILD_SPEC 13.3).

* **The AI writes it** in one small call (``write_briefing`` tool) from the compact facts only: no rows, no
  personal data. It follows the daily-briefing playbook: a subject line with the single most important item;
  sections Today's alerts, Unusual numbers, Suggested changes (₹ impact per R9), Working well, Goals (M15), or a
  short Business health summary when nothing is notable. Every figure carries its result id, and the number check (R14) removes any
  item with a figure that is not in a result. Identical facts reuse the words already written (Redis, one day),
  so two people with the same access cost one call.
* **Code writes it** when AI cannot be used (no key, quota, an error, an unusable answer): plain sentences from the
  same facts, with the same citations. A briefing is never lost because of the AI.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent import provenance
from app.agent.prompts import playbook_text
from app.briefings.facts import Facts
from app.exports.charts import format_value
from app.llm.base import LLMClient, LLMError, LLMResponse, ToolSpec, UserMessage

logger = logging.getLogger(__name__)

SECTIONS = ("alerts", "unusual", "suggested", "growth", "goals", "risks", "tasks", "health")
MAX_ITEMS = 3
MAX_TEXT = 400
CACHE_PREFIX = "briefing:words:"
CACHE_SECONDS = 24 * 3600
OUTPUT_TOKENS = 1500

WRITE_TOOL = ToolSpec(
    name="write_briefing",
    description=(
        "Write the morning briefing. Every figure must be copied from the facts and followed by its result id in "
        "brackets, e.g. '₹1.2 L [r2]' or '-58.6% [r3]'. Amounts in rupees with lakh/crore (₹8,450, ₹12.4 L, "
        "₹1.24 Cr). At most 3 short items per section; leave a section empty when there is nothing for it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Email subject: the single most important item, under 80 characters.",
            },
            "headline": {
                "type": "string",
                "description": "One sentence with the single most important figure.",
            },
            "key_figure": {
                "type": "string",
                "description": "The most important figure exactly as written in the headline.",
            },
            **{
                key: {"type": "array", "items": {"type": "string"}}
                for key in ("alerts", "unusual", "suggested", "growth", "goals", "risks", "health")
            },
        },
        "required": ["subject", "headline"],
    },
)

RULES = """You write the morning briefing for {company}, a {industry} business in India, as a senior business
analyst. Write in plain English for the owner (answers in other languages come later).

Rules:
- Use only the facts given. Never invent or compute a number; copy each figure from the facts and cite it with its
  result id in brackets right after it, e.g. "₹1.2 L [r2]".
- alerts: urgent items (an unusual drop on the latest day, a connection that stopped, data that stopped arriving).
- unusual: other unusual numbers, largest rupee change first.
- suggested: at most 2 concrete changes that follow from the findings. Give a rupee impact only when it is in the
  facts (upside_next_period) and it is material: at least {threshold_inr} or {threshold_pct}% of the monthly figure.
- growth: segments that grew several periods in a row; say how much came from repeat customers when given.
- goals: one item per goal in the facts: actual so far against the target and where it is heading by the end of
  its period. Say plainly when a goal is at_risk or behind (this is the early warning) and what is needed per day.
- risks: data-quality issues and other risks in the facts.
- health: only when notable is false: 1-2 sentences on the main metrics and their change.
- Say when the data is old (data_as_of is before yesterday).
- Be brief: at most 3 items per section, one sentence each.

{playbook}"""


@dataclass
class Written:
    subject: str
    headline: str
    key_figure: str | None
    sections: dict[str, list[str]]
    by: str  # "ai" | "code"
    notes: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ AI
def cache_key(facts: Facts, language: str) -> str:
    raw = json.dumps({"facts": facts.compact(), "language": language}, sort_keys=True, default=str)
    return CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()


def write_with_ai(
    client: LLMClient,
    facts: Facts,
    *,
    on_response: Callable[[LLMResponse], None] = lambda _r: None,
) -> Written | None:
    """One model call. ``None`` when the answer cannot be used (the caller falls back to code)."""
    system = RULES.format(
        company=facts.org_name,
        industry=facts.industry or "small or medium",
        threshold_inr=format_value(facts.impact_threshold_inr, "INR"),
        threshold_pct=f"{facts.impact_threshold_pct:g}",
        playbook=playbook_text("daily_briefing"),
    )
    prompt = "Facts (JSON):\n" + json.dumps(facts.compact(), ensure_ascii=False, default=str)
    try:
        response = client.generate(system, [UserMessage(prompt)], [WRITE_TOOL], OUTPUT_TOKENS)
    except LLMError as error:
        logger.info("briefing: AI unavailable (%s)", error.kind)
        return None
    on_response(response)
    call = next((c for c in response.tool_calls if c.name == WRITE_TOOL.name), None)
    if call is None or not isinstance(call.input, dict):
        return None
    raw = call.input
    headline = _text(raw.get("headline"))
    if not headline:
        return None
    sections = {key: _items(raw.get(key)) for key in SECTIONS if key not in ("tasks",)}
    written = Written(
        subject=_text(raw.get("subject"), 120) or headline[:120],
        headline=headline,
        key_figure=_text(raw.get("key_figure"), 40) or None,
        sections=sections,
        by="ai",
    )
    return check(written, facts)


def check(written: Written, facts: Facts) -> Written | None:
    """The number check (R14): figures are verified against the results; items with unsupported figures are
    removed. An unsupported headline makes the whole text unusable (the code version is used instead)."""
    report = provenance.NumberCheck()
    headline = provenance.check_text(written.headline, facts.store, report)
    if report.unsupported:
        return None
    written.headline = headline
    removed = 0
    for key, items in written.sections.items():
        kept = []
        for item in items:
            item_report = provenance.NumberCheck()
            text = provenance.check_text(item, facts.store, item_report)
            if item_report.unsupported:
                removed += 1
                continue
            kept.append(text)
        written.sections[key] = kept
    if written.key_figure and written.key_figure not in written.headline:
        written.key_figure = None
    if removed:
        written.notes.append({"code": "numbers_removed", "detail": str(removed)})
    return written


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def _items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v) for v in value) if t][:MAX_ITEMS]


# ---------------------------------------------------------------------------------------------- code
def _pct(value: Any) -> str:
    return f"{value:+.1f}%" if isinstance(value, int | float) else "–"  # noqa: RUF001


def _label(metric: Any) -> str:
    return {"aov": "Average order value", "revenue": "Revenue", "orders": "Orders"}.get(
        str(metric), str(metric).replace("_", " ").capitalize()
    )


def _when(day: str) -> str:
    from datetime import date

    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return day
    return f"{d.day} {d:%b}"


def write_with_code(facts: Facts) -> Written:
    """Plain sentences from the facts, each figure cited (the fallback, and the shape tests rely on)."""
    sections: dict[str, list[str]] = {key: [] for key in SECTIONS}
    for item in facts.alerts:
        if item.get("kind") == "connection":
            sections["alerts"].append(
                f"{item['connection']} stopped sharing data. An Owner can reconnect it under Connections."
            )
        elif item.get("kind") == "stale":
            sections["alerts"].append(
                f"{item['table']} has no data after {_when(item['last_day'])}."
            )
        else:
            sections["alerts"].append(_unusual_sentence(item))
    for item in facts.unusual:
        if item not in facts.alerts:
            sections["unusual"].append(_unusual_sentence(item))
    for item in facts.growth:
        rid = item["result_id"]
        text = (
            f"{item['segment']}: {_label(item['metric']).lower()} rose {item['periods']} months in a row, "
            f"from {format_value(item['start_value'], item.get('unit'))} [{rid}] to "
            f"{format_value(item['end_value'], item.get('unit'))} [{rid}]"
        )
        repeat = item.get("repeat_share_of_increase_pct")
        if isinstance(repeat, int | float):
            text += f", {repeat:.0f}% [{rid}] of it from repeat customers"
        sections["growth"].append(text + ".")
    if facts.growth:
        top = facts.growth[0]
        sections["suggested"].append(
            f"Back what is working in {top['segment']}: keep it in stock and in your marketing while it grows."
        )
    sections["goals"] = goal_sentences(facts.goals)
    for item in facts.risks:
        if item.get("kind") == "quality":
            sections["risks"].append(f"{item['table']}: {item.get('message') or item.get('issue')}")
    for task in facts.tasks:
        sections["tasks"].append(f"Your analysis is ready: {task['title']}")
    week = [k for k in facts.kpis if k["span"] == "week"]
    health = [
        f"{_label(k['metric'])} in the last 7 days: {format_value(k['current'], k['unit'])} [{k['result_id']}], "
        f"{_pct(k['change_pct'])} [{k['result_id']}] on the week before."
        for k in week[:2]
        if k.get("current") is not None
    ]
    if not facts.notable:
        sections["health"] = health
    headline, key = _headline(facts, sections, health)
    subject = provenance.CITATION.sub("", headline).strip()[:120]
    return Written(
        subject=subject or "Your morning briefing",
        headline=headline,
        key_figure=key,
        sections={k: v[:MAX_ITEMS] for k, v in sections.items()},
        by="code",
    )


def _unusual_sentence(item: dict[str, Any]) -> str:
    rid = item["result_id"]
    unit = "INR" if item.get("metric") in ("revenue", "aov") else None
    where = (
        "" if item.get("series") in (None, "", "all", "Total", "total") else f" ({item['series']})"
    )
    return (
        f"{_label(item['metric'])}{where} on {_when(item['bucket'])}: "
        f"{format_value(item.get('value'), unit)} [{rid}], {_pct(item.get('change_pct'))} [{rid}] against a usual "
        f"{format_value(item.get('expected'), unit)} [{rid}]."
    )


def _headline(
    facts: Facts, sections: dict[str, list[str]], health: list[str]
) -> tuple[str, str | None]:
    for key in ("alerts", "unusual", "growth"):
        if sections[key]:
            return sections[key][0], _first_figure(sections[key][0])
    off_track = [g for g in facts.goals if g.get("status") in ("at_risk", "behind")]
    if off_track:
        first = goal_sentences(off_track)[0]
        return first, _first_figure(first)
    if health:
        return health[0], _first_figure(health[0])
    return "No data arrived for this briefing yet.", None


def _first_figure(text: str) -> str | None:
    found = provenance.figures(text)
    return found[0].text if found else None


# ----------------------------------------------------------------------------------------------- goals
GOAL_WORDS = {
    "on_track": "On track.",
    "at_risk": "At risk: it may fall a little short.",
    "behind": "Behind: at this pace it will fall short.",
    "achieved": "Achieved.",
    "missed": "Missed.",
}


def period_text(start: str, end: str) -> str:
    """A whole calendar month as "November 2026", any other period as "1 Oct to 31 Dec 2026"."""
    from calendar import monthrange
    from datetime import date

    try:
        a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        return f"{start} to {end}"
    if (
        a.day == 1
        and a.year == b.year
        and a.month == b.month
        and b.day == monthrange(b.year, b.month)[1]
    ):
        return f"{a:%B %Y}"
    return f"{a.day} {a:%b} to {b.day} {b:%b %Y}"


def goal_label(goal: dict[str, Any]) -> str:
    name = goal.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"{_label(goal.get('metric'))} goal"


def goal_sentences(goals: list[dict[str, Any]]) -> list[str]:
    """One cited sentence per goal; every figure is a value of the goal_progress result."""
    out = []
    for goal in goals:
        rid, unit = goal["result_id"], goal.get("unit")
        status = str(goal.get("status"))
        if status not in GOAL_WORDS:
            continue
        when = period_text(str(goal.get("period_start")), str(goal.get("period_end")))
        target = f"{format_value(goal.get('target'), unit)} [{rid}]"
        actual = f"{format_value(goal.get('actual'), unit)} [{rid}]"
        head = f"{goal_label(goal)} ({'at most ' if goal.get('direction') == 'at_most' else ''}{target}, {when})"
        if status in ("achieved", "missed") and goal.get("method") == "final":
            text = f"{head}: ended at {actual}. {GOAL_WORDS[status]}"
        elif status == "achieved":
            text = f"{head}: {actual} already. {GOAL_WORDS[status]}"
        elif status == "missed":
            text = f"{head}: {actual} so far, past the limit. {GOAL_WORDS[status]}"
        else:
            projection = format_value(goal.get("projection"), unit)
            pct = goal.get("projected_pct_of_target")
            share = f", {pct:.0f}% [{rid}] of the target" if isinstance(pct, int | float) else ""
            text = f"{head}: {actual} so far, heading for {projection} [{rid}]{share}. {GOAL_WORDS[status]}"
            need = goal.get("needed_per_day")
            if status in ("at_risk", "behind") and isinstance(need, int | float) and need > 0:
                text += f" {format_value(need, unit)} [{rid}] a day is needed from here."
        out.append(text)
    return out
