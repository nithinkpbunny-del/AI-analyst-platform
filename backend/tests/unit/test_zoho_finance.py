"""Finance views over Zoho Books tables (M13): the P&L from the profit and loss report, receivables from invoices,
with a dictionary drafted in code from what the connector reads (rupees, nothing divided)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import cache

import pandas as pd
import pytest

from app.analytics import finance
from app.analytics.common import Analysis, AnalyticsError, Period
from app.connectors.base import DatasetInfo
from app.connectors.zoho_books import ZohoBooksConnector
from app.data.workspace import Workspace
from app.dictionary.draft import DatasetInput, build
from app.dictionary.model import Dictionary
from app.dictionary.profiler import profile_frame
from app.dictionary.units import to_units
from tests.recorded_zoho import ORG_ID, REFRESH_TOKEN, fake_zoho, zoho_test_setup

TABLES = ("profit_and_loss", "invoices", "expenses")


@cache
def _read() -> tuple[Dictionary, dict[str, pd.DataFrame]]:
    connector = ZohoBooksConnector(
        setup=zoho_test_setup(fake_zoho()),
        refresh_token=REFRESH_TOKEN,
        region="in",
        organization_id=ORG_ID,
        timezone="Asia/Kolkata",
        max_rows=1_000_000,
        clock=lambda: datetime(2026, 11, 9, tzinfo=UTC),
    )
    inputs = []
    frames: dict[str, pd.DataFrame] = {}
    for ref in TABLES:
        result = connector.fetch(DatasetInfo(ref=ref, name=ref))
        info = [
            {
                "name": c.name,
                "label": c.label,
                "type": c.type,
                "unit": c.unit,
                "stored_in": c.stored_in,
                "currencies": list(c.currencies),
            }
            for c in result.columns
        ]
        profile = profile_frame(result.frame, info, name=ref)
        inputs.append(DatasetInput(f"ds-{ref}", "conn-zoho", "Zoho Books", ref, profile))
        frames[ref] = result.frame
    return build(inputs, None, company_name="UrbanCart").dictionary, frames


def analysis() -> Analysis:
    dictionary, frames = _read()
    d = dictionary.model_copy(deep=True)
    ws = Workspace(timeout_seconds=30)
    for key, entry in d.datasets.items():
        ws.load(key, to_units(frames[key], entry), {"dataset": key})
    return Analysis(ws, d)


def test_the_dictionary_sees_rupees_and_a_month_column() -> None:
    entry = analysis().dictionary.datasets["profit_and_loss"]
    assert entry.columns["amount"].unit == "INR"
    assert not entry.columns["amount"].divide_by
    assert entry.time_column == "month"


def test_pnl_lines_and_profits_for_a_quarter() -> None:
    q3 = Period(date(2026, 7, 1), date(2026, 9, 30))
    result = finance.pnl(analysis(), q3)
    facts = result.facts
    sections = set(result.table["section"])
    assert sections == {"Operating Income", "Cost of Goods Sold", "Operating Expense"}
    income = float(result.table[result.table["section"] == "Operating Income"]["amount"].sum())
    assert facts["operating_income"] == pytest.approx(income)
    assert facts["gross_profit"] == pytest.approx(income - facts["cost_of_goods_sold"])
    assert facts["operating_profit"] == pytest.approx(
        facts["gross_profit"] - facts["operating_expenses"]
    )
    assert facts["net_profit"] == pytest.approx(facts["operating_profit"])
    assert facts["gross_margin_pct"] == round(facts["gross_profit"] / income * 100, 1)
    assert result.tables_used == ["profit_and_loss"]


def test_pnl_operating_expenses_equal_the_expense_entries() -> None:
    q3 = Period(date(2026, 7, 1), date(2026, 9, 30))
    a = analysis()
    pnl = finance.pnl(a, q3).facts["operating_expenses"]
    spent = a.workspace.frame(
        "SELECT SUM(total) AS s FROM expenses WHERE date >= '2026-07-01' AND date < '2026-10-01'"
    )["s"].iloc[0]
    assert pnl == pytest.approx(float(spent))


def test_receivables_come_from_invoices() -> None:
    result = finance.receivables_ageing(analysis(), as_of=date(2026, 10, 31))
    assert result.facts["table"] == "invoices"
    assert result.facts["total"] > 0


def test_without_a_pnl_table_it_still_says_accounting_data_is_needed() -> None:
    a = analysis()
    del a.dictionary.datasets["profit_and_loss"]
    with pytest.raises(AnalyticsError) as caught:
        finance.pnl(a)
    assert caught.value.code == "needs_accounting_data"
