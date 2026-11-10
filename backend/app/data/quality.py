"""Data-quality checks run on every fetch (BUILD_SPEC 7.7, R14).

Each finding has a stable ``kind``, ``details`` (numbers and names the web app turns into its own sentence from
``locales/en.ts``) and an English ``message`` (used by the agent and emails). Checks:

* ``incomplete_period``: a week (or month, for monthly data) with far fewer rows than the weeks around it.
* ``missing_segment``: one value of a small category column (e.g. ``sales_channel = Website``) almost vanishes
  for a week while it is normally there, e.g. a missing export.
* ``stale``: the newest row is older than the data's own rhythm suggests.
* ``nulls`` / ``empty_column``: columns that are often or always empty.
* ``duplicate_key``: an id-like column that is almost, but not quite, unique.
* ``duplicate_rows``: rows that are exact copies of another row.
* ``currency_mismatch``: a money column with more than one currency.
* ``unparsed_values``: values that did not fit the column's type and were left empty.
* ``total_row_dropped`` / ``truncated``: what the reader left out.

All checks are deterministic and bounded (a few category columns, a few findings each), so they are cheap on
large tables.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from app.connectors.tabular import ColumnInfo

Severity = Literal["warning", "info"]

MIN_ROWS = 50
LOW_PERIOD_RATIO = 0.6
MISSING_SEGMENT_RATIO = 0.25
NULL_RATE = 0.2
MAX_SEGMENT_COLUMNS = 4
MAX_SEGMENT_VALUES = 12
MAX_FINDINGS_PER_CHECK = 3
_NEIGHBOURS = 4
_ID_NAME = re.compile(r"(^id$|_id$|^.+id$|_no$|number$|_code$)")
_RUPEE = {"₹", "rs", "rs.", "inr"}


@dataclass(frozen=True)
class QualityIssue:
    kind: str
    severity: Severity
    message: str
    column: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _day(value: Any) -> str:
    stamp = pd.Timestamp(value)
    return f"{stamp.day} {stamp:%b %Y}"


def _rows(count: int) -> str:
    return "1 row" if count == 1 else f"{count:,} rows"


def _pct(part: float, whole: float) -> int:
    return round(100 * part / whole) if whole else 0


def columns_from_schema(schema: dict[str, Any]) -> list[ColumnInfo]:
    out = []
    for c in schema.get("columns", []):
        data: dict[str, Any] = {k: (tuple(v) if isinstance(v, list) else v) for k, v in c.items()}
        out.append(ColumnInfo(**data))
    return out


# --------------------------------------------------------------------------------------- time column
_TIME_PREFERRED = re.compile(r"(created|order|date|day|month|^at$|time)")


def time_column(frame: pd.DataFrame, columns: Sequence[ColumnInfo]) -> str | None:
    """The column that best says when each row happened (created/order date before updated)."""
    best: tuple[float, str] | None = None
    for c in columns:
        if c.type not in ("date", "datetime") or c.name not in frame:
            continue
        series = frame[c.name]
        if series.notna().mean() < 0.5:
            continue
        score = float(series.nunique())
        if _TIME_PREFERRED.search(c.name):
            score *= 10
        if "updated" in c.name or "end" in c.name:
            score /= 100
        if best is None or score > best[0]:
            best = (score, c.name)
    return best[1] if best else None


def _grain(times: pd.Series) -> Literal["W", "M"] | None:
    distinct = times.dt.normalize().drop_duplicates().sort_values()
    if len(distinct) < 6:
        return None
    gap = distinct.diff().dt.days.median()
    return "M" if gap >= 25 else "W"


def _period_counts(times: pd.Series, grain: str) -> pd.Series:
    freq = "W-MON" if grain == "W" else "MS"
    periods = times.dt.to_period("W-SUN" if grain == "W" else "M").dt.start_time
    counts = periods.value_counts().sort_index()
    full = pd.date_range(counts.index.min(), counts.index.max(), freq=freq)
    return counts.reindex(full, fill_value=0)


def _baselines(counts: pd.Series) -> pd.Series:
    values = counts.to_numpy(dtype=float)
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - _NEIGHBOURS), min(len(values), i + _NEIGHBOURS + 1)
        around = [values[j] for j in range(lo, hi) if j != i]
        out.append(
            float(pd.Series(around).median()) if len(around) >= _NEIGHBOURS else float("nan")
        )
    return pd.Series(out, index=counts.index)


def _low_periods(
    counts: pd.Series, ratio: float, minimum_baseline: float
) -> list[tuple[Any, int, float]]:
    """(period start, rows, usual rows) for inner periods below ``ratio`` of their neighbours.

    The shortfall must also be larger than chance: more than three standard deviations of a Poisson count with
    the usual mean, so small, noisy tables (a few rows a week) do not raise false alarms.
    """
    if len(counts) < 2 * _NEIGHBOURS:
        return []
    baseline = _baselines(counts).to_numpy()
    values = counts.to_numpy()
    found = []
    for i in range(1, len(counts) - 1):  # the first and last periods are usually partial
        start, count, usual = counts.index[i], int(values[i]), float(baseline[i])
        significant = usual - count > 3 * math.sqrt(usual)
        if usual >= minimum_baseline and count < ratio * usual and significant:
            found.append((start, int(count), float(usual)))
    return found


def _period_label(grain: str) -> str:
    return "week" if grain == "W" else "month"


def _completeness(frame: pd.DataFrame, column: str) -> list[QualityIssue]:
    times = frame[column].dropna()
    grain = _grain(times)
    if grain is None or len(times) < MIN_ROWS:
        return []
    counts = _period_counts(times, grain)
    issues = []
    for start, rows, usual in _low_periods(counts, LOW_PERIOD_RATIO, 10)[:MAX_FINDINGS_PER_CHECK]:
        period = _period_label(grain)
        share = _pct(rows, usual)
        label = f"the week of {_day(start)}" if grain == "W" else f"{start:%B %Y}"
        issues.append(
            QualityIssue(
                kind="incomplete_period",
                severity="warning",
                column=column,
                message=f"{label[0].upper()}{label[1:]} has {_rows(rows)}, about {share}% of a usual {period}. "
                "Numbers for that period may be too low.",
                details={
                    "grain": "week" if grain == "W" else "month",
                    "period_start": pd.Timestamp(start).date().isoformat(),
                    "rows": rows,
                    "usual_rows": round(usual),
                    "share_pct": share,
                },
            )
        )
    return issues


def _segment_columns(frame: pd.DataFrame, columns: Sequence[ColumnInfo]) -> list[str]:
    found = []
    for c in columns:
        if c.type != "text" or c.name not in frame:
            continue
        distinct = frame[c.name].nunique(dropna=True)
        if 2 <= distinct <= MAX_SEGMENT_VALUES:
            found.append(c.name)
        if len(found) == MAX_SEGMENT_COLUMNS:
            break
    return found


def _missing_segments(
    frame: pd.DataFrame, columns: Sequence[ColumnInfo], time_col: str
) -> list[QualityIssue]:
    times = frame[time_col]
    grain = _grain(times.dropna())
    if grain != "W" or len(frame) < MIN_ROWS:
        return []
    issues: list[QualityIssue] = []
    for column in _segment_columns(frame, columns):
        for value, group in frame.groupby(column, dropna=True, observed=True):
            group_times = group[time_col].dropna()
            if len(group_times) < MIN_ROWS:
                continue
            counts = _period_counts(group_times, grain)
            for start, rows, usual in _low_periods(counts, MISSING_SEGMENT_RATIO, 5):
                issues.append(
                    QualityIssue(
                        kind="missing_segment",
                        severity="warning",
                        column=column,
                        message=f"Rows with {column} = {value} look missing for the week of {_day(start)}: "
                        f"{_rows(rows)}, usually about {round(usual):,}. Totals for that week may be too low.",
                        details={
                            "value": str(value),
                            "period_start": pd.Timestamp(start).date().isoformat(),
                            "rows": rows,
                            "usual_rows": round(usual),
                        },
                    )
                )
                if len(issues) >= MAX_FINDINGS_PER_CHECK:
                    return issues
    return issues


def _stale(frame: pd.DataFrame, column: str, now_local: datetime) -> list[QualityIssue]:
    times = frame[column].dropna()
    if len(times) < 2:
        return []
    newest = times.max()
    distinct = times.dt.normalize().drop_duplicates().sort_values()
    gap_days = float(distinct.diff().dt.days.median()) if len(distinct) > 1 else 1.0
    allowed = max(7.0, 3 * gap_days)
    age_days = (pd.Timestamp(now_local) - newest).total_seconds() / 86400
    if age_days <= allowed:
        return []
    days = int(age_days)
    return [
        QualityIssue(
            kind="stale",
            severity="warning",
            column=column,
            message=f"The newest row is from {_day(newest)}, {days} days ago. Recent activity may be missing.",
            details={"newest": pd.Timestamp(newest).date().isoformat(), "age_days": days},
        )
    ]


def _nulls(frame: pd.DataFrame, columns: Sequence[ColumnInfo]) -> list[QualityIssue]:
    issues = []
    total = len(frame)
    if total == 0:
        return []
    for c in columns:
        if c.name not in frame:
            continue
        empty = int(frame[c.name].isna().sum())
        if empty == total:
            issues.append(
                QualityIssue(
                    kind="empty_column",
                    severity="info",
                    column=c.name,
                    message=f"The column {c.label} is empty.",
                    details={"label": c.label},
                )
            )
        elif empty / total >= NULL_RATE:
            share = _pct(empty, total)
            issues.append(
                QualityIssue(
                    kind="nulls",
                    severity="info",
                    column=c.name,
                    message=f"{share}% of {c.label} is empty.",
                    details={"label": c.label, "empty": empty, "share_pct": share},
                )
            )
    return issues[: MAX_FINDINGS_PER_CHECK * 2]


def _duplicates(frame: pd.DataFrame, columns: Sequence[ColumnInfo]) -> list[QualityIssue]:
    issues = []
    total = len(frame)
    if total < 2:
        return []
    for c in columns:
        if c.name not in frame or not _ID_NAME.search(c.name) or c.type in ("date", "datetime"):
            continue
        series = frame[c.name].dropna()
        if series.empty:
            continue
        distinct = series.nunique()
        repeated = len(series) - distinct
        if repeated and distinct / len(series) >= 0.9:
            issues.append(
                QualityIssue(
                    kind="duplicate_key",
                    severity="warning",
                    column=c.name,
                    message=f"{repeated:,} values in {c.label} appear more than once, although it looks like an id.",
                    details={"label": c.label, "repeated": int(repeated)},
                )
            )
            break
    copies = int(frame.duplicated().sum())
    if copies:
        issues.append(
            QualityIssue(
                kind="duplicate_rows",
                severity="warning",
                message=f"{copies:,} rows are exact copies of another row.",
                details={"rows": copies},
            )
        )
    return issues


def _column_findings(columns: Sequence[ColumnInfo]) -> list[QualityIssue]:
    issues = []
    for c in columns:
        currencies = set(c.currencies)
        if currencies and not currencies <= _RUPEE and currencies & _RUPEE:
            issues.append(
                QualityIssue(
                    kind="currency_mismatch",
                    severity="warning",
                    column=c.name,
                    message=f"{c.label} mixes currencies ({', '.join(sorted(currencies))}). Totals may add "
                    "different currencies together.",
                    details={"label": c.label, "currencies": sorted(currencies)},
                )
            )
        if c.unparsed_count:
            kind = {
                "integer": "numbers",
                "number": "numbers",
                "date": "dates",
                "datetime": "dates",
            }.get(c.type, "values")
            examples = ", ".join(f"'{e}'" for e in c.unparsed_examples)
            issues.append(
                QualityIssue(
                    kind="unparsed_values",
                    severity="warning",
                    column=c.name,
                    message=f"{c.unparsed_count:,} values in {c.label} are not {kind}"
                    + (f" (for example {examples})" if examples else "")
                    + " and were left empty.",
                    details={
                        "label": c.label,
                        "count": c.unparsed_count,
                        "expected": kind,
                        "examples": list(c.unparsed_examples),
                    },
                )
            )
    return issues


def check(
    frame: pd.DataFrame,
    columns: Sequence[ColumnInfo],
    *,
    now: datetime,
    timezone: str,
    truncated: bool = False,
    total_row_dropped: bool = False,
    max_rows: int | None = None,
) -> list[QualityIssue]:
    """Every finding for one table, most important first."""
    issues: list[QualityIssue] = []
    if truncated:
        issues.append(
            QualityIssue(
                kind="truncated",
                severity="warning",
                message=f"Only the first {max_rows or len(frame):,} rows were read. Later rows are not included.",
                details={"rows": max_rows or len(frame)},
            )
        )
    column = time_column(frame, columns)
    if column is not None:
        now_local = now.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
        issues += _stale(frame, column, now_local)
        issues += _completeness(frame, column)
        issues += _missing_segments(frame, columns, column)
    issues += _column_findings(columns)
    issues += _duplicates(frame, columns)
    issues += _nulls(frame, columns)
    if total_row_dropped:
        issues.append(
            QualityIssue(
                kind="total_row_dropped",
                severity="info",
                message="The 'Total' row at the end was left out, so totals are not counted twice.",
            )
        )
    return issues
