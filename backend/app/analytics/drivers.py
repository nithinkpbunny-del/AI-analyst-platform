"""Why a metric changed (BUILD_SPEC 10.1 ``driver_analysis``).

For a metric, a current and a comparison period and a few dimensions:

* **contribution by dimension**: each value's metric in both periods, the change and its share of the total change
  (largest movers first, the rest as "Other");
* **mix vs rate** (metrics that add up): the change of each value split into a *volume* effect (more or fewer rows,
  e.g. orders) and a *rate* effect (more or less per row, e.g. order value);
* **new vs repeat customers**: a customer is *new* in a period when their first row in the whole table falls in
  that period, otherwise *repeat*. Also the number of active customers and rows per customer, so a drop in how
  often existing customers buy shows up as such.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.common import (
    OTHER,
    Analysis,
    AnalyticsError,
    Filters,
    Frame,
    MetricInfo,
    Period,
    Result,
    change_pct,
    coverage_notes,
    customer_column,
    frame_for,
    metric_info,
    number,
    value_of,
    where,
)
from app.security.sql_guard import safe_identifier as q

MAX_DIMENSIONS = 3
TOP_VALUES = 10


def driver_analysis(
    analysis: Analysis,
    metric: str,
    current: Period,
    previous: Period,
    dimensions: list[str],
    filters: Filters | None = None,
    customer: str | None = None,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    now = value_of(analysis, info, current, filters)
    before = value_of(analysis, info, previous, filters)
    total_change = (now or 0.0) - (before or 0.0)
    facts: dict[str, Any] = {
        "metric": metric,
        "unit": info.unit,
        "current": now,
        "previous": before,
        "change": total_change,
        "change_pct": change_pct(now, before),
        "period": current.as_json(),
        "compare": previous.as_json(),
    }
    notes = coverage_notes(analysis, info.table, current, previous)
    rows: list[pd.DataFrame] = []
    tables = [info.table]
    by_dimension: dict[str, Any] = {}
    for dimension in dimensions[:MAX_DIMENSIONS]:
        frame = frame_for(analysis, info.table)
        part = _by_dimension(analysis, info, frame, dimension, current, previous, filters)
        part.insert(0, "dimension", dimension)
        if total_change:
            part["share_of_change"] = part["change"] / total_change * 100.0
        else:
            part["share_of_change"] = None
        rows.append(part)
        tables += [j.table for j in frame.joins.values() if j.table not in tables]
        movers = part[part["value"] != OTHER]
        if len(movers):
            falling = movers[movers["change"] < 0]
            rising = movers[movers["change"] > 0]
            by_dimension[dimension] = {
                "values": len(movers),
                "falling": falling["value"].tolist(),
                "rising": rising["value"].tolist(),
                "largest_mover": movers.iloc[0]["value"],
            }
    facts["by_dimension"] = by_dimension
    table = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    column = customer or customer_column(analysis, info.table)
    if column:
        facts["new_vs_repeat"] = _new_vs_repeat(analysis, info, column, current, previous, filters)
    else:
        notes.append("no_customer_column")
    if not info.additive:
        notes.append("not_additive")
    return Result(table, facts, notes, tables, list(analysis.sql_log))


def _by_dimension(
    analysis: Analysis,
    info: MetricInfo,
    frame: Frame,
    dimension: str,
    current: Period,
    previous: Period,
    filters: Filters | None,
) -> pd.DataFrame:
    column = frame.column(dimension)
    base = frame.base_sql()
    parts = []
    for label, period in (("current", current), ("previous", previous)):
        clause, params = where(frame, filters, time_column=info.time_column, period=period)
        sql = (
            f"SELECT CAST({q(column)} AS VARCHAR) AS value, ({info.sql}) AS metric, "  # noqa: S608
            f"count(*) AS rows FROM ({base}) AS b {clause} GROUP BY 1"
        )
        found = analysis.frame(sql, params).rename(
            columns={"metric": label, "rows": f"{label}_rows"}
        )
        parts.append(found.set_index("value"))
    joined = parts[0].join(parts[1], how="outer").reset_index()
    for name in ("current", "previous", "current_rows", "previous_rows"):
        joined[name] = pd.to_numeric(joined[name], errors="coerce").astype("float64").fillna(0.0)
    joined["value"] = joined["value"].astype(object).where(joined["value"].notna(), "(empty)")
    joined["change"] = joined["current"] - joined["previous"]
    joined["change_pct"] = [
        change_pct(c, p) for c, p in zip(joined["current"], joined["previous"], strict=True)
    ]
    if info.additive:
        rate_now = joined["current"] / joined["current_rows"].where(joined["current_rows"] > 0)
        rate_before = joined["previous"] / joined["previous_rows"].where(
            joined["previous_rows"] > 0
        )
        joined["volume_effect"] = (
            joined["current_rows"] - joined["previous_rows"]
        ) * rate_before.fillna(rate_now).fillna(0.0)
        joined["rate_effect"] = joined["change"] - joined["volume_effect"]
    joined = joined.reindex(joined["change"].abs().sort_values(ascending=False).index)
    if len(joined) > TOP_VALUES and info.additive:
        head, tail = joined.iloc[:TOP_VALUES], joined.iloc[TOP_VALUES:]
        other = {
            "value": OTHER,
            **{
                c: tail[c].sum()
                for c in (
                    "current",
                    "previous",
                    "current_rows",
                    "previous_rows",
                    "change",
                    "volume_effect",
                    "rate_effect",
                )
            },
        }
        other["change_pct"] = change_pct(number(other["current"]), number(other["previous"]))
        joined = pd.concat([head, pd.DataFrame([other])], ignore_index=True)
    elif len(joined) > TOP_VALUES:
        joined = joined.iloc[:TOP_VALUES]
    joined = joined.rename(
        columns={"current_rows": "current_count", "previous_rows": "previous_count"}
    )
    return joined.reset_index(drop=True)


def _new_vs_repeat(
    analysis: Analysis,
    info: MetricInfo,
    customer: str,
    current: Period,
    previous: Period,
    filters: Filters | None,
) -> dict[str, Any]:
    frame = frame_for(analysis, info.table)
    column = frame.column(customer)
    time = q(info.time_column or "")
    base = frame.base_sql()
    # First appearance of each customer in the whole table (any filter, any period).
    first = f"(SELECT {q(column)} AS __c, min({time}) AS __first FROM {q(info.table)} GROUP BY 1)"  # noqa: S608
    out: dict[str, Any] = {"customer_column": customer}
    for label, period in (("current", current), ("previous", previous)):
        clause, params = where(frame, filters, time_column=info.time_column, period=period)
        sql = (
            f"SELECT CASE WHEN f.__first >= ? THEN 'new' ELSE 'repeat' END AS kind, "  # noqa: S608
            f"({info.sql}) AS metric, count(*) AS rows, count(DISTINCT b.{q(column)}) AS customers "
            f"FROM ({base}) AS b LEFT JOIN {first} AS f ON b.{q(column)} = f.__c {clause} GROUP BY 1"
        )
        found = analysis.frame(sql, [period.start, *params])
        out[label] = {
            str(row["kind"]): {
                "metric": number(row["metric"]),
                "rows": int(row["rows"]),
                "customers": int(row["customers"]),
                "rows_per_customer": (int(row["rows"]) / int(row["customers"]))
                if int(row["customers"])
                else None,
            }
            for _, row in found.iterrows()
        }
    for kind in ("new", "repeat"):
        now = out["current"].get(kind, {}).get("metric")
        before = out["previous"].get(kind, {}).get("metric")
        out[f"{kind}_change"] = (now or 0.0) - (before or 0.0)
        out[f"{kind}_change_pct"] = change_pct(now, before)
        rate_now = out["current"].get(kind, {}).get("rows_per_customer")
        rate_before = out["previous"].get(kind, {}).get("rows_per_customer")
        out[f"{kind}_rows_per_customer_change_pct"] = change_pct(rate_now, rate_before)
    total = out["new_change"] + out["repeat_change"]
    out["repeat_share_of_change"] = out["repeat_change"] / total * 100.0 if total else None
    return out
