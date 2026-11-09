"""Estimated cost per call (BUILD_SPEC 7.8): USD per million tokens, standard paid tier, short prompts.

These are estimates for the usage meter, not invoices: free-tier keys cost nothing, long prompts and cached input
are priced differently, and providers change prices. Each model has a list of ``(effective_from, input, output)``
so announced price changes apply from their date; a call is priced at the rate of the day it was made.
Sources (checked 2026-11-09): Google AI Studio pricing page (aistudio.google.com/docs/pricing) and Anthropic's
model list. A model with no entry gets no estimate (shown as "not known") rather than a guessed number.
The longest matching prefix wins, so ``gemini-2.5-flash-lite`` is not priced as ``gemini-2.5-flash``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.llm.base import Usage

Price = tuple[Decimal, Decimal]  # (input, output) USD per 1M tokens
_ALWAYS = date(2000, 1, 1)


def _p(input_usd: str, output_usd: str, since: date = _ALWAYS) -> tuple[date, Decimal, Decimal]:
    return since, Decimal(input_usd), Decimal(output_usd)


# model id prefix -> price periods, oldest first
PRICES: dict[str, list[tuple[date, Decimal, Decimal]]] = {
    # Google Gemini (paid tier; prompts up to 200k tokens; thinking tokens are billed as output)
    "gemini-4-argon": [_p("2.00", "10.00"), _p("4.00", "20.00", date(2026, 12, 1))],
    "gemini-3.8-flash": [_p("0.75", "3.75"), _p("1.50", "7.50", date(2027, 1, 1))],
    "gemini-3.6-flash": [_p("0.75", "3.75"), _p("1.50", "7.50", date(2027, 1, 1))],
    "gemini-3.5-flash-lite": [_p("0.30", "2.50")],
    "gemini-3.1-flash-lite": [_p("0.25", "1.50")],
    "gemini-3.1-pro": [_p("2.00", "12.00")],
    "gemini-3-flash": [_p("0.50", "3.00")],
    "gemini-2.5-pro": [_p("1.25", "10.00")],
    "gemini-2.5-flash": [_p("0.30", "2.50")],
    "gemini-2.5-flash-lite": [_p("0.10", "0.40")],
    "gemini-2.0-flash": [_p("0.10", "0.40")],
    "gemini-2.0-flash-lite": [_p("0.075", "0.30")],
    # Anthropic Claude (first-party API)
    "claude-fable-5-1": [_p("10.00", "50.00")],
    "claude-fable-5": [_p("10.00", "50.00")],
    "claude-opus-5-5": [_p("4.00", "20.00")],
    "claude-opus-5": [_p("5.00", "25.00")],
    "claude-opus-4-8": [_p("5.00", "25.00")],
    "claude-opus-4-7": [_p("5.00", "25.00")],
    "claude-opus-4-6": [_p("5.00", "25.00")],
    "claude-sonnet-5-5": [_p("2.00", "10.00")],
    "claude-sonnet-5": [_p("2.00", "10.00")],
    "claude-sonnet-4-6": [_p("3.00", "15.00")],
    "claude-haiku-5-5": [_p("0.10", "0.50")],
    "claude-haiku-4-5": [_p("1.00", "5.00")],
}

_MILLION = Decimal(1_000_000)
_PLACES = Decimal("0.000001")


def price_for(model: str, on: date | None = None) -> Price | None:
    """The (input, output) price for ``model`` on day ``on`` (default: today, UTC)."""
    name = model.removeprefix("models/")
    matches = [prefix for prefix in PRICES if name.startswith(prefix)]
    if not matches:
        return None
    day = on or datetime.now(UTC).date()
    periods = [period for period in PRICES[max(matches, key=len)] if period[0] <= day]
    if not periods:
        return None
    _, input_usd, output_usd = periods[-1]
    return input_usd, output_usd


def estimate_cost_usd(model: str, usage: Usage, on: date | None = None) -> Decimal | None:
    price = price_for(model, on)
    if price is None:
        return None
    cost = (
        Decimal(usage.input_tokens) * price[0] + Decimal(usage.output_tokens) * price[1]
    ) / _MILLION
    return cost.quantize(_PLACES, rounding=ROUND_HALF_UP)
