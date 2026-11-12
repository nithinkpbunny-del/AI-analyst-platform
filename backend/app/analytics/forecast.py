"""Metric forecast with a range (BUILD_SPEC 10.5).

* Holt-Winters (statsmodels exponential smoothing, additive trend and seasonality) when the series has at least two
  full seasonal cycles (24 months, 104 weeks, 14 days);
* otherwise a linear trend times a seasonal index (when there is at least one cycle) or a plain linear trend;
* an ~80% range from the spread of the in-sample errors, widening with the horizon;
* a backtest: the same method fitted without the last 3 periods, its error on them (MAPE) is reported.

An incomplete last bucket (the data ends mid-month) is left out of the fit, and says so.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    Grain,
    Period,
    Result,
    bucket_sql,
    coverage,
    frame_for,
    metric_info,
    where,
)

Z80 = 1.2816
SEASON = {"day": 7, "week": 52, "month": 12, "quarter": 4}
FREQ = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}
BACKTEST = 3
MAX_HORIZON = {"day": 90, "week": 26, "month": 12, "quarter": 8}
MIN_POINTS = 6


def forecast(
    analysis: Analysis,
    metric: str,
    grain: Grain = "month",
    horizon: int = 3,
    filters: Filters | None = None,
    history: Period | None = None,
) -> Result:
    info = metric_info(analysis.dictionary, metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    if grain not in SEASON:
        raise AnalyticsError("bad_grain", grain)
    horizon = max(1, min(int(horizon), MAX_HORIZON[grain]))
    frame = frame_for(analysis, info.table)
    clause, params = where(frame, filters, time_column=info.time_column, period=history)
    bucket = bucket_sql(info.time_column, grain)
    found = analysis.frame(
        f"SELECT {bucket} AS bucket, ({info.sql}) AS value FROM ({frame.base_sql()}) AS b {clause} GROUP BY 1 ORDER BY 1",  # noqa: S608
        params,
    )
    if found.empty:
        raise AnalyticsError("no_data", metric)
    series = pd.Series(
        pd.to_numeric(found["value"], errors="coerce").astype("float64").to_numpy(),
        index=pd.to_datetime(found["bucket"]),
    )
    series = series.reindex(
        pd.date_range(series.index.min(), series.index.max(), freq=FREQ[grain])
    ).fillna(0.0)
    notes: list[str] = []
    _, last_day = coverage(analysis, info.table)
    if last_day is not None and _incomplete(series.index[-1], last_day, grain):
        notes.append(f"partial_last_bucket_left_out:{series.index[-1].date().isoformat()}")
        series = series.iloc[:-1]
    if len(series) < MIN_POINTS:
        raise AnalyticsError("too_little_history", f"{len(series)} {grain}s")
    method, fitted, future = _fit(series, grain, horizon)
    residuals = series.to_numpy() - fitted
    spread = float(np.std(residuals[np.isfinite(residuals)], ddof=1)) if len(series) > 2 else 0.0
    mape, backtest_rmse = _backtest(series, grain)
    # An in-sample fit can look better than it is (one season fits its own index exactly): the range is never
    # narrower than the error the method made on the last periods it had not seen.
    spread = max(spread, backtest_rmse or 0.0)
    steps = np.arange(1, horizon + 1)
    low = future - Z80 * spread * np.sqrt(steps)
    high = future + Z80 * spread * np.sqrt(steps)
    if info.unit in ("INR", "count") and (series >= 0).all():
        low = np.maximum(low, 0.0)
    index = pd.date_range(series.index[-1], periods=horizon + 1, freq=FREQ[grain])[1:]
    history_rows = pd.DataFrame(
        {
            "bucket": pd.DatetimeIndex(series.index).date,
            "value": series.to_numpy(),
            "kind": "actual",
        }
    )
    forecast_rows = pd.DataFrame(
        {"bucket": index.date, "value": future, "low": low, "high": high, "kind": "forecast"}
    )
    table = pd.concat([history_rows, forecast_rows], ignore_index=True)
    facts: dict[str, Any] = {
        "metric": metric,
        "unit": info.unit,
        "grain": grain,
        "method": method,
        "horizon": horizon,
        "history_points": len(series),
        "range": "80%",
        "total_forecast": float(future.sum()),
        "total_low": float(low.sum()),
        "total_high": float(high.sum()),
        "backtest_mape_pct": mape,
        "backtest_periods": BACKTEST,
    }
    return Result(table, facts, notes, [info.table], list(analysis.sql_log))


def _incomplete(last_bucket: pd.Timestamp, last_day: Any, grain: Grain) -> bool:
    end = last_bucket + pd.tseries.frequencies.to_offset(FREQ[grain])
    return bool(pd.Timestamp(last_day) < end - pd.Timedelta(days=1))


def _fit(series: pd.Series, grain: Grain, horizon: int) -> tuple[str, np.ndarray, np.ndarray]:
    season = SEASON[grain]
    values = series.to_numpy()
    if len(values) >= 2 * season and season > 1:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                values,
                trend="add",
                seasonal="add",
                seasonal_periods=season,
                initialization_method="estimated",
            ).fit()
        return "holt_winters", np.asarray(model.fittedvalues), np.asarray(model.forecast(horizon))
    x = np.arange(len(values), dtype="float64")
    slope, intercept = np.polyfit(x, values, 1)
    trend = intercept + slope * x
    future_x = np.arange(len(values), len(values) + horizon, dtype="float64")
    future_trend = intercept + slope * future_x
    if len(values) >= season > 1 and np.all(trend > 0):
        ratios = values / trend
        position = np.arange(len(values)) % season
        index = np.array([ratios[position == p].mean() for p in range(season)])
        index = index / index.mean()
        future_position = (np.arange(len(values), len(values) + horizon)) % season
        return (
            "trend_x_seasonal_index",
            trend * index[position],
            future_trend * index[future_position],
        )
    return "linear_trend", trend, future_trend


def _backtest(series: pd.Series, grain: Grain) -> tuple[float | None, float | None]:
    """MAPE (%) and RMSE of the method fitted without the last ``BACKTEST`` periods."""
    if len(series) < MIN_POINTS + BACKTEST:
        return None, None
    train, test = series.iloc[:-BACKTEST], series.iloc[-BACKTEST:].to_numpy()
    _, _, predicted = _fit(train, grain, BACKTEST)
    rmse = float(np.sqrt(np.mean((test - predicted) ** 2)))
    mask = test != 0
    if not mask.any():
        return None, rmse
    return float(np.mean(np.abs((test[mask] - predicted[mask]) / test[mask])) * 100.0), rmse
