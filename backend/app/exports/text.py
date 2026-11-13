"""Sentences for the system's data notes in PDF, Excel and email (the web app has the same ones in
``frontend/src/locales/en.ts`` under ``ask.notes``; ``tests/unit/test_exports.py`` keeps the two in step).

Answers are in English for now (DECISIONS D41), so the documents are too.
"""

from __future__ import annotations

from typing import Any

NOTES: dict[str, str] = {
    "dictionary_not_confirmed": (
        "The business dictionary is not confirmed yet, so column meanings may be wrong. An Owner can confirm it "
        "under Dictionary."
    ),
    "metrics_unavailable": (
        "These metrics could not be used because their table was removed: {metrics}. An Owner can fix them under "
        "Dictionary."
    ),
    "data_ends": "The data ends on {date}, so the period {period} is incomplete.",
    "data_starts": "The data starts on {date}, so the period {period} is incomplete.",
    "no_data": "There is no data for {period}.",
    "partial_last_bucket": "The last {detail} in the chart is not complete yet.",
    "partial_last_bucket_left_out": "The incomplete last period ({detail}) was left out of the forecast.",
    "unequal_periods": "The two periods have different numbers of days ({detail}).",
    "no_customer_column": "No customer column was found, so new and repeat customers could not be split.",
    "not_additive": "This metric is a ratio, so its parts do not add up to the total.",
    "attribution_limits": (
        "Campaign revenue is as each platform reports it; platforms count the same sale differently."
    ),
    "what_if_assumptions": (
        "This is a projection from your past, not a promise. It assumes nothing else changes."
    ),
    "what_if_default_assumption": (
        "Part of this simulation uses a typical figure for your kind of business, because your own history could "
        "not settle it."
    ),
    "nothing_to_check": "There was not enough history to look for unusual values.",
    "no_revenue_column": "No revenue column was found in the campaign data, so ROAS is not shown.",
    "skipped": "A table could not be used: {detail}.",
    "numbers_removed": "Some figures could not be checked against the data and were left out.",
    "restarted": "The AI model changed while answering, so the question was answered again.",
    "briefing_data_old": "The newest data is from {detail}, so this briefing covers that day, not yesterday.",
    "briefing_no_metrics": (
        "No metric with a date is set up yet, so there is nothing to brief on. An Owner can add one under "
        "Dictionary."
    ),
    "recommendation_not_material": (
        "A recommendation was left out because its impact is below your materiality threshold."
    ),
}


def note_sentence(note: dict[str, Any]) -> str:
    """A system note (``{"code": ..., "detail": ...}``) as a sentence."""
    code = str(note.get("code") or "")
    detail = str(note.get("detail") or "")
    template = NOTES.get(code)
    if template is None:
        return f"Note: {code.replace('_', ' ')}" + (f" ({detail})" if detail else "")
    parts = detail.split(":")
    values = {
        "detail": detail.replace(":", ", "),
        "date": parts[0] if parts else "",
        "period": parts[1] if len(parts) > 1 else "",
        "metrics": ", ".join(str(m) for m in note.get("metrics") or []),
    }
    return template.format(**values)


def quality_sentence(warning: dict[str, Any]) -> str:
    """A data-quality warning of a table, as a sentence (each issue carries its own English ``message``)."""
    name = warning.get("name") or warning.get("table") or "A table"
    message = str(warning.get("message") or str(warning.get("kind") or "issue").replace("_", " "))
    return f"{name}: {message}"
