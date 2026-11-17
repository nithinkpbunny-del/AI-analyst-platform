"""Finance views (BUILD_SPEC 10.1 ``budget_variance``, ``receivables_ageing``; ``pnl`` needs accounting data).

* **Budget vs actual:** any table with a budget and an actual column (found by name), by a dimension
  (department, account, category) and month. Variance and variance % per row; lines more than
  ``flag_pct`` (5%, the Financial lens) over or under budget are flagged.
* **Receivables ageing:** a table with a due date and an outstanding amount: buckets 0-30, 31-60, 61-90 and over
  90 days overdue on ``as_of``.
* **P&L:** a profit and loss table with one row per account line (section, account, amount and a month), e.g.
  Zoho Books' report (M13): lines summed for the period, then gross profit (operating income - cost of goods
  sold), operating profit (- operating expenses) and net profit (all income - all costs), with margins as a share of
  operating income. Without such a table it says that accounting data is needed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Period,
    Result,
    change_pct,
    coverage,
)
from app.dictionary.model import DatasetEntry
from app.security.sql_guard import safe_identifier as q

FLAG_PCT = 5.0
_BUDGET = re.compile(r"(budget|plan(ned)?|target|forecast)", re.I)
_ACTUAL = re.compile(r"(actual|spent|expense|spend)", re.I)
_DIMENSION = re.compile(
    r"(department|dept|account|category|cost_cent|line|head|team|function)", re.I
)
_DUE = re.compile(r"(due_?date|due_on|due)", re.I)
_OUTSTANDING = re.compile(r"(balance|outstanding|amount_due|due_amount|unpaid)", re.I)


def _money_columns(entry: DatasetEntry) -> list[str]:
    """Rupee columns only: a budget is money (a "target ROAS" ratio is not a budget)."""
    return [n for n, c in entry.columns.items() if c.unit == "INR"]


def budget_tables(analysis: Analysis) -> list[str]:
    out = []
    for key, entry in analysis.dictionary.datasets.items():
        money = _money_columns(entry)
        if any(_BUDGET.search(n) for n in money) and any(_ACTUAL.search(n) for n in money):
            out.append(key)
    return out


def budget_variance(
    analysis: Analysis,
    period: Period | None = None,
    table: str | None = None,
    by_month: bool = False,
    flag_pct: float = FLAG_PCT,
) -> Result:
    key = table or next(iter(budget_tables(analysis)), None)
    if key is None:
        raise AnalyticsError("no_budget_data")
    entry = analysis.dictionary.datasets.get(key)
    if entry is None:
        raise AnalyticsError("unknown_table", key)
    money = _money_columns(entry)
    budget = next((n for n in money if _BUDGET.search(n)), None)
    actual = next((n for n in money if _ACTUAL.search(n) and n != budget), None)
    if budget is None or actual is None:
        raise AnalyticsError("no_budget_data", key)
    dimension = next(
        (n for n, c in entry.columns.items() if c.role == "dimension" and _DIMENSION.search(n)),
        next((n for n, c in entry.columns.items() if c.role == "dimension"), None),
    )
    analysis.table(key)
    groups = [f"CAST({q(dimension)} AS VARCHAR) AS line"] if dimension else ["'Total' AS line"]
    if by_month and entry.time_column:
        groups.append(f"CAST(date_trunc('month', {q(entry.time_column)}) AS DATE) AS month")
    clause, params = "", []
    if period is not None:
        if not entry.time_column:
            raise AnalyticsError("no_time_column", key)
        clause = f"WHERE {q(entry.time_column)} >= ? AND {q(entry.time_column)} < ?"
        params = [period.start, period.end_exclusive]
    group_by = ", ".join(str(i + 1) for i in range(len(groups)))
    sql = (
        f"SELECT {', '.join(groups)}, SUM({q(budget)}) AS budget, SUM({q(actual)}) AS actual "  # noqa: S608
        f"FROM {q(key)} {clause} GROUP BY {group_by} ORDER BY {group_by}"
    )
    table_out = analysis.frame(sql, params)
    for name in ("budget", "actual"):
        table_out[name] = pd.to_numeric(table_out[name], errors="coerce").astype("float64")
    table_out["variance"] = table_out["actual"] - table_out["budget"]
    table_out["variance_pct"] = [
        change_pct(a, b) for a, b in zip(table_out["actual"], table_out["budget"], strict=True)
    ]
    table_out["flag"] = [
        ("over" if v > 0 else "under") if v is not None and abs(v) > flag_pct else None
        for v in table_out["variance_pct"]
    ]
    if "month" in table_out:
        table_out["month"] = pd.to_datetime(table_out["month"]).dt.date
    total_budget = float(table_out["budget"].sum())
    total_actual = float(table_out["actual"].sum())
    flagged = table_out[table_out["flag"].notna()]
    facts: dict[str, Any] = {
        "table": key,
        "dimension": dimension,
        "period": period.as_json() if period else None,
        "total_budget": total_budget,
        "total_actual": total_actual,
        "total_variance": total_actual - total_budget,
        "total_variance_pct": change_pct(total_actual, total_budget),
        "flag_pct": flag_pct,
        "flagged": flagged[["line", *(["month"] if "month" in flagged else []), "variance_pct"]]
        .assign(month=lambda f: f["month"].astype(str) if "month" in f else None)
        .to_dict("records")
        if len(flagged)
        else [],
    }
    return Result(table_out, facts, [], [key], list(analysis.sql_log))


def receivables_ageing(
    analysis: Analysis, table: str | None = None, as_of: date | None = None
) -> Result:
    key = table
    if key is None:
        for name, entry in analysis.dictionary.datasets.items():
            cols = list(entry.columns)
            if any(_DUE.search(c) for c in cols) and any(_OUTSTANDING.search(c) for c in cols):
                key = name
                break
    if key is None:
        raise AnalyticsError("needs_accounting_data", "receivables")
    entry = analysis.dictionary.datasets[key]
    due = next(c for c in entry.columns if _DUE.search(c))
    outstanding = next(c for c in entry.columns if _OUTSTANDING.search(c))
    analysis.table(key)
    if as_of is None:
        _, as_of = coverage(analysis, key)
        as_of = as_of or date.today()
    sql = (
        f"SELECT CASE WHEN days <= 0 THEN 'Not due' WHEN days <= 30 THEN '1-30' WHEN days <= 60 THEN '31-60' "  # noqa: S608
        f"WHEN days <= 90 THEN '61-90' ELSE 'Over 90' END AS bucket, count(*) AS invoices, SUM(amount) AS amount "
        f"FROM (SELECT date_diff('day', CAST({q(due)} AS DATE), CAST(? AS DATE)) AS days, {q(outstanding)} AS amount "
        f"FROM {q(key)} WHERE {q(outstanding)} > 0) GROUP BY 1"
    )
    table_out = analysis.frame(sql, [as_of])
    order = ["Not due", "1-30", "31-60", "61-90", "Over 90"]
    table_out["bucket"] = pd.Categorical(table_out["bucket"], order, ordered=True)
    table_out = table_out.sort_values("bucket").reset_index(drop=True)
    table_out["bucket"] = table_out["bucket"].astype(str)
    facts = {
        "table": key,
        "as_of": as_of.isoformat(),
        "total": float(pd.to_numeric(table_out["amount"]).sum()),
    }
    return Result(table_out, facts, [], [key], list(analysis.sql_log))


_SECTION = re.compile(r"(section|group|category|type)", re.I)
_ACCOUNT = re.compile(r"(account|line|ledger|head)", re.I)


def pnl_tables(analysis: Analysis) -> list[str]:
    out = []
    for key, entry in analysis.dictionary.datasets.items():
        columns = list(entry.columns)
        money = _money_columns(entry)
        if (
            money
            and any(_SECTION.search(c) for c in columns)
            and any(_ACCOUNT.search(c) and c not in money for c in columns)
            and ("profit" in key.lower() or "pnl" in key.lower() or "p_l" in key.lower())
        ):
            out.append(key)
    return out


def _kind(section: str) -> str:
    lowered = section.lower()
    if "cost of goods" in lowered or "cogs" in lowered:
        return "cogs"
    if "income" in lowered or "revenue" in lowered or "sales" in lowered:
        return "other_income" if "non" in lowered else "income"
    return "other_expense" if "non" in lowered else "expense"


def pnl(analysis: Analysis, period: Period | None = None) -> Result:
    key = next(iter(pnl_tables(analysis)), None)
    if key is None:
        raise AnalyticsError("needs_accounting_data", "pnl")
    entry = analysis.dictionary.datasets[key]
    columns = list(entry.columns)
    money = _money_columns(entry)
    amount = next((c for c in money if "amount" in c.lower()), money[0])
    section = next(c for c in columns if _SECTION.search(c))
    account = next(c for c in columns if _ACCOUNT.search(c) and c not in money)
    analysis.table(key)
    clause, params = "", []
    if period is not None:
        if not entry.time_column:
            raise AnalyticsError("no_time_column", key)
        clause = f"WHERE {q(entry.time_column)} >= ? AND {q(entry.time_column)} < ?"
        params = [period.start, period.end_exclusive]
    sql = (
        f"SELECT CAST({q(section)} AS VARCHAR) AS section, CAST({q(account)} AS VARCHAR) AS line, "  # noqa: S608
        f"SUM({q(amount)}) AS amount FROM {q(key)} {clause} GROUP BY 1, 2 ORDER BY 1, 3 DESC"
    )
    table_out = analysis.frame(sql, params)
    table_out["amount"] = pd.to_numeric(table_out["amount"], errors="coerce").astype("float64")
    by_kind: dict[str, float] = {}
    for sec, value in zip(table_out["section"], table_out["amount"], strict=True):
        by_kind[_kind(str(sec))] = by_kind.get(_kind(str(sec)), 0.0) + float(value)
    income = by_kind.get("income", 0.0)
    gross = income - by_kind.get("cogs", 0.0)
    operating = gross - by_kind.get("expense", 0.0)
    net = operating + by_kind.get("other_income", 0.0) - by_kind.get("other_expense", 0.0)

    def margin(value: float) -> float | None:
        return round(value / income * 100, 1) if income else None

    facts: dict[str, Any] = {
        "table": key,
        "period": period.as_json() if period else None,
        "operating_income": income,
        "cost_of_goods_sold": by_kind.get("cogs", 0.0),
        "gross_profit": gross,
        "gross_margin_pct": margin(gross),
        "operating_expenses": by_kind.get("expense", 0.0),
        "operating_profit": operating,
        "operating_margin_pct": margin(operating),
        "net_profit": net,
        "net_margin_pct": margin(net),
    }
    return Result(table_out, facts, [], [key], list(analysis.sql_log))
