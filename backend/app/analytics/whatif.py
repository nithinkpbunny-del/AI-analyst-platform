"""What-if simulator (BUILD_SPEC 10.4, R12; DECISIONS D71-D75).

A scenario is ``{lever, change, scope, horizon}``:

* ``price``: list price of the products in scope changes by ``change`` percent;
* ``discount``: the discount rate changes by ``change`` percentage points;
* ``marketing_spend``: spend on one channel (``scope={"channel": "YouTube"}``) changes by ``change`` percent.

Method (a driver model, all in code, no AI):

* revenue = units x net price. Units respond to price with an elasticity, so revenue moves by
  ``(1 + x) * (1 + x) ** e`` for a price change ``x``; a discount change moves the net price paid, and
  marketing revenue follows ``(1 + x) ** beta`` (diminishing returns).
* The elasticity is **estimated from the company's own history**: weekly units of the scope against the average
  list price and the average net-of-discount factor, in a log-log regression with a trend and annual seasonality as
  controls. It is used only when the history moved enough, the sign is right and the estimate is not noise.
  Otherwise the industry default is used and **labelled as an assumption**.
* The baseline is the forecast of the metric for the scope (``forecast.forecast``). The scenario is the baseline
  times the effect, with a low/expected/high range that combines the forecast range and the elasticity's 80% range.

Everything here is deterministic; the sliders on the What-if screen call it again with new numbers.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.analytics import forecast as forecast_module
from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    Result,
    bucket_sql,
    coverage,
    customer_column,
    frame_for,
    metric_info,
    where,
)
from app.analytics.marketing import campaign_tables, find_columns
from app.dictionary.model import DatasetEntry
from app.security.sql_guard import safe_identifier as q

LEVERS = ("price", "discount", "marketing_spend")
MAX_HORIZON = 6
MIN_WEEKS = 26
FULL_WEEKS = 52
Z80 = 1.2816
MIN_PRICE_MOVE = (
    0.04  # the log price must have moved by at least this much (range) to learn from it
)
MIN_DISCOUNT_MOVE = 0.03
MIN_T = 1.5
ELASTICITY_BOUNDS = (-6.0, -0.05)
MAX_DISCOUNT_RATE = 0.9
MAX_PRICE_CHANGE = 100.0  # percent
MAX_SPEND_CHANGE = 300.0
MIN_SPEND_POINTS = 12

# Industry defaults, clearly labelled assumptions when used (the templates of M17 may refine them).
# price: units per 1% of list price; discount: units per 1% of the net price paid; spend: revenue per 1% of spend.
DEFAULTS: dict[str, dict[str, float]] = {
    "d2c": {"price": -1.4, "discount": -1.8, "spend": 0.45},
    "retail": {"price": -1.0, "discount": -1.3, "spend": 0.35},
    "restaurant": {"price": -0.8, "discount": -1.0, "spend": 0.30},
    "fuel_station": {"price": -0.3, "discount": -0.4, "spend": 0.10},
    "services": {"price": -0.7, "discount": -0.9, "spend": 0.40},
    "manufacturing": {"price": -0.6, "discount": -0.8, "spend": 0.30},
    "other": {"price": -1.0, "discount": -1.3, "spend": 0.40},
}
DEFAULT_SPREAD = (1.5, 0.5)  # an assumption is shown from 1.5x to 0.5x of its value
SPEND_SPREAD = (0.5, 1.5)

ELASTICITY_KEY = {
    "price": "price_elasticity",
    "discount": "discount_elasticity",
    "marketing_spend": "spend_elasticity",
}

_ROLES: dict[str, re.Pattern[str]] = {
    "quantity": re.compile(r"^(quantity|qty|units|units_sold)$", re.I),
    "gross": re.compile(
        r"^(total_line_items_price|line_items_price|gross_(sales|amount|price)|subtotal_price|subtotal)$",
        re.I,
    ),
    "unit_price": re.compile(r"^(unit_price|price|list_price|selling_price|rate)$", re.I),
    "discount": re.compile(r"^(total_discounts?|discount(_amount)?|discounts?)$", re.I),
    "end": re.compile(r"^(end|to)(_date)?$", re.I),
}


@dataclass(frozen=True)
class Scenario:
    metric: str
    lever: str
    change: float
    scope: Filters = field(default_factory=dict)
    horizon: int = 3
    # Assumption values the person set (key -> value), e.g. {"price_elasticity": -0.8}.
    overrides: dict[str, float] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "lever": self.lever,
            "change": self.change,
            "scope": dict(self.scope),
            "horizon": self.horizon,
            "overrides": dict(self.overrides),
        }


@dataclass(frozen=True)
class Elasticity:
    value: float
    low: float  # the 80% range, as numbers (low <= value <= high)
    high: float
    source: str  # history | default | you
    t_value: float | None = None
    reason: str | None = None  # why history was not used


# ----------------------------------------------------------------------------------- the effect itself
def effect_factor(
    lever: str, change: float, elasticity: float, rate: float = 0.0
) -> dict[str, float]:
    """How much units and the revenue move for ``change`` (percent, or points for a discount).

    ``rate`` is the current discount rate (0-1), needed for the discount lever. Pure arithmetic, no data."""
    if lever == "price":
        units = (1.0 + change / 100.0) ** elasticity
        return {"units": units, "revenue": (1.0 + change / 100.0) * units}
    if lever == "discount":
        new_rate = min(max(rate + change / 100.0, 0.0), MAX_DISCOUNT_RATE)
        net = (1.0 - new_rate) / (1.0 - rate)
        units = net**elasticity
        return {"units": units, "revenue": net * units, "net_price": net, "new_rate": new_rate}
    if lever == "marketing_spend":
        factor = (1.0 + change / 100.0) ** elasticity
        return {"units": factor, "revenue": factor}
    raise AnalyticsError("bad_lever", lever)


def default_elasticity(industry: str | None, lever: str) -> Elasticity:
    table = DEFAULTS.get(industry or "other", DEFAULTS["other"])
    key = "spend" if lever == "marketing_spend" else lever
    value = table[key]
    first, second = SPEND_SPREAD if lever == "marketing_spend" else DEFAULT_SPREAD
    low, high = sorted((value * first, value * second))
    return Elasticity(value, low, high, "default")


def check(scenario: Scenario) -> None:
    if scenario.lever not in LEVERS:
        raise AnalyticsError("bad_lever", scenario.lever)
    if not np.isfinite(scenario.change):
        raise AnalyticsError("bad_change", str(scenario.change))
    if scenario.lever == "price" and not -90.0 <= scenario.change <= MAX_PRICE_CHANGE:
        raise AnalyticsError("bad_change", f"price {scenario.change}")
    if scenario.lever == "discount" and not -50.0 <= scenario.change <= 50.0:
        raise AnalyticsError("bad_change", f"discount {scenario.change}")
    if scenario.lever == "marketing_spend" and not -90.0 <= scenario.change <= MAX_SPEND_CHANGE:
        raise AnalyticsError("bad_change", f"spend {scenario.change}")
    if not 1 <= scenario.horizon <= MAX_HORIZON:
        raise AnalyticsError("bad_horizon", str(scenario.horizon))
    allowed = {ELASTICITY_KEY[scenario.lever]}
    for key, value in scenario.overrides.items():
        if key not in allowed:
            raise AnalyticsError("bad_assumption", key)
        if not np.isfinite(value) or not -10.0 <= value <= 10.0:
            raise AnalyticsError("bad_assumption", f"{key}={value}")
        if key != "spend_elasticity" and value > 0:
            raise AnalyticsError("bad_assumption", f"{key}={value}")
        if key == "spend_elasticity" and value < 0:
            raise AnalyticsError("bad_assumption", f"{key}={value}")


# ---------------------------------------------------------------------------------------- the columns
def sales_columns(entry: DatasetEntry) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, column in entry.columns.items():
        for role, pattern in _ROLES.items():
            if role in found:
                continue
            words = [name, column.label, *column.synonyms]
            if any(pattern.match(re.sub(r"[^a-z0-9]+", "_", w.lower()).strip("_")) for w in words):
                found[role] = name
    return found


def _sums(cols: dict[str, str], lever: str) -> tuple[str, str, str]:
    """SQL for units, gross sales and discounts of a group of rows."""
    units = f"SUM({q(cols['quantity'])})" if "quantity" in cols else "COUNT(*)"
    if "gross" in cols:
        gross = f"SUM({q(cols['gross'])})"
    elif "unit_price" in cols:
        quantity = q(cols["quantity"]) if "quantity" in cols else "1"
        gross = f"SUM({q(cols['unit_price'])} * {quantity})"
    else:
        raise AnalyticsError("lever_not_supported", f"{lever}: no price column")
    if lever == "discount":
        if "discount" not in cols:
            raise AnalyticsError("lever_not_supported", "discount: no discount column")
        return units, gross, f"SUM({q(cols['discount'])})"
    return units, gross, "0"


# ------------------------------------------------------------------------------------- the estimation
@dataclass
class Fit:
    elasticities: dict[str, Elasticity]
    weeks: int
    start: str | None
    end: str | None
    r_squared: float | None
    discount_rate: float | None  # the recent discount rate of the scope (0-1)
    notes: list[str] = field(default_factory=list)


def _weekly(
    analysis: Analysis,
    table: str,
    time_column: str,
    cols: dict[str, str],
    lever: str,
    scope: Filters,
) -> pd.DataFrame:
    frame = frame_for(analysis, table)
    units, gross, discount = _sums(cols, lever)
    clause, params = where(frame, scope, time_column=time_column)
    bucket = bucket_sql(time_column, "week")
    data = analysis.frame(
        f"SELECT {bucket} AS bucket, {units} AS units, {gross} AS gross, {discount} AS discount "  # noqa: S608
        f"FROM ({frame.base_sql()}) AS b {clause} GROUP BY 1 ORDER BY 1",
        params,
    )
    for column in ("units", "gross", "discount"):
        data[column] = pd.to_numeric(data[column], errors="coerce").astype("float64")
    data["bucket"] = pd.to_datetime(data["bucket"])
    return data


def fit_history(
    analysis: Analysis,
    table: str,
    time_column: str,
    lever: str,
    scope: Filters,
    industry: str | None,
) -> Fit:
    """Estimate the unit response of the scope from its weekly history, or fall back to the default."""
    entry = analysis.dictionary.datasets[table]
    cols = sales_columns(entry)
    weekly = _weekly(analysis, table, time_column, cols, lever, scope)
    default = default_elasticity(industry, lever)
    _, last_day = coverage(analysis, table)
    if (
        last_day is not None
        and len(weekly)
        and pd.Timestamp(last_day) < weekly["bucket"].iloc[-1] + pd.Timedelta(days=6)
    ):
        weekly = weekly.iloc[:-1]  # the last week is not complete
    weekly = weekly[(weekly["units"] > 0) & (weekly["gross"] > 0)].reset_index(drop=True)
    notes: list[str] = []
    if len(weekly) < MIN_WEEKS:
        return Fit(
            {lever: _with_reason(default, "too_little_history")},
            len(weekly),
            None,
            None,
            None,
            _rate(weekly),
            notes,
        )
    weekly["price"] = weekly["gross"] / weekly["units"]
    weekly["keep"] = 1.0 - (weekly["discount"] / weekly["gross"]).clip(0.0, MAX_DISCOUNT_RATE)
    regressor = "price" if lever == "price" else "keep"
    ptp = float(np.ptp(np.log(weekly[regressor])))
    need = MIN_PRICE_MOVE if lever == "price" else MIN_DISCOUNT_MOVE
    if ptp < need:
        return Fit(
            {lever: _with_reason(default, "history_did_not_move")},
            len(weekly),
            None,
            None,
            None,
            _rate(weekly),
            notes,
        )
    estimate, r2 = _regress(weekly, regressor)
    start, end = (
        weekly["bucket"].iloc[0].date().isoformat(),
        weekly["bucket"].iloc[-1].date().isoformat(),
    )
    if estimate is None:
        return Fit(
            {lever: _with_reason(default, "history_inconclusive")},
            len(weekly),
            start,
            end,
            r2,
            _rate(weekly),
            notes,
        )
    value, se = estimate
    t_value = abs(value) / se if se > 0 else float("inf")
    if not ELASTICITY_BOUNDS[0] <= value <= ELASTICITY_BOUNDS[1] or t_value < MIN_T:
        return Fit(
            {lever: _with_reason(default, "history_inconclusive")},
            len(weekly),
            start,
            end,
            r2,
            _rate(weekly),
            notes,
        )
    low, high = value - Z80 * se, value + Z80 * se
    low = max(low, ELASTICITY_BOUNDS[0])
    high = min(high, ELASTICITY_BOUNDS[1])
    return Fit(
        {lever: Elasticity(value, low, high, "history", t_value)},
        len(weekly),
        start,
        end,
        r2,
        _rate(weekly),
        notes,
    )


def _with_reason(elasticity: Elasticity, reason: str) -> Elasticity:
    return Elasticity(
        elasticity.value, elasticity.low, elasticity.high, elasticity.source, None, reason
    )


def _rate(weekly: pd.DataFrame) -> float | None:
    recent = weekly.tail(8)
    gross = float(recent["gross"].sum()) if len(recent) else 0.0
    if gross <= 0 or float(weekly["discount"].sum()) == 0.0:
        return None if gross <= 0 else 0.0
    return float(recent["discount"].sum() / gross)


def _controls(weekly: pd.DataFrame, t: np.ndarray, kind: str, logged: np.ndarray) -> np.ndarray:
    n = len(weekly)
    columns: list[np.ndarray] = [np.ones(n), t]
    if kind == "months" and n >= FULL_WEEKS:
        month = weekly["bucket"].dt.month.to_numpy()
        columns += [(month == m).astype("float64") for m in range(2, 13) if (month == m).any()]
    else:
        day = weekly["bucket"].dt.dayofyear.to_numpy(dtype="float64")
        for k in (1, 2) if n >= 2 * FULL_WEEKS else (1,):
            angle = 2.0 * np.pi * k * day / 365.25
            columns += [np.sin(angle), np.cos(angle)]
    columns.append(logged)
    return np.column_stack(columns)


def _regress(
    weekly: pd.DataFrame, regressor: str
) -> tuple[tuple[float, float] | None, float | None]:
    """ln(units) on ln(regressor) with a trend and annual seasonality; the coefficient and its standard error."""
    import statsmodels.api as sm

    n = len(weekly)
    t = np.arange(n, dtype="float64") / FULL_WEEKS
    logged = np.log(weekly[regressor].to_numpy(dtype="float64"))
    design = _controls(weekly, t, "months", logged)
    if np.linalg.matrix_rank(design) < design.shape[1] or n <= design.shape[1] + 3:
        # A year of weeks cannot tell a month's season from a one-off: fall back to smooth seasonality.
        design = _controls(weekly, t, "harmonics", logged)
    if np.linalg.matrix_rank(design) < design.shape[1] or n <= design.shape[1] + 3:
        return None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = sm.OLS(np.log(weekly["units"].to_numpy(dtype="float64")), design).fit()
    value, se = float(model.params[-1]), float(model.bse[-1])
    if not (np.isfinite(value) and np.isfinite(se)):
        return None, None
    return (value, se), float(model.rsquared)


def estimate_spend(
    analysis: Analysis, channel: str, industry: str | None
) -> tuple[Elasticity, int]:
    """Revenue response to spend, from the channels' own history (fixed effect per channel), else the default."""
    default = default_elasticity(industry, "marketing_spend")
    points = _channel_rows(analysis, None)
    if points.empty or len(points) < MIN_SPEND_POINTS:
        return _with_reason(default, "too_little_history"), len(points)
    points = points[(points["spend"] > 0) & (points["revenue"] > 0)].copy()
    points["x"] = np.log(points["spend"])
    points["y"] = np.log(points["revenue"])
    points["x"] -= points.groupby("channel")["x"].transform("mean")
    points["y"] -= points.groupby("channel")["y"].transform("mean")
    sxx = float((points["x"] ** 2).sum())
    if sxx < 0.05 or len(points) < MIN_SPEND_POINTS:
        return _with_reason(default, "history_did_not_move"), len(points)
    slope = float((points["x"] * points["y"]).sum() / sxx)
    residual = points["y"] - slope * points["x"]
    dof = max(len(points) - points["channel"].nunique() - 1, 1)
    se = float(np.sqrt((residual**2).sum() / dof / sxx))
    t_value = abs(slope) / se if se > 0 else float("inf")
    if not 0.0 < slope <= 1.5 or t_value < MIN_T:
        return _with_reason(default, "history_inconclusive"), len(points)
    return Elasticity(
        slope, max(slope - Z80 * se, 0.0), min(slope + Z80 * se, 1.5), "history", t_value
    ), len(points)


def _channel_rows(analysis: Analysis, channel: str | None) -> pd.DataFrame:
    """Spend and attributed revenue of every campaign row (one per channel per period) across the campaign tables."""
    parts: list[pd.DataFrame] = []
    for key in campaign_tables(analysis):
        entry = analysis.dictionary.datasets[key]
        cols = find_columns(entry)
        if "revenue" not in cols or "channel" not in cols or not entry.time_column:
            continue
        analysis.table(key)
        end = sales_columns(entry).get("end")
        end_sql = f"CAST({q(end)} AS DATE)" if end else "NULL"
        parts.append(
            analysis.frame(
                f"SELECT CAST({q(cols['channel'])} AS VARCHAR) AS channel, "  # noqa: S608
                f"CAST({q(entry.time_column)} AS DATE) AS start, {end_sql} AS finish, "
                f"SUM({q(cols['spend'])}) AS spend, SUM({q(cols['revenue'])}) AS revenue "
                f"FROM {q(key)} GROUP BY 1, 2, 3"
            )
        )
    if not parts:
        return pd.DataFrame(columns=["channel", "start", "finish", "spend", "revenue"])
    data = pd.concat(parts, ignore_index=True)
    for column in ("spend", "revenue"):
        data[column] = pd.to_numeric(data[column], errors="coerce").astype("float64")
    data["start"] = pd.to_datetime(data["start"])
    data["finish"] = pd.to_datetime(data["finish"])
    if channel is not None:
        data = data[data["channel"].str.lower() == channel.lower()]
    return data.dropna(subset=["spend", "revenue"]).sort_values("start").reset_index(drop=True)


# ------------------------------------------------------------------------------------------ the run
def what_if(analysis: Analysis, scenario: Scenario, industry: str | None = None) -> Result:
    check(scenario)
    if scenario.lever == "marketing_spend":
        return _marketing(analysis, scenario, industry)
    return _sales(analysis, scenario, industry)


def _sales(analysis: Analysis, scenario: Scenario, industry: str | None) -> Result:
    info = metric_info(analysis.dictionary, scenario.metric)
    if not info.time_column:
        raise AnalyticsError("no_time_column", info.table)
    if not info.additive:
        raise AnalyticsError("metric_not_supported", scenario.metric)
    kind = "money" if info.unit == "INR" else "count" if info.unit == "count" else None
    if kind is None:
        raise AnalyticsError("metric_not_supported", scenario.metric)
    lever = scenario.lever
    fit = fit_history(analysis, info.table, info.time_column, lever, scenario.scope, industry)
    elasticity = fit.elasticities[lever]
    key = ELASTICITY_KEY[lever]
    if key in scenario.overrides:
        value = scenario.overrides[key]
        elasticity = Elasticity(value, value, value, "you")
    rate = fit.discount_rate if fit.discount_rate is not None else 0.0
    if lever == "discount" and fit.discount_rate is None:
        raise AnalyticsError("no_gross_sales", info.table)

    base = forecast_module.forecast(
        analysis, scenario.metric, "month", scenario.horizon, scenario.scope or None
    )
    future = base.table[base.table["kind"] == "forecast"].reset_index(drop=True)
    metric_key = "revenue" if kind == "money" else "units"

    def factor(e: float) -> float:
        return effect_factor(lever, scenario.change, e, rate)[metric_key]

    f_exp = factor(elasticity.value)
    f_all = [factor(elasticity.low), factor(elasticity.high), f_exp]
    f_low, f_high = min(f_all), max(f_all)
    table = pd.DataFrame(
        {
            "bucket": future["bucket"],
            "baseline": future["value"],
            "baseline_low": future["low"],
            "baseline_high": future["high"],
            "scenario": future["value"] * f_exp,
            "scenario_low": future["low"] * f_low,
            "scenario_high": future["high"] * f_high,
        }
    )
    base_total = float(table["baseline"].sum())
    full = effect_factor(lever, scenario.change, elasticity.value, rate)
    side = _side_effects(analysis, info, scenario, full, rate, base_total, kind)
    assumptions = _assumptions(scenario, elasticity, fit, rate, base)
    confidence = _confidence(elasticity, fit, base.facts.get("backtest_mape_pct"))
    facts: dict[str, Any] = {
        "scenario": scenario.as_json(),
        "metric": scenario.metric,
        "unit": info.unit,
        "horizon_months": scenario.horizon,
        "baseline_method": base.facts.get("method"),
        "baseline_total": base_total,
        "baseline_low": float(table["baseline_low"].sum()),
        "baseline_high": float(table["baseline_high"].sum()),
        "scenario_total": float(table["scenario"].sum()),
        "scenario_low": float(table["scenario_low"].sum()),
        "scenario_high": float(table["scenario_high"].sum()),
        "delta_expected": base_total * (f_exp - 1.0),
        "delta_low": base_total * (f_low - 1.0),
        "delta_high": base_total * (f_high - 1.0),
        "delta_pct_expected": (f_exp - 1.0) * 100.0,
        "delta_pct_low": (f_low - 1.0) * 100.0,
        "delta_pct_high": (f_high - 1.0) * 100.0,
        "units_change_pct": (full["units"] - 1.0) * 100.0,
        "range": "80%",
        "assumptions": assumptions,
        "confidence": confidence,
        "informed_by": {
            "source": elasticity.source,
            "weeks": fit.weeks,
            "start": fit.start if elasticity.source == "history" else None,
            "end": fit.end if elasticity.source == "history" else None,
        },
        "side_effects": side,
        "backtest_mape_pct": base.facts.get("backtest_mape_pct"),
    }
    notes = [*base.notes, "what_if_assumptions"]
    if elasticity.source == "default":
        notes.append("what_if_default_assumption")
    return Result(table, facts, notes, [info.table], list(analysis.sql_log))


def _assumptions(
    scenario: Scenario, elasticity: Elasticity, fit: Fit, rate: float, base: Result
) -> list[dict[str, Any]]:
    key = ELASTICITY_KEY[scenario.lever]
    items: list[dict[str, Any]] = [
        {
            "key": key,
            "value": elasticity.value,
            "low": elasticity.low,
            "high": elasticity.high,
            "source": elasticity.source,  # history | default | you
            "editable": True,
            "reason": elasticity.reason,
            "t_value": elasticity.t_value,
        }
    ]
    if scenario.lever == "discount":
        items.append(
            {
                "key": "current_discount_rate",
                "value": rate * 100.0,
                "source": "history",
                "editable": False,
            }
        )
    items.append(
        {
            "key": "baseline_method",
            "value": base.facts.get("method"),
            "source": "history",
            "editable": False,
        }
    )
    items.append(
        {"key": "change_starts_now", "value": True, "source": "default", "editable": False}
    )
    items.append(
        {"key": "nothing_else_changes", "value": True, "source": "default", "editable": False}
    )
    return items


def _confidence(elasticity: Elasticity, fit: Fit, mape: float | None) -> dict[str, str]:
    if elasticity.source == "you":
        level, reason = "medium", "your_assumption"
    elif elasticity.source == "default":
        level, reason = "low", elasticity.reason or "default_assumption"
    else:
        t = elasticity.t_value or 0.0
        if t >= 3.0 and fit.weeks >= FULL_WEEKS and (fit.r_squared or 0.0) >= 0.5:
            level, reason = "high", "clear_history"
        elif t >= 2.0:
            level, reason = "medium", "some_history"
        else:
            level, reason = "low", "weak_history"
    if mape is not None and mape > 25.0 and level != "low":
        level, reason = ("medium" if level == "high" else "low"), "uncertain_baseline"
    return {"level": level, "reason": reason}


def _side_effects(
    analysis: Analysis,
    info: Any,
    scenario: Scenario,
    full: dict[str, float],
    rate: float,
    base_total: float,
    kind: str,
) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = [{"code": "units_change", "pct": (full["units"] - 1.0) * 100.0}]
    if scenario.lever == "discount" and kind == "money":
        # The discount given away: units x gross price x rate, before and after.
        gross_per_unit_base = base_total / max(1.0 - rate, 1e-9)
        before = gross_per_unit_base * rate
        after = gross_per_unit_base * full["units"] * full["new_rate"]
        effects.append({"code": "discount_given", "before": before, "after": after})
    try:
        share = _repeat_share(analysis, info, scenario.scope)
    except AnalyticsError:
        share = None
    if share is not None:
        effects.append({"code": "repeat_exposure", "share_pct": share})
    return effects


def _repeat_share(analysis: Analysis, info: Any, scope: Filters) -> float | None:
    """Share of the metric (last 12 months, in scope) from customers who ordered more than once."""
    customer = customer_column(analysis, info.table)
    if not customer or not info.time_column:
        return None
    frame = frame_for(analysis, info.table)
    _, last_day = coverage(analysis, info.table)
    if last_day is None:
        return None
    clause, params = where(frame, scope)
    window = f"{q(info.time_column)} > CAST(? AS DATE) - INTERVAL 365 DAY"
    clause = f"{clause} AND {window}" if clause else f"WHERE {window}"
    params = [*params, pd.Timestamp(last_day).date()]
    found = analysis.frame(
        f"SELECT {q(customer)} AS customer, COUNT(*) AS n, ({info.sql}) AS value "  # noqa: S608
        f"FROM ({frame.base_sql()}) AS b {clause} GROUP BY 1",
        params,
    )
    if found.empty:
        return None
    value = pd.to_numeric(found["value"], errors="coerce").fillna(0.0)
    total = float(value.sum())
    if total <= 0:
        return None
    return float(value[found["n"] >= 2].sum() / total * 100.0)


# --------------------------------------------------------------------------------------- marketing
def _marketing(analysis: Analysis, scenario: Scenario, industry: str | None) -> Result:
    channel = scenario.scope.get("channel")
    if not isinstance(channel, str) or not channel.strip() or len(scenario.scope) != 1:
        raise AnalyticsError("needs_channel", "scope must be {'channel': <name>}")
    if not campaign_tables(analysis):
        raise AnalyticsError("no_marketing_data")
    rows = _channel_rows(analysis, channel)
    if rows.empty:
        raise AnalyticsError("unknown_channel", channel)
    elasticity, points = estimate_spend(analysis, channel, industry)
    key = ELASTICITY_KEY["marketing_spend"]
    if key in scenario.overrides:
        value = scenario.overrides[key]
        elasticity = Elasticity(value, value, value, "you")
    recent = rows.iloc[-1]
    days = (
        (recent["finish"] - recent["start"]).days + 1
        if pd.notna(recent["finish"]) and recent["finish"] >= recent["start"]
        else 30
    )
    months = max(days / 30.4, 0.5)
    monthly_spend = float(recent["spend"]) / months
    monthly_revenue = float(recent["revenue"]) / months
    history = rows.assign(
        months=[
            (max(((f - s).days + 1) / 30.4, 0.5) if pd.notna(f) and f >= s else 1.0)
            for s, f in zip(rows["start"], rows["finish"], strict=True)
        ]
    )
    per_month = history["revenue"] / history["months"]
    spread = float(per_month.std(ddof=1)) if len(per_month) > 2 else monthly_revenue * 0.2
    spread = max(spread, monthly_revenue * 0.05)

    def revenue_factor(e: float) -> float:
        return effect_factor("marketing_spend", scenario.change, e)["revenue"]

    f_exp = revenue_factor(elasticity.value)
    candidates = [revenue_factor(elasticity.low), revenue_factor(elasticity.high), f_exp]
    f_low, f_high = min(candidates), max(candidates)
    first = (
        pd.Timestamp(recent["finish"])
        if pd.notna(recent["finish"])
        else pd.Timestamp(recent["start"])
    ) + pd.Timedelta(days=1)
    index = pd.date_range(
        first.normalize().replace(day=1) if first.day != 1 else first,
        periods=scenario.horizon,
        freq="MS",
    )
    steps = np.arange(1, scenario.horizon + 1)
    base_low = np.maximum(monthly_revenue - Z80 * spread * np.sqrt(steps), 0.0)
    base_high = monthly_revenue + Z80 * spread * np.sqrt(steps)
    table = pd.DataFrame(
        {
            "bucket": index.date,
            "baseline": monthly_revenue,
            "baseline_low": base_low,
            "baseline_high": base_high,
            "scenario": monthly_revenue * f_exp,
            "scenario_low": base_low * f_low,
            "scenario_high": base_high * f_high,
        }
    )
    horizon = scenario.horizon
    extra_spend = monthly_spend * (scenario.change / 100.0) * horizon
    base_total = monthly_revenue * horizon
    facts: dict[str, Any] = {
        "scenario": scenario.as_json(),
        "metric": "channel_revenue",
        "unit": "INR",
        "channel": channel,
        "horizon_months": horizon,
        "baseline_method": "latest_period_run_rate",
        "baseline_total": base_total,
        "baseline_low": float(table["baseline_low"].sum()),
        "baseline_high": float(table["baseline_high"].sum()),
        "scenario_total": float(table["scenario"].sum()),
        "scenario_low": float(table["scenario_low"].sum()),
        "scenario_high": float(table["scenario_high"].sum()),
        "delta_expected": base_total * (f_exp - 1.0),
        "delta_low": base_total * (f_low - 1.0),
        "delta_high": base_total * (f_high - 1.0),
        "delta_pct_expected": (f_exp - 1.0) * 100.0,
        "delta_pct_low": (f_low - 1.0) * 100.0,
        "delta_pct_high": (f_high - 1.0) * 100.0,
        "units_change_pct": None,
        "range": "80%",
        "monthly_spend": monthly_spend,
        "extra_spend": extra_spend,
        "net_after_spend": base_total * (f_exp - 1.0) - extra_spend,
        "current_roas": monthly_revenue / monthly_spend if monthly_spend else None,
        "assumptions": [
            {
                "key": key,
                "value": elasticity.value,
                "low": elasticity.low,
                "high": elasticity.high,
                "source": elasticity.source,
                "editable": True,
                "reason": elasticity.reason,
                "t_value": elasticity.t_value,
            },
            {
                "key": "baseline_method",
                "value": "latest_period_run_rate",
                "source": "history",
                "editable": False,
            },
            {"key": "attribution_limits", "value": True, "source": "default", "editable": False},
            {"key": "nothing_else_changes", "value": True, "source": "default", "editable": False},
        ],
        "confidence": _marketing_confidence(elasticity, points),
        "informed_by": {
            "source": elasticity.source,
            "weeks": None,
            "start": None,
            "end": None,
            "campaign_rows": points,
        },
        "side_effects": [{"code": "extra_spend", "amount": extra_spend}],
        "backtest_mape_pct": None,
    }
    notes = ["what_if_assumptions", "attribution_limits"]
    if elasticity.source == "default":
        notes.append("what_if_default_assumption")
    return Result(table, facts, notes, campaign_tables(analysis), list(analysis.sql_log))


def _marketing_confidence(elasticity: Elasticity, points: int) -> dict[str, str]:
    if elasticity.source == "you":
        return {"level": "medium", "reason": "your_assumption"}
    if elasticity.source == "default":
        return {"level": "low", "reason": elasticity.reason or "default_assumption"}
    # Attributed revenue per quarter is not an experiment: spend and revenue rise together by construction.
    return {"level": "low", "reason": "attribution_estimate"}
