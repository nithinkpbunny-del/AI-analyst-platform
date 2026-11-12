"""Metric values for a period against a comparison period, and a metric over time (BUILD_SPEC 10.1)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.common import (
    OTHER,
    Analysis,
    AnalyticsError,
    Filters,
    Grain,
    Period,
    Result,
    bucket_sql,
    change_pct,
    coverage_notes,
    frame_for,
    metric_info,
    number,
    value_of,
    where,
)
from app.security.sql_guard import safe_identifier as q

MAX_METRICS = 8
MAX_SPLIT = 8


def kpi_summary(
    analysis: Analysis,
    metrics: list[str],
    period: Period,
    compare: Period | None,
    filters: Filters | None = None,
) -> Result:
    """One row per metric: the value in ``period``, in ``compare``, the change and the change in %."""
    if not metrics:
        raise AnalyticsError("no_metric")
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    tables: list[str] = []
    for key in metrics[:MAX_METRICS]:
        info = metric_info(analysis.dictionary, key)
        current = value_of(analysis, info, period, filters)
        previous = value_of(analysis, info, compare, filters) if compare else None
        change = current - previous if current is not None and previous is not None else None
        rows.append(
            {
                "metric": key,
                "unit": info.unit,
                "current": current,
                "previous": previous,
                "change": change,
                "change_pct": change_pct(current, previous),
                "good_direction": info.good_direction,
            }
        )
        if info.table not in tables:
            tables.append(info.table)
            notes += coverage_notes(analysis, info.table, period, compare)
    facts: dict[str, Any] = {"period": period.as_json()}
    if compare:
        facts["compare"] = compare.as_json()
        if abs(compare.days - period.days) > 0.1 * period.days:
            notes.append(f"unequal_periods:{period.days}:{compare.days}")
    return Result(pd.DataFrame(rows), facts, notes, tables, list(analysis.sql_log))


def trend(
    analysis: Analysis,
    metric: str,
    period: Period,
    grain: Grain,
    split_by: str | None = None,
    filters: Filters | None = None,
    top_n: int = MAX_SPLIT,
) -> Result:
    """The metric per ``grain`` bucket, optionally one series per value of ``split_by`` (largest first; the rest
    together as "Other" when the metric adds up)."""
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    frame = frame_for(analysis, info.table)
    split = frame.column(split_by) if split_by else None
    clause, params = where(frame, filters, time_column=info.time_column, period=period)
    bucket = bucket_sql(info.time_column, grain)
    base = frame.base_sql()
    notes = coverage_notes(analysis, info.table, period)
    if split is None:
        sql = (
            f"SELECT {bucket} AS bucket, ({info.sql}) AS value "  # noqa: S608
            f"FROM ({base}) AS b {clause} GROUP BY 1 ORDER BY 1"
        )
        table = analysis.frame(sql, params)
    else:
        top = _top_values(analysis, base, clause, params, info.sql, split, top_n)
        label = f"CASE WHEN CAST({q(split)} AS VARCHAR) IN ({', '.join('?' for _ in top)}) THEN CAST({q(split)} AS VARCHAR) ELSE '{OTHER}' END"
        if not info.additive:
            label = f"CAST({q(split)} AS VARCHAR)"
        sql = (
            f"SELECT {bucket} AS bucket, {label} AS series, ({info.sql}) AS value "  # noqa: S608
            f"FROM ({base}) AS b {clause} GROUP BY 1, 2 ORDER BY 1, 2"
        )
        table = analysis.frame(sql, ([*top] if info.additive else []) + params)
        if not info.additive:
            table = table[table["series"].isin(top)]
    table["bucket"] = pd.to_datetime(table["bucket"]).dt.date
    table["value"] = [number(v) for v in table["value"]]
    facts: dict[str, Any] = {
        "metric": metric,
        "unit": info.unit,
        "grain": grain,
        "period": period.as_json(),
        "buckets": int(table["bucket"].nunique()) if len(table) else 0,
    }
    if split is None and len(table) >= 2:
        first, last = table["value"].iloc[0], table["value"].iloc[-1]
        facts.update(first=first, last=last, change_pct=change_pct(last, first))
        ranked = table.dropna(subset=["value"]).sort_values("value")
        if len(ranked):
            facts["max"] = {
                "bucket": str(ranked["bucket"].iloc[-1]),
                "value": ranked["value"].iloc[-1],
            }
            facts["min"] = {
                "bucket": str(ranked["bucket"].iloc[0]),
                "value": ranked["value"].iloc[0],
            }
    if _partial_last_bucket(period, grain):
        notes.append(f"partial_last_bucket:{grain}")
    return Result(
        table,
        facts,
        notes,
        [info.table, *[j.table for j in frame.joins.values()]],
        list(analysis.sql_log),
    )


def _top_values(
    analysis: Analysis,
    base: str,
    clause: str,
    params: list[Any],
    metric_sql: str,
    split: str,
    top_n: int,
) -> list[str]:
    sql = (
        f"SELECT CAST({q(split)} AS VARCHAR) AS v, ({metric_sql}) AS value "  # noqa: S608
        f"FROM ({base}) AS b {clause} GROUP BY 1 ORDER BY abs(value) DESC NULLS LAST LIMIT {int(top_n)}"
    )
    found = analysis.frame(sql, params)
    return [str(v) for v in found["v"] if v is not None and v is not pd.NA]


def _partial_last_bucket(period: Period, grain: Grain) -> bool:
    end = pd.Timestamp(period.end_exclusive)
    if grain == "day":
        return False
    if grain == "week":
        return end.dayofweek != 0
    if grain == "month":
        return end.day != 1
    if grain == "quarter":
        return not (end.day == 1 and end.month in (1, 4, 7, 10))
    return not (end.day == 1 and end.month == 1)
