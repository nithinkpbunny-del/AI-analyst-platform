"""Unusual values against a seasonal baseline (BUILD_SPEC 10.2; used by ``detect_anomalies`` and, from M10, the
briefings).

* A daily (or weekly/monthly) series of the metric, for the total and for each of the top values of a dimension.
* **Daily:** the baseline of a day is the same weekday over the previous 8 weeks: median and MAD.
  **Weekly/monthly:** a seasonal decomposition (STL) when the series has at least two seasons, else the median and
  MAD of the whole series.
* A point is flagged when its robust z-score (0.6745 x (value - median) / MAD) is at least ``z_threshold`` **and**
  the change is material: at least ``min_change_pct`` of the baseline and, for money, at least
  ``min_change_inr``.
* Flags are ranked by the size of the change (for money: rupees).
"""

from __future__ import annotations

from typing import Any

import numpy as np
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
    frame_for,
    metric_info,
    where,
)
from app.security.sql_guard import safe_identifier as q

BASELINE_WEEKS = 8
MIN_BASELINE_POINTS = 4
Z_THRESHOLD = 3.0
MIN_CHANGE_PCT = 10.0
MIN_CHANGE_INR = 10_000.0
TOP_VALUES = 5
MAX_FLAGS = 20
SEASON = {"week": 52, "month": 12, "quarter": 4}
# Share of the natural noise used as the floor; 0.8 was chosen on the demo history (DECISIONS D66): it keeps the
# planted App outage of 2026-08-15 and the missing Website week while flagging few ordinary days.
NOISE_FLOOR = 0.8


def detect_anomalies(
    analysis: Analysis,
    metric: str,
    period: Period,
    dimension: str | None = None,
    grain: Grain = "day",
    filters: Filters | None = None,
    *,
    z_threshold: float = Z_THRESHOLD,
    min_change_pct: float = MIN_CHANGE_PCT,
    min_change_inr: float = MIN_CHANGE_INR,
    top_values: int = TOP_VALUES,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    if grain not in ("day", "week", "month"):
        raise AnalyticsError("bad_grain", grain)
    # The baseline needs history before the period.
    history = Period(
        (pd.Timestamp(period.start) - pd.Timedelta(weeks=BASELINE_WEEKS + 1)).date()
        if grain == "day"
        else (pd.Timestamp(period.start) - pd.DateOffset(years=3)).date(),
        period.end,
    )
    series = _series(analysis, info, history, grain, dimension, filters, top_values)
    flags: list[dict[str, Any]] = []
    checked = 0
    for name, points in series.items():
        count = info.unit == "count"
        scored = (
            _score_daily(points, count=count)
            if grain == "day"
            else _score_seasonal(points, grain, count=count)
        )
        in_period = scored[
            (scored.index >= pd.Timestamp(period.start))
            & (scored.index <= pd.Timestamp(period.end))
        ]
        checked += len(in_period)
        for when, row in in_period.iterrows():
            if not _is_flag(row, info, z_threshold, min_change_pct, min_change_inr):
                continue
            flags.append(
                {
                    "series": name,
                    "bucket": pd.Timestamp(str(when)).date().isoformat(),
                    "weekday": pd.Timestamp(str(when)).day_name() if grain == "day" else None,
                    "value": float(row["value"]),
                    "baseline": float(row["baseline"]),
                    "change": float(row["value"] - row["baseline"]),
                    "change_pct": float((row["value"] - row["baseline"]) / row["baseline"] * 100.0)
                    if row["baseline"]
                    else None,
                    "z": float(row["z"]),
                    "direction": "up" if row["value"] > row["baseline"] else "down",
                }
            )
    flags.sort(key=lambda f: -abs(f["change"]))
    table = pd.DataFrame(flags[:MAX_FLAGS])
    facts = {
        "metric": metric,
        "unit": info.unit,
        "grain": grain,
        "period": period.as_json(),
        "dimension": dimension,
        "series_checked": list(series),
        "points_checked": checked,
        "flags": len(flags),
        "method": "same weekday, previous 8 weeks (median/MAD)"
        if grain == "day"
        else "STL or median/MAD",
        "thresholds": {
            "z": z_threshold,
            "min_change_pct": min_change_pct,
            "min_change_inr": min_change_inr if info.unit == "INR" else None,
        },
    }
    notes = [] if checked else ["nothing_to_check"]
    return Result(table, facts, notes, [info.table], list(analysis.sql_log))


def _series(
    analysis: Analysis,
    info: MetricInfo,
    history: Period,
    grain: Grain,
    dimension: str | None,
    filters: Filters | None,
    top_values: int,
) -> dict[str, pd.Series]:
    frame = frame_for(analysis, info.table)
    split = frame.column(dimension) if dimension else None
    clause, params = where(frame, filters, time_column=info.time_column, period=history)
    bucket = bucket_sql(info.time_column or "", grain)
    base = frame.base_sql()
    total = analysis.frame(
        f"SELECT {bucket} AS bucket, ({info.sql}) AS value, count(*) AS rows "  # noqa: S608
        f"FROM ({base}) AS b {clause} GROUP BY 1",
        params,
    )
    out = {"Total": _filled(total, grain)}
    if split:
        by_value = analysis.frame(
            f"SELECT CAST({q(split)} AS VARCHAR) AS series, {bucket} AS bucket, ({info.sql}) AS value, "  # noqa: S608
            f"count(*) AS rows FROM ({base}) AS b {clause} GROUP BY 1, 2",
            params,
        )
        volume = by_value.groupby("series")["value"].sum().abs().sort_values(ascending=False)
        for name in volume.index[:top_values]:
            if name is None or name is pd.NA:
                continue
            out[str(name)] = _filled(by_value[by_value["series"] == name], grain)
    return out


def _filled(found: pd.DataFrame, grain: Grain) -> pd.Series:
    """One value per bucket; a day (week, month) with no rows is 0 for counts and sums. ``attrs["per_row"]`` is
    the metric per row over the whole series (e.g. the average order value), used for the noise floor."""
    if found.empty:
        return pd.Series(dtype="float64")
    values = pd.to_numeric(found["value"], errors="coerce").astype("float64")
    series = pd.Series(values.to_numpy(), index=pd.to_datetime(found["bucket"])).sort_index()
    freq = {"day": "D", "week": "W-MON", "month": "MS"}[grain]
    full = pd.date_range(series.index.min(), series.index.max(), freq=freq)
    out = series.reindex(full).fillna(0.0)
    rows = float(pd.to_numeric(found["rows"], errors="coerce").sum())
    out.attrs["per_row"] = abs(float(values.sum()) / rows) if rows else 0.0
    return out


def _robust(
    values: np.ndarray, per_row: float = 0.0, *, count: bool = False
) -> tuple[float, float]:
    """Median and MAD of the baseline. The MAD never goes below the natural noise of a total of rows: a count of
    about ``m`` rows varies by about sqrt(m); a sum by about sqrt(2 x m x value per row) (``NOISE_FLOOR`` of it).
    Without this floor a
    quiet series of small numbers (12 orders a day) flags ordinary days."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if per_row > 0:
        noise = np.sqrt(abs(median) * (1.0 if count else 2.0 * per_row))
        mad = max(mad, 0.6745 * NOISE_FLOOR * float(noise))
    if mad == 0:
        # A flat baseline: fall back to the mean absolute deviation (scaled like MAD) so one change is not infinite.
        mad = float(np.mean(np.abs(values - median))) / 1.2533 or 1e-9
    return median, mad


def _score_daily(points: pd.Series, *, count: bool) -> pd.DataFrame:
    per_row = float(points.attrs.get("per_row", 0.0))
    rows = []
    for when, value in points.items():
        stamp = pd.Timestamp(str(when))
        past = [stamp - pd.Timedelta(weeks=w) for w in range(1, BASELINE_WEEKS + 1)]
        baseline = points.reindex(past).dropna().to_numpy()
        if len(baseline) < MIN_BASELINE_POINTS:
            continue
        median, mad = _robust(baseline, per_row, count=count)
        rows.append(
            {
                "when": stamp,
                "value": float(value),
                "baseline": median,
                "z": 0.6745 * (float(value) - median) / mad,
            }
        )
    return (
        pd.DataFrame(rows).set_index("when")
        if rows
        else pd.DataFrame(columns=["value", "baseline", "z"])
    )


def _score_seasonal(points: pd.Series, grain: Grain, *, count: bool) -> pd.DataFrame:
    period = SEASON.get(grain, 12)
    per_row = float(points.attrs.get("per_row", 0.0))
    if len(points) >= 2 * period + 1:
        from statsmodels.tsa.seasonal import STL

        fit = STL(points.to_numpy(), period=period, robust=True).fit()
        baseline = points.to_numpy() - fit.resid
        _, mad = _robust(fit.resid)
        z = 0.6745 * (fit.resid - np.median(fit.resid)) / mad
        return pd.DataFrame(
            {"value": points.to_numpy(), "baseline": baseline, "z": z}, index=points.index
        )
    values = points.to_numpy()
    if len(values) < MIN_BASELINE_POINTS:
        return pd.DataFrame(columns=["value", "baseline", "z"])
    median, mad = _robust(values, per_row, count=count)
    return pd.DataFrame(
        {"value": values, "baseline": median, "z": 0.6745 * (values - median) / mad},
        index=points.index,
    )


def _is_flag(row: pd.Series, info: MetricInfo, z: float, pct: float, inr: float) -> bool:
    if abs(row["z"]) < z:
        return False
    change = abs(row["value"] - row["baseline"])
    if row["baseline"] and change / abs(row["baseline"]) * 100.0 < pct:
        return False
    return not (info.unit == "INR" and change < inr)
