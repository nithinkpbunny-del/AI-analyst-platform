"""Goals against actuals, with a projection to the end of the goal's period (BUILD_SPEC X7, 10.1 ``goal_progress``).

For each goal: the metric's value from the start of the period up to the latest day of data (``as_of``), how much
of the period has passed, and where it is heading by the end:

* **Run-rate**: for a metric that adds up over days (a SUM or COUNT), the value so far scaled to the whole period.
* **Seasonal**: the same, but scaled by how last year's same period split between "so far" and "the rest", so a
  festive month that ends strong is not called short halfway through. Used when last year has data for the whole
  period; otherwise the run-rate is the projection.
* A metric that does not add up (an average such as AOV, a ratio) is projected as its value so far.

The status is decided by code, never by a model: ``not_started``, ``on_track``, ``at_risk`` (the projection misses
the target by less than ``AT_RISK_MARGIN``), ``behind``, ``achieved`` and ``missed``. A goal can be "at least"
(revenue, orders) or "at most" (refunds, discounts, costs). Every number comes from the workspace (P4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Filters,
    MetricInfo,
    Period,
    Result,
    coverage,
    metric_info,
    value_of,
)

Direction = Literal["at_least", "at_most"]
DIRECTIONS: tuple[Direction, ...] = ("at_least", "at_most")
Status = Literal["not_started", "on_track", "at_risk", "behind", "achieved", "missed", "no_data"]
# A projection that misses the target by less than this share of it is "at risk" rather than "behind".
AT_RISK_MARGIN = 0.10
MAX_GOALS = 20


@dataclass(frozen=True)
class GoalSpec:
    """One goal as the analytics need it (the database row is ``app.models.Goal``)."""

    metric: str
    target: float
    period: Period
    direction: Direction = "at_least"
    filters: Filters = field(default_factory=dict)
    goal_id: str | None = None
    name: str | None = None


def default_direction(info: MetricInfo) -> Direction:
    """A metric where lower is better (refunds, costs) gets an "at most" goal."""
    return "at_most" if info.good_direction == "down" else "at_least"


def goal_progress(analysis: Analysis, goals: list[GoalSpec], *, today: date) -> Result:
    """One row per goal (at most ``MAX_GOALS``). ``today`` is the person's today; the latest day of data is the
    newest day the metric's table covers, up to yesterday."""
    if not goals:
        raise AnalyticsError("no_goal")
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    tables: list[str] = []
    for goal in goals[:MAX_GOALS]:
        info = metric_info(analysis.dictionary, goal.metric)
        if not info.time_column:
            raise AnalyticsError("no_time_column", info.table)
        row, note = _one(analysis, info, goal, today)
        rows.append(row)
        if note and note not in notes:
            notes.append(note)
        if info.table not in tables:
            tables.append(info.table)
    facts: dict[str, Any] = {
        "goals": len(rows),
        "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in _counted(rows)},
    }
    if len(rows) == 1:
        facts.update({k: rows[0][k] for k in ("metric", "unit", "status", "target", "actual")})
    return Result(pd.DataFrame(rows), facts, notes, tables, list(analysis.sql_log))


def _counted(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(r["status"] for r in rows))


def _one(
    analysis: Analysis, info: MetricInfo, goal: GoalSpec, today: date
) -> tuple[dict[str, Any], str | None]:
    period = goal.period
    _, last = coverage(analysis, info.table)
    yesterday = today - timedelta(days=1)
    as_of = min(last, yesterday) if last is not None else None
    row: dict[str, Any] = {
        "goal_id": goal.goal_id,
        "name": goal.name,
        "metric": goal.metric,
        "unit": info.unit,
        "direction": goal.direction,
        "target": float(goal.target),
        "period_start": period.start.isoformat(),
        "period_end": period.end.isoformat(),
        "as_of": as_of.isoformat() if as_of else None,
        "total_days": period.days,
        "elapsed_days": 0,
        "elapsed_pct": 0.0,
        "actual": None,
        "actual_pct_of_target": None,
        "run_rate_projection": None,
        "seasonal_projection": None,
        "projection": None,
        "projected_pct_of_target": None,
        "gap": None,
        "needed_per_day": None,
        "method": None,
        "status": "no_data",
    }
    note = None
    if as_of is None:
        return row, f"no_data:{period.label()}"
    if as_of < period.start:
        row["status"] = "not_started"
        if last is not None and last < yesterday and period.start <= yesterday:
            note = f"data_ends:{last.isoformat()}:{period.label()}"
        return row, note
    if last is not None and last < yesterday and period.end > last:
        note = f"data_ends:{last.isoformat()}:{period.label()}"
    to_date = Period(period.start, min(as_of, period.end))
    actual = value_of(analysis, info, to_date, goal.filters)
    elapsed = to_date.days
    row.update(
        elapsed_days=elapsed,
        elapsed_pct=round(elapsed / period.days * 100.0, 1),
        actual=actual,
        actual_pct_of_target=_pct(actual, goal.target),
    )
    # Over by the calendar: no projection past the end, even when the data stopped early (a note says so).
    finished = yesterday >= period.end
    if finished:
        projection, method = actual, "final"
    elif info.additive:
        run_rate = actual * period.days / elapsed if actual is not None and elapsed else None
        seasonal = _seasonal(analysis, info, goal, to_date, actual)
        row.update(run_rate_projection=run_rate, seasonal_projection=seasonal)
        projection, method = (
            (seasonal, "seasonal") if seasonal is not None else (run_rate, "run_rate")
        )
    else:
        projection, method = actual, "current_value"
    remaining = period.days - elapsed
    if info.additive and not finished and actual is not None and remaining > 0:
        need = goal.target - actual
        row["needed_per_day"] = max(0.0, need) / remaining if goal.direction == "at_least" else None
    row.update(
        projection=projection,
        projected_pct_of_target=_pct(projection, goal.target),
        gap=projection - goal.target if projection is not None else None,
        method=method,
        status=status_of(
            goal.direction,
            goal.target,
            actual,
            projection,
            finished=finished,
            additive=info.additive,
        ),
    )
    return row, note


def _seasonal(
    analysis: Analysis, info: MetricInfo, goal: GoalSpec, to_date: Period, actual: float | None
) -> float | None:
    """Scale the value so far by last year's full period / last year's same days. ``None`` when last year does not
    cover the whole period or the numbers cannot carry it (nothing so far, a zero last year)."""
    if actual is None:
        return None
    first, _ = coverage(analysis, info.table)
    last_year = goal.period.previous_year()
    if first is None or first > last_year.start:
        return None
    so_far = value_of(
        analysis, info, Period(last_year.start, to_date.previous_year().end), goal.filters
    )
    whole = value_of(analysis, info, last_year, goal.filters)
    if not so_far or whole is None or so_far <= 0 or whole <= 0:
        return None
    return actual * whole / so_far


def status_of(
    direction: str,
    target: float,
    actual: float | None,
    projection: float | None,
    *,
    finished: bool,
    additive: bool,
) -> Status:
    if actual is None:
        return "no_data"

    def meets(value: float) -> bool:
        return value >= target if direction == "at_least" else value <= target

    if finished:
        return "achieved" if meets(actual) else "missed"
    if additive and direction == "at_least" and actual >= target:
        return "achieved"  # already there, before the period ends
    if additive and direction == "at_most" and actual > target:
        return "missed"  # the cap is already passed
    if projection is None:
        return "no_data"
    if meets(projection):
        return "on_track"
    margin = abs(target) * AT_RISK_MARGIN
    close = (
        projection >= target - margin if direction == "at_least" else projection <= target + margin
    )
    return "at_risk" if close else "behind"


def _pct(value: float | None, target: float) -> float | None:
    if value is None or not target:
        return None
    return round(value / target * 100.0, 1)
