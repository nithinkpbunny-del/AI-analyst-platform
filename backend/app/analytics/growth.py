"""Growth signals: patterns that repeat and point to growth (BUILD_SPEC 10.3; the agent's ``growth_signals`` tool and
the briefings' "Working well" items).

Two kinds of signal, both measured on complete periods only (a month that has not ended is left out):

* **streak**: a segment (one value of a dimension, or a pair of values of two dimensions, e.g. Beauty x West) whose
  metric rose in each of the last ``min_streak`` or more periods **and** whose share of the whole rose each time.
  The share rule takes out the season: in October every segment sells more, but only a segment that grows faster
  than the business keeps gaining share.
* **beats_last_year**: the whole metric was above the same period a year earlier in each of the last
  ``min_streak`` or more periods.

For each streak the result says how much came from **repeat customers** (customers first seen before the streak
began) when the table has a customer column, and the **estimated upside**: one more period at the streak's average
step, labelled as an estimate. Signals are ranked by the size of the increase.
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    Grain,
    MetricInfo,
    Period,
    Result,
    bucket_sql,
    change_pct,
    customer_column,
    frame_for,
    metric_info,
    number,
    where,
)
from app.security.sql_guard import safe_identifier as q

MIN_STREAK = 3
TOP_VALUES = 5
MAX_DIMENSIONS = 3
MAX_SIGNALS = 10
# A segment must be at least this share of the whole in its last period to count (tiny segments are noise).
MIN_SHARE_PCT = 1.0
# ...and have grown at least this much over its streak.
MIN_GROWTH_PCT = 10.0
MAX_DISTINCT = 40


def growth_signals(
    analysis: Analysis,
    metric: str,
    period: Period,
    dimensions: list[str] | None = None,
    grain: Grain = "month",
    filters: Filters | None = None,
    *,
    min_streak: int = MIN_STREAK,
    top_values: int = TOP_VALUES,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    if grain not in ("week", "month", "quarter"):
        raise AnalyticsError("bad_grain", grain)
    dims = list(dict.fromkeys(dimensions or default_dimensions(analysis, info)))[:MAX_DIMENSIONS]
    window = _complete(period, grain)
    total = _series(analysis, info, window, grain, [], filters)
    notes: list[str] = []
    if len(total) < min_streak + 1:
        notes.append(f"short_history:{len(total)}:{grain}")
    rows: list[dict[str, Any]] = []
    yearly = _beats_last_year(analysis, info, window, grain, filters, min_streak)
    if yearly is not None:
        rows.append(yearly)
    if info.additive and len(total) >= min_streak + 1:
        groups: list[list[str]] = [[d] for d in dims]
        if len(dims) >= 2:
            groups.append(dims[:2])
        for group in groups:
            frame = _series(analysis, info, window, grain, group, filters, top_values)
            for key, points in frame.items():
                found = _streak(points, total, min_streak)
                if found is not None:
                    rows.append(
                        {"kind": "streak", "segment": dict(zip(group, key, strict=True)), **found}
                    )
    streaks = sorted(
        (r for r in rows if r["kind"] == "streak"), key=lambda r: -abs(r["change"] or 0.0)
    )
    # A pair (Beauty x West) explains more than its parts: drop a single value whose streak the pair already shows.
    streaks = _without_echoes(streaks)[:MAX_SIGNALS]
    customer = customer_column(analysis, info.table)
    if customer is not None:
        for row in streaks[:3]:
            row["repeat_share_of_increase_pct"] = _repeat_share(
                analysis, info, customer, row, grain, filters
            )
    ordered = streaks + [r for r in rows if r["kind"] == "beats_last_year"]
    table = pd.DataFrame(
        [
            {
                **{k: v for k, v in r.items() if k not in ("segment", "series")},
                "segment": _label(r.get("segment") or {}),
            }
            for r in ordered
        ]
    )
    facts: dict[str, Any] = {
        "metric": metric,
        "unit": info.unit,
        "grain": grain,
        "period": window.as_json(),
        "last_period": str(total.index[-1]) if len(total) else None,
        "dimensions": dims,
        "signals": len(ordered),
    }
    if streaks:
        top = streaks[0]
        facts["top"] = {
            "segment": _label(top["segment"]),
            "periods": top["periods"],
            "start_value": top["start_value"],
            "end_value": top["end_value"],
            "change_pct": top["change_pct"],
            "repeat_share_of_increase_pct": top.get("repeat_share_of_increase_pct"),
            "upside_next_period": top["upside_next_period"],
        }
    if not ordered:
        notes.append("no_growth_signals")
    return Result(table, facts, notes, [info.table], list(analysis.sql_log))


# Words that name how a business splits itself (most telling first), and words that never do.
_SEGMENT_WORDS = (
    "region",
    "category",
    "product_type",
    "type",
    "channel",
    "segment",
    "outlet",
    "store",
    "branch",
    "state",
    "city",
    "product",
)
_NOT_SEGMENTS = (
    "status",
    "gateway",
    "currency",
    "method",
    "flag",
    "sku",
    "title",
    "name",
    "email",
    "phone",
)


def default_dimensions(analysis: Analysis, info: MetricInfo) -> list[str]:
    """Dimension columns of the metric's table that look like business segments (region, category, channel...)
    and have a handful of values (2 to ``MAX_DISTINCT``)."""
    entry = analysis.dictionary.datasets[info.table]

    def rank(name: str) -> int | None:
        text = " ".join([name, *entry.columns[name].synonyms]).lower()
        if any(word in name.lower() for word in _NOT_SEGMENTS):
            return None
        return next(
            (i for i, word in enumerate(_SEGMENT_WORDS) if word in text), len(_SEGMENT_WORDS)
        )

    candidates = [
        name
        for name, column in entry.columns.items()
        if column.role == "dimension" and rank(name) is not None
    ]
    if not candidates:
        return []
    frame_for(analysis, info.table)
    counts = analysis.frame(
        "SELECT "  # noqa: S608
        + ", ".join(f"count(DISTINCT {q(c)}) AS {q('n_' + c)}" for c in candidates)
        + f" FROM {q(info.table)}"
    )
    sized = [
        (rank(c), int(counts[f"n_{c}"].iloc[0]), c)
        for c in candidates
        if 2 <= int(counts[f"n_{c}"].iloc[0]) <= MAX_DISTINCT
    ]
    return [c for *_, c in sorted(sized)][:MAX_DIMENSIONS]


# ------------------------------------------------------------------------------------------- helpers
def _complete(period: Period, grain: Grain) -> Period:
    """The period cut back to whole buckets: a bucket still running at ``period.end`` is left out."""
    end = pd.Timestamp(period.end_exclusive)
    start_of_last = pd.Timestamp(period.end).to_period(_freq(grain)).start_time
    bucket_end = (pd.Timestamp(period.end).to_period(_freq(grain)) + 1).start_time
    if end < bucket_end:
        cut = (start_of_last - pd.Timedelta(days=1)).date()
        if cut < period.start:
            raise AnalyticsError("bad_period", "no complete period")
        return Period(period.start, cut)
    return period


def _freq(grain: Grain) -> str:
    return {"week": "W-SUN", "month": "M", "quarter": "Q"}[grain]


def _series(
    analysis: Analysis,
    info: MetricInfo,
    period: Period,
    grain: Grain,
    group: list[str],
    filters: Filters | None,
    top_values: int = TOP_VALUES,
) -> Any:
    """The metric per bucket: a Series for the whole, or ``{(value, ...): Series}`` for a group of dimensions."""
    frame = frame_for(analysis, info.table)
    columns = [frame.column(g) for g in group]
    clause, params = where(frame, filters, time_column=info.time_column, period=period)
    base = frame.base_sql()
    bucket = bucket_sql(info.time_column or "", grain)
    if not columns:
        sql = (
            f"SELECT {bucket} AS bucket, ({info.sql}) AS value FROM ({base}) AS b {clause} "  # noqa: S608
            "GROUP BY 1 ORDER BY 1"
        )
        found = analysis.frame(sql, params)
        return pd.Series(
            [number(v) or 0.0 for v in found["value"]],
            index=pd.to_datetime(found["bucket"]).dt.date,
            dtype=float,
        )
    tops: list[list[str]] = []
    for column in columns:
        sql = (
            f"SELECT CAST({q(column)} AS VARCHAR) AS v, ({info.sql}) AS value "  # noqa: S608
            f"FROM ({base}) AS b {clause} GROUP BY 1 ORDER BY abs(value) DESC NULLS LAST "
            f"LIMIT {int(top_values)}"
        )
        tops.append([str(v) for v in analysis.frame(sql, params)["v"] if isinstance(v, str)])
    labels = ", ".join(
        f"CAST({q(c)} AS VARCHAR) AS {q('d' + str(i))}" for i, c in enumerate(columns)
    )
    keys = ", ".join(str(i + 2) for i in range(len(columns)))
    sql = (
        f"SELECT {bucket} AS bucket, {labels}, ({info.sql}) AS value FROM ({base}) AS b {clause} "  # noqa: S608
        f"GROUP BY 1, {keys} ORDER BY 1"
    )
    found = analysis.frame(sql, params)
    found["bucket"] = pd.to_datetime(found["bucket"]).dt.date
    buckets = sorted(found["bucket"].unique())
    out: dict[tuple[str, ...], pd.Series] = {}
    for combo in product(*tops):
        mask = pd.Series(True, index=found.index)
        for i, value in enumerate(combo):
            mask &= found[f"d{i}"] == value
        part = found[mask]
        series = pd.Series(
            [number(v) or 0.0 for v in part["value"]], index=part["bucket"], dtype=float
        )
        out[tuple(combo)] = series.reindex(buckets, fill_value=0.0)
    return out


def _streak(points: pd.Series, total: pd.Series, min_streak: int) -> dict[str, Any] | None:
    points = points.reindex(total.index, fill_value=0.0)
    if len(points) < min_streak + 1:
        return None
    share = points / total.where(total != 0)
    length = 0
    for i in range(len(points) - 1, 0, -1):
        rose = points.iloc[i] > points.iloc[i - 1]
        gained = bool(share.iloc[i] > share.iloc[i - 1])
        if not (rose and gained):
            break
        length += 1
    if length < min_streak:
        return None
    start_index = len(points) - 1 - length
    start, end = float(points.iloc[start_index]), float(points.iloc[-1])
    grown = change_pct(end, start)
    if (grown is not None and grown < MIN_GROWTH_PCT) or float(
        share.iloc[-1] or 0
    ) * 100 < MIN_SHARE_PCT:
        return None
    return {
        "periods": length,
        "start_period": str(points.index[start_index]),
        "end_period": str(points.index[-1]),
        "start_value": start,
        "end_value": end,
        "change": end - start,
        "change_pct": grown,
        "share_start_pct": float(share.iloc[start_index]) * 100,
        "share_end_pct": float(share.iloc[-1]) * 100,
        "upside_next_period": (end - start) / length,
        "series": {str(k): float(v) for k, v in points.iloc[start_index:].items()},
    }


def _beats_last_year(
    analysis: Analysis,
    info: MetricInfo,
    window: Period,
    grain: Grain,
    filters: Filters | None,
    min_streak: int,
) -> dict[str, Any] | None:
    lookback = Period((pd.Timestamp(window.start) - pd.DateOffset(years=1)).date(), window.end)
    series = _series(analysis, info, lookback, grain, [], filters)
    if len(series) < min_streak:
        return None
    by_bucket = dict(series.items())
    length = 0
    growth: list[float] = []
    for bucket in reversed(list(series.index)):
        if bucket < window.start:
            break
        before = by_bucket.get((pd.Timestamp(bucket) - pd.DateOffset(years=1)).date())
        value = by_bucket[bucket]
        if before is None or before <= 0 or value <= before:
            break
        length += 1
        growth.append((value / before - 1) * 100)
    if length < min_streak:
        return None
    last = series.index[-1]
    before_last = by_bucket[(pd.Timestamp(last) - pd.DateOffset(years=1)).date()]
    return {
        "kind": "beats_last_year",
        "segment": {},
        "periods": length,
        "start_period": str(series.index[-length]),
        "end_period": str(last),
        "start_value": before_last,
        "end_value": float(series.iloc[-1]),
        "change": float(series.iloc[-1]) - before_last,
        "change_pct": change_pct(float(series.iloc[-1]), before_last),
        "average_growth_vs_last_year_pct": sum(growth) / len(growth),
        "upside_next_period": None,
    }


def _without_echoes(streaks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = [s for s in streaks if len(s["segment"]) > 1]
    kept: list[dict[str, Any]] = []
    for s in streaks:
        single = len(s["segment"]) == 1
        echoed = single and any(
            set(s["segment"].items()) <= set(p["segment"].items())
            and abs(p["change"]) >= 0.5 * abs(s["change"])
            for p in pairs
        )
        if not echoed:
            kept.append(s)
    return kept


def _repeat_share(
    analysis: Analysis,
    info: MetricInfo,
    customer: str,
    row: dict[str, Any],
    grain: Grain,
    filters: Filters | None,
) -> float | None:
    """How much of the streak's increase came from customers first seen before the streak began."""
    frame = frame_for(analysis, info.table)
    column = frame.column(customer)
    segment_filters = {**(filters or {}), **row["segment"]}
    start = pd.Timestamp(row["start_period"]).date()
    end = pd.Timestamp(row["end_period"]).date()
    first = (
        f"(SELECT {q(column)} AS __c, min({q(info.time_column or '')}) AS __first "  # noqa: S608
        f"FROM {q(info.table)} GROUP BY 1)"
    )
    values: dict[str, dict[str, float]] = {}
    for label, bucket in (("start", start), ("end", end)):
        bucket_end = (pd.Timestamp(bucket).to_period(_freq(grain)) + 1).start_time.date()
        period = Period(bucket, bucket_end - pd.Timedelta(days=1).to_pytimedelta())
        clause, params = where(frame, segment_filters, time_column=info.time_column, period=period)
        sql = (
            f"SELECT CASE WHEN f.__first < ? THEN 'repeat' ELSE 'new' END AS kind, "  # noqa: S608
            f"({info.sql}) AS value FROM ({frame.base_sql()}) AS b "
            f"LEFT JOIN {first} AS f ON b.{q(column)} = f.__c {clause} GROUP BY 1"
        )
        found = analysis.frame(sql, [start, *params])
        values[label] = {
            str(k): number(v) or 0.0 for k, v in zip(found["kind"], found["value"], strict=True)
        }
    increase = sum(values["end"].values()) - sum(values["start"].values())
    if not increase:
        return None
    repeat = values["end"].get("repeat", 0.0) - values["start"].get("repeat", 0.0)
    return repeat / increase * 100.0


def _label(segment: dict[str, Any]) -> str:
    return " × ".join(str(v) for v in segment.values()) if segment else "all"  # noqa: RUF001
