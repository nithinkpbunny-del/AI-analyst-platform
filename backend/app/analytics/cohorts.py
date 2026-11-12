"""Cohort retention (BUILD_SPEC 10.1 ``cohort_retention``).

A cohort is the month of a customer's first row in the table. For each cohort: its size and the share of its
customers with at least one row 1, 2, ... months later. The latest ``max_cohorts`` cohorts are returned; a month
that has not fully happened yet (or lies past the data) is left empty, not 0.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    Result,
    coverage,
    customer_column,
    frame_for,
    metric_info,
    where,
)
from app.security.sql_guard import safe_identifier as q

MAX_COHORTS = 12
MAX_OFFSET = 12


def cohort_retention(
    analysis: Analysis,
    metric: str,
    customer: str | None = None,
    filters: Filters | None = None,
    max_cohorts: int = MAX_COHORTS,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    column = customer or customer_column(analysis, info.table)
    if not column:
        raise AnalyticsError("no_customer_column", info.table)
    frame = frame_for(analysis, info.table)
    key = q(frame.column(column))
    time = q(info.time_column)
    clause, params = where(frame, filters)
    months = analysis.frame(
        f"SELECT DISTINCT {key} AS customer, CAST(date_trunc('month', {time}) AS DATE) AS month "  # noqa: S608
        f"FROM ({frame.base_sql()}) AS b {clause}",
        params,
    ).dropna()
    if months.empty:
        raise AnalyticsError("no_data", metric)
    months["month"] = pd.to_datetime(months["month"])
    first = months.groupby("customer")["month"].min().rename("cohort")
    months = months.join(first, on="customer")
    months["offset"] = (months["month"].dt.year - months["cohort"].dt.year) * 12 + (
        months["month"].dt.month - months["cohort"].dt.month
    )
    _, last_day = coverage(analysis, info.table)
    last_full = pd.Timestamp(last_day).to_period("M") if last_day is not None else None
    if last_day is not None and (pd.Timestamp(last_day) + pd.Timedelta(days=1)).day != 1:
        last_full = last_full - 1 if last_full is not None else None
    sizes = first.value_counts().sort_index()
    cohorts = list(sizes.index[-max_cohorts:])
    rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        members = months[months["cohort"] == cohort]
        size = int(sizes[cohort])
        row: dict[str, Any] = {"cohort": cohort.date().isoformat()[:7], "customers": size}
        for offset in range(1, MAX_OFFSET):
            month = (cohort + pd.DateOffset(months=offset)).to_period("M")
            if last_full is not None and month > last_full:
                row[f"m{offset}"] = None
                continue
            active = members[members["offset"] == offset]["customer"].nunique()
            row[f"m{offset}"] = active / size * 100.0
        rows.append(row)
    table = pd.DataFrame(rows)
    m1 = table["m1"].dropna() if "m1" in table else pd.Series(dtype="float64")
    m3 = table["m3"].dropna() if "m3" in table else pd.Series(dtype="float64")
    facts: dict[str, Any] = {
        "metric": metric,
        "customer_column": column,
        "cohorts": len(rows),
        "average_month_1_retention_pct": float(m1.mean()) if len(m1) else None,
        "average_month_3_retention_pct": float(m3.mean()) if len(m3) else None,
    }
    if len(m3) >= 2:
        ranked = table.dropna(subset=["m3"]).sort_values("m3")
        facts["best_month_3_cohort"] = str(ranked["cohort"].iloc[-1])
        facts["worst_month_3_cohort"] = str(ranked["cohort"].iloc[0])
    return Result(table, facts, [], [info.table], list(analysis.sql_log))
