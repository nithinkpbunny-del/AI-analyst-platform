"""Customer segments by recency, frequency and money spent (BUILD_SPEC 10.1 ``rfm_segments``).

Each customer gets a score of 1-5 for recency (days since their last row), frequency (rows) and monetary value
(the metric, e.g. revenue) by quintiles, and a plain segment from recency and frequency:

    Champions (R>=4, F>=4) · Loyal (F>=4) · Potential loyalists (R>=4, F 2-3) · New (R>=4, F=1)
    At risk (R<=2, F>=3) · Hibernating (R<=2, F<=2) · Needs attention (the rest)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    Period,
    Result,
    coverage,
    customer_column,
    frame_for,
    metric_info,
    where,
)
from app.security.sql_guard import safe_identifier as q

SEGMENTS = (
    "Champions",
    "Loyal",
    "Potential loyalists",
    "New",
    "Needs attention",
    "At risk",
    "Hibernating",
)


def rfm_segments(
    analysis: Analysis,
    metric: str,
    period: Period | None = None,
    as_of: date | None = None,
    customer: str | None = None,
    filters: Filters | None = None,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    column = customer or customer_column(analysis, info.table)
    if not column:
        raise AnalyticsError("no_customer_column", info.table)
    frame = frame_for(analysis, info.table)
    key = frame.column(column)
    if as_of is None:
        _, as_of = coverage(analysis, info.table)
    clause, params = where(frame, filters, time_column=info.time_column, period=period)
    time = q(info.time_column)
    per_customer = analysis.frame(
        f"SELECT {q(key)} AS customer, CAST(max({time}) AS DATE) AS last_seen, count(*) AS frequency, "  # noqa: S608
        f"({info.sql}) AS monetary FROM ({frame.base_sql()}) AS b {clause} GROUP BY 1",
        params,
    ).dropna(subset=["customer"])
    if per_customer.empty or as_of is None:
        raise AnalyticsError("no_data", metric)
    per_customer["recency_days"] = (
        pd.Timestamp(as_of) - pd.to_datetime(per_customer["last_seen"])
    ).dt.days
    per_customer["monetary"] = pd.to_numeric(per_customer["monetary"], errors="coerce").astype(
        "float64"
    )
    per_customer["r"] = _score(-per_customer["recency_days"])
    per_customer["f"] = _score(per_customer["frequency"].astype("float64"))
    per_customer["m"] = _score(per_customer["monetary"])
    per_customer["segment"] = [
        _segment(r, f) for r, f in zip(per_customer["r"], per_customer["f"], strict=True)
    ]
    total_customers = len(per_customer)
    total_money = float(per_customer["monetary"].sum())
    grouped = per_customer.groupby("segment").agg(
        customers=("customer", "count"),
        metric_total=("monetary", "sum"),
        avg_recency_days=("recency_days", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_metric=("monetary", "mean"),
    )
    grouped = grouped.reindex([s for s in SEGMENTS if s in grouped.index]).reset_index()
    grouped["share_of_customers_pct"] = grouped["customers"] / total_customers * 100.0
    grouped["share_of_metric_pct"] = (
        grouped["metric_total"] / total_money * 100.0 if total_money else None
    )
    facts: dict[str, Any] = {
        "metric": metric,
        "unit": info.unit,
        "customers": total_customers,
        "as_of": as_of.isoformat(),
        "customer_column": column,
        "period": period.as_json() if period else None,
    }
    return Result(grouped, facts, [], [info.table], list(analysis.sql_log))


def _score(values: pd.Series) -> pd.Series:
    """Quintile scores 1-5 by rank (ties share a score; few distinct values still give valid scores)."""
    ranks = values.rank(method="average", pct=True)
    return pd.Series(np.clip(np.ceil(ranks * 5), 1, 5).astype(int), index=values.index)


def _segment(r: int, f: int) -> str:
    if r >= 4 and f >= 4:
        return "Champions"
    if f >= 4 and r >= 3:
        return "Loyal"
    if r >= 4 and f >= 2:
        return "Potential loyalists"
    if r >= 4:
        return "New"
    if r <= 2 and f >= 3:
        return "At risk"
    if r <= 2:
        return "Hibernating"
    return "Needs attention"
