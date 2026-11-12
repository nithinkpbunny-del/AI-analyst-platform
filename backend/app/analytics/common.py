"""Shared pieces of the analytics library (BUILD_SPEC 10): metrics from the dictionary, periods, filters and
dimensions (including a dimension of a joined table), all turned into SQL over the DuckDB workspace.

Every function of the library takes an ``Analysis`` (the workspace, the dictionary the asking person may see, and
a way to load a table on demand), works on exact numbers in code (P4) and returns a ``Result``: a table, the
headline facts and notes about what limits the result. Nothing here calls an LLM.

SQL is built from dictionary names only (each checked to exist, then quoted) and values are bound as
parameters; the workspace still checks every statement (``sql_guard``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

import pandas as pd

from app.data.workspace import Workspace
from app.dictionary import validate
from app.dictionary.model import Dictionary, Metric
from app.security.sql_guard import safe_identifier as q

Grain = Literal["day", "week", "month", "quarter", "year"]
GRAINS: tuple[Grain, ...] = ("day", "week", "month", "quarter", "year")
OTHER = "Other"


class AnalyticsError(Exception):
    """The analysis cannot be done as asked. ``code`` is stable (the agent and the UI explain it)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class Result:
    table: pd.DataFrame
    facts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    workspace: Workspace
    dictionary: Dictionary
    # Loads a dictionary table into the workspace (the agent fetches it live); tests pre-load and pass a no-op.
    ensure: Callable[[str], None] = lambda _table: None
    sql_log: list[str] = field(default_factory=list)

    def table(self, key: str) -> None:
        if key not in self.dictionary.datasets:
            raise AnalyticsError("unknown_table", key)
        if not self.workspace.has(key):
            self.ensure(key)
        if not self.workspace.has(key):
            raise AnalyticsError("table_unavailable", key)

    def frame(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        self.sql_log.append(sql)
        return self.workspace.frame(sql, params)


# -------------------------------------------------------------------------------------------- periods
@dataclass(frozen=True)
class Period:
    start: date  # inclusive
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise AnalyticsError("bad_period", f"{self.start} is after {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def end_exclusive(self) -> date:
        return self.end + timedelta(days=1)

    def label(self) -> str:
        return f"{self.start.isoformat()} to {self.end.isoformat()}"

    def previous(self) -> Period:
        """The period of the same length just before this one (a calendar month/quarter maps to the one before)."""
        if self.start.day == 1 and self.end_exclusive.day == 1:
            months = _month_span(self.start, self.end_exclusive)
            if months:
                start = _add_months(self.start, -months)
                return Period(start, self.start - timedelta(days=1))
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def previous_year(self) -> Period:
        return Period(_minus_year(self.start), _minus_year(self.end))

    def as_json(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def _month_span(start: date, end_exclusive: date) -> int:
    return (end_exclusive.year - start.year) * 12 + end_exclusive.month - start.month


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _minus_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def parse_date(value: Any, name: str = "date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise AnalyticsError("bad_date", f"{name}={value!r}") from error


def period_of(value: Any, name: str = "period") -> Period:
    if isinstance(value, Period):
        return value
    if not isinstance(value, dict) or "start" not in value or "end" not in value:
        raise AnalyticsError("bad_period", f"{name} needs start and end dates")
    return Period(parse_date(value["start"], "start"), parse_date(value["end"], "end"))


def comparison_of(current: Period, compare: Any) -> Period | None:
    """``previous_period`` (default), ``previous_year``, ``none`` or an explicit ``{start, end}``."""
    if compare in (None, "", "previous_period"):
        return current.previous()
    if compare == "previous_year":
        return current.previous_year()
    if compare == "none":
        return None
    return period_of(compare, "compare")


# -------------------------------------------------------------------------------------------- metrics
@dataclass(frozen=True)
class MetricInfo:
    key: str
    sql: str
    table: str
    time_column: str | None
    unit: str | None
    good_direction: str | None
    meaning: str
    additive: bool  # the metric of a whole is the sum of its parts (SUM/COUNT only)


_ADDITIVE = re.compile(r"^\s*(\(?\s*(SUM|COUNT)\s*\([^()]*\)\s*\)?\s*[-+]?\s*)+$", re.IGNORECASE)


def metric_info(dictionary: Dictionary, key: str) -> MetricInfo:
    metric: Metric | None = dictionary.metrics.get(key)
    if metric is None:
        raise AnalyticsError("unknown_metric", key)
    if metric.valid is False:
        raise AnalyticsError("metric_invalid", key)
    try:
        sql, table = validate.expand(key, dictionary)
    except validate.MetricError as error:
        raise AnalyticsError(
            "metric_unavailable" if error.code == "no_table" else "metric_invalid",
            f"{key}: {error.code}",
        ) from error
    if table is None or table not in dictionary.datasets:
        raise AnalyticsError("metric_unavailable", key)
    entry = dictionary.datasets[table]
    return MetricInfo(
        key=key,
        sql=sql,
        table=table,
        time_column=entry.time_column,
        unit=metric.unit,
        good_direction=metric.good_direction,
        meaning=metric.meaning,
        additive=bool(_ADDITIVE.match(sql)),
    )


# ---------------------------------------------------------------------------------------- dimensions
@dataclass(frozen=True)
class Join:
    alias: str
    table: str
    left_column: str  # in the metric's table
    right_column: str  # in the joined table


@dataclass
class Frame:
    """``FROM`` for one metric table plus the joined tables its dimensions and filters need.

    ``base_sql()`` is a subquery with every column of the metric's table under its own name and each joined
    column as ``__j<n>``, so a metric expression written over its table works unchanged."""

    analysis: Analysis
    table: str
    joins: dict[str, Join] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)  # "customers.region" -> "__j0"

    def column(self, spec: str) -> str:
        """The name of ``spec`` (a column of the table, or ``table.column`` of a joined one) in ``base_sql``."""
        dictionary = self.analysis.dictionary
        entry = dictionary.datasets[self.table]
        name = spec.strip()
        if "." not in name:
            if name not in entry.columns:
                raise AnalyticsError("unknown_column", f"{self.table}.{name}")
            return name
        other, _, column = name.partition(".")
        if other == self.table:
            return self.column(column)
        if other not in dictionary.datasets or column not in dictionary.datasets[other].columns:
            raise AnalyticsError("unknown_column", name)
        if name in self.extra:
            return self.extra[name]
        join = self._join_for(other)
        alias = f"__j{len(self.extra)}"
        self.extra[name] = alias
        self.joins.setdefault(join.alias, join)
        return alias

    def _join_for(self, other: str) -> Join:
        for join in self.joins.values():
            if join.table == other:
                return join
        for relationship in self.analysis.dictionary.relationships:
            left_table, _, left_column = relationship.left.partition(".")
            right_table, _, right_column = relationship.right.partition(".")
            if left_table == self.table and right_table == other:
                found = Join(f"__t{len(self.joins)}", other, left_column, right_column)
                break
            if right_table == self.table and left_table == other:
                found = Join(f"__t{len(self.joins)}", other, right_column, left_column)
                break
        else:
            raise AnalyticsError("no_join", f"{self.table} -> {other}")
        self.analysis.table(other)
        return found

    def base_sql(self) -> str:
        # Names come from the dictionary, each checked and quoted (``q``); no value is ever put into SQL.
        parts = [f"SELECT __m.*{''.join(self._extra_columns())} FROM {q(self.table)} AS __m"]  # noqa: S608
        for join in self.joins.values():
            # One row per key on the joined side, so a join never multiplies the metric's rows.
            right = (
                f"(SELECT * FROM {q(join.table)} QUALIFY row_number() OVER "  # noqa: S608
                f"(PARTITION BY {q(join.right_column)}) = 1)"
            )
            parts.append(
                f"LEFT JOIN {right} AS {join.alias} "
                f"ON __m.{q(join.left_column)} = {join.alias}.{q(join.right_column)}"
            )
        return " ".join(parts)

    def _extra_columns(self) -> list[str]:
        out = []
        for spec, alias in self.extra.items():
            table, _, column = spec.partition(".")
            join = next(j for j in self.joins.values() if j.table == table)
            out.append(f", {join.alias}.{q(column)} AS {alias}")
        return out


def frame_for(analysis: Analysis, table: str) -> Frame:
    analysis.table(table)
    return Frame(analysis, table)


# -------------------------------------------------------------------------------------------- filters
Filters = dict[str, Any]


def where(
    frame: Frame,
    filters: Filters | None,
    *,
    time_column: str | None = None,
    period: Period | None = None,
) -> tuple[str, list[Any]]:
    """A ``WHERE`` clause over ``frame.base_sql()`` columns and its parameters.

    Filter values: a value, a list of values, or ``{"from": a, "to": b}`` (inclusive)."""
    clauses: list[str] = []
    params: list[Any] = []
    if period is not None:
        if not time_column:
            raise AnalyticsError("no_time_column", frame.table)
        clauses.append(f"{q(time_column)} >= ? AND {q(time_column)} < ?")
        params += [period.start, period.end_exclusive]
    for spec, value in (filters or {}).items():
        column = q(frame.column(spec))
        if isinstance(value, dict):
            if "from" in value:
                clauses.append(f"{column} >= ?")
                params.append(value["from"])
            if "to" in value:
                clauses.append(f"{column} <= ?")
                params.append(value["to"])
        elif isinstance(value, list):
            if not value:
                clauses.append("FALSE")
                continue
            clauses.append(f"CAST({column} AS VARCHAR) IN ({', '.join('?' for _ in value)})")
            params += [_text(v) for v in value]
        elif value is None:
            clauses.append(f"{column} IS NULL")
        else:
            clauses.append(f"CAST({column} AS VARCHAR) = ?")
            params.append(_text(value))
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _text(value: Any) -> str:
    """A filter value as DuckDB writes the column as text (booleans are ``true``/``false``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def bucket_sql(time_column: str, grain: Grain) -> str:
    if grain not in GRAINS:
        raise AnalyticsError("bad_grain", str(grain))
    return f"CAST(date_trunc('{grain}', {q(time_column)}) AS DATE)"


# ---------------------------------------------------------------------------------------------- values
def value_of(
    analysis: Analysis,
    metric: MetricInfo,
    period: Period | None,
    filters: Filters | None = None,
) -> float | None:
    frame = frame_for(analysis, metric.table)
    clause, params = where(frame, filters, time_column=metric.time_column, period=period)
    sql = f"SELECT ({metric.sql}) AS value FROM ({frame.base_sql()}) AS b {clause}"  # noqa: S608
    result = analysis.frame(sql, params)
    return number(result["value"].iloc[0]) if len(result) else None


def number(value: Any) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def coverage(analysis: Analysis, table: str) -> tuple[date | None, date | None]:
    """First and last day with data in the table's time column."""
    entry = analysis.dictionary.datasets[table]
    if not entry.time_column:
        return None, None
    analysis.table(table)
    column = q(entry.time_column)
    found = analysis.frame(
        f"SELECT CAST(min({column}) AS DATE) AS lo, CAST(max({column}) AS DATE) AS hi FROM {q(table)}"  # noqa: S608
    )
    lo, hi = found["lo"].iloc[0], found["hi"].iloc[0]
    return (
        None if pd.isna(lo) else pd.Timestamp(lo).date(),
        None if pd.isna(hi) else pd.Timestamp(hi).date(),
    )


def coverage_notes(analysis: Analysis, table: str, *periods: Period | None) -> list[str]:
    """Plain notes when a period reaches past the data (a common reason for a "drop")."""
    lo, hi = coverage(analysis, table)
    notes = []
    for period in periods:
        if period is None or lo is None or hi is None:
            continue
        if period.end > hi and period.start <= hi:
            notes.append(f"data_ends:{hi.isoformat()}:{period.label()}")
        elif period.start > hi or period.end < lo:
            notes.append(f"no_data:{period.label()}")
        elif period.start < lo:
            notes.append(f"data_starts:{lo.isoformat()}:{period.label()}")
    return notes


def customer_column(analysis: Analysis, table: str) -> str | None:
    """The column that says which customer a row belongs to: named like a customer key, or joined to a table of
    customers in the dictionary."""
    entry = analysis.dictionary.datasets[table]
    for name, column in entry.columns.items():
        lowered = name.lower()
        if "customer" in lowered and (lowered.endswith("id") or column.role == "key"):
            return name
    for relationship in analysis.dictionary.relationships:
        left_table, _, left_column = relationship.left.partition(".")
        right_table = relationship.right.partition(".")[0]
        if left_table == table and "customer" in right_table.lower():
            return left_column
    return None
