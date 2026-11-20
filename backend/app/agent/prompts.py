"""The system prompt (BUILD_SPEC 7.4, 7.5): role + lens + dictionary summary + rules + answer format, plus the
playbooks that fit the question.

Lenses live in ``prompts/lenses/*.md`` and playbooks in ``playbooks/*.md``. Playbooks are chosen by plain word
matching on the question (no AI call): at most two, the first match first.
"""

from __future__ import annotations

import re
from datetime import date
from functools import cache
from pathlib import Path
from string import Template

from app import templates_industry
from app.agent.access import AccessContext, VisibleDictionary
from app.models.chat import LENSES

HERE = Path(__file__).resolve().parent
MAX_PLAYBOOKS = 2

# Playbook file -> words that call for it, most specific first (``choose_playbooks`` keeps the first few).
# ``daily_briefing`` also guides the morning briefing writer (M10) and "health check" questions.
PLAYBOOK_WORDS: dict[str, re.Pattern[str]] = {
    "what_if": re.compile(
        r"\b(what if|what would happen|if we|if i|suppose|simulat\w*|scenario|"
        r"should (we|i) (raise|increase|cut|lower|reduce|drop|stop|double))\b",
        re.I,
    ),
    "why_changed": re.compile(
        r"\b(why|reason|cause|drop(ped)?|fell|fall(en|ing)?|declin\w*|decreas\w*|went (up|down)|increas\w*|"
        r"grew|spike|dip|explain)\b",
        re.I,
    ),
    "anomaly_review": re.compile(
        r"\b(unusual|anomal\w*|odd|strange|outage|spike|weird|sudden)\b", re.I
    ),
    "forecasting": re.compile(
        r"\b(forecast\w*|predict\w*|project\w*|next (month|quarter|year|week)|expect)\b", re.I
    ),
    "campaign_efficiency": re.compile(
        r"\b(roas|cpa|ctr|campaigns?|ads?|advertis\w*|marketing spend|channels?|spend)\b", re.I
    ),
    "customer_retention": re.compile(
        r"\b(retention|retain|repeat|churn\w*|cohorts?|loyal\w*|rfm|segments?|lapsed|at risk)\b",
        re.I,
    ),
    "profitability": re.compile(
        r"\b(margin\w*|profit\w*|discount\w*|cost of|cogs|leakage)\b", re.I
    ),
    "budget_variance": re.compile(
        r"\b(budget\w*|over ?spen\w*|variance|expenses?|overrun)\b", re.I
    ),
    "goal_tracking": re.compile(r"\b(targets?|goals?|on track|behind plan|ahead of plan)\b", re.I),
    "daily_briefing": re.compile(
        r"\b(health ?check|briefing|overview|how (are|is) (we|the business) doing|what'?s working|"
        r"working well|growth signals?|growing)\b",
        re.I,
    ),
}


@cache
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def lens_text(lens: str) -> str:
    return _read(HERE / "prompts" / "lenses" / f"{lens if lens in LENSES else 'business'}.md")


def choose_playbooks(question: str) -> list[str]:
    return [name for name, words in PLAYBOOK_WORDS.items() if words.search(question)][
        :MAX_PLAYBOOKS
    ]


def playbook_text(name: str) -> str:
    return _read(HERE / "playbooks" / f"{name}.md")


def _section(text: str) -> str:
    return f"\n{text}\n" if text else ""


def _amount(value: float) -> str:
    if value >= 1e7:
        return f"{value / 1e7:g} crore"
    if value >= 1e5:
        return f"{value / 1e5:g} lakh"
    return f"{value:,.0f}"


def dictionary_summary(visible: VisibleDictionary) -> str:
    """Tables with their columns, metrics and joins, compactly. Meanings are shown where someone wrote one."""
    d = visible.dictionary
    if not d.datasets:
        return "(No tables are connected that you may use.)"
    lines: list[str] = []
    for key, entry in d.datasets.items():
        source = visible.tables[key].connection_name
        head = f"- table `{key}` ({entry.name}, from {source})"
        if entry.meaning:
            head += f": {entry.meaning}"
        if entry.time_column:
            head += f". Date column: {entry.time_column}"
        lines.append(head)
        columns = []
        for name, column in entry.columns.items():
            tags: list[str] = [column.role]
            if column.unit:
                tags.append(column.unit)
            text = f"{name} [{', '.join(tags)}]"
            if column.source != "code" and column.meaning:
                text += f" {column.meaning}"
            if column.synonyms:
                text += f" (also: {', '.join(column.synonyms[:4])})"
            columns.append(text)
        lines.append("  columns: " + "; ".join(columns))
    if d.metrics:
        lines.append("Metrics (DuckDB expressions over their table):")
        for name, metric in d.metrics.items():
            text = f"- `{name}` = {metric.expr}"
            if metric.dataset:
                text += f" on {metric.dataset}"
            if metric.unit:
                text += f" [{metric.unit}]"
            if metric.meaning:
                text += f": {metric.meaning}"
            lines.append(text)
    if d.relationships:
        lines.append("Joins: " + "; ".join(f"{r.left} = {r.right}" for r in d.relationships))
    if visible.unavailable_metrics:
        lines.append(
            "Not available now (their table was removed): " + ", ".join(visible.unavailable_metrics)
        )
    return "\n".join(lines)


def system_prompt(
    *,
    access: AccessContext,
    visible: VisibleDictionary,
    lens: str,
    question: str,
    today: date,
    confirmed: bool,
    max_steps: int,
) -> tuple[str, list[str]]:
    """The system prompt and the playbooks it includes."""
    chosen = choose_playbooks(question)
    playbooks = "".join(f"\n\n{playbook_text(name)}" for name in chosen)
    text = Template(_read(HERE / "prompts" / "system.md")).substitute(
        org_name=access.org_name,
        industry=templates_industry.get(access.industry).business,
        industry_notes=_section(templates_industry.prompt_notes(access.industry, chosen)),
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        timezone=access.timezone,
        lens=lens_text(lens),
        threshold_inr=_amount(access.impact_threshold_inr),
        threshold_pct=f"{access.impact_threshold_pct:g}",
        max_steps=max_steps,
        dictionary_note=""
        if confirmed
        else " (not yet confirmed by the Owner: meanings may be wrong; the answer will say so)",
        dictionary=dictionary_summary(visible),
        playbooks=playbooks,
    )
    return text, chosen
