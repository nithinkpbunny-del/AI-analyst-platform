"""The agent's tools (BUILD_SPEC 7.3; M8 without what-if, goals and growth signals).

Every tool receives the ``ToolContext`` of the question: the asking person's ``AccessContext``, the dictionary
cut down to what they may see, the per-question DuckDB workspace and the live data cache. A tool can only reach
tables of that dictionary; tables are fetched live (cache-aware, R1) the first time a tool needs them.

Results go back to the model as compact JSON with a ``result_id`` and at most ``rows_to_llm_per_tool_call`` rows
(aggregates are preferred); the same content is kept for the answer's provenance and the "Show my work" panel.
Every number is computed here, in code (P4).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from app.agent.access import AccessContext, VisibleDictionary
from app.agent.provenance import ResultStore
from app.analytics import anomalies, cohorts, drivers, finance, forecast, kpis, marketing, rfm
from app.analytics.common import (
    GRAINS,
    Analysis,
    AnalyticsError,
    Period,
    Result,
    comparison_of,
    coverage,
    parse_date,
    period_of,
)
from app.connectors.base import ConnectorError
from app.data.cache import DatasetCache, DatasetMissing
from app.data.workspace import QueryError, Workspace, plain
from app.dictionary.units import to_units
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.security import sql_guard

logger = logging.getLogger(__name__)

MAX_CHART_POINTS = 400
SAMPLE_ROWS = 3


class ToolFailure(Exception):
    def __init__(self, code: str, detail: str = "", hint: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.hint = hint


@dataclass
class ToolContext:
    access: AccessContext
    visible: VisibleDictionary
    workspace: Workspace
    cache: DatasetCache | None  # None in tests that pre-load the workspace
    store: ResultStore
    today: date
    max_rows: int = 200
    progress: Callable[[dict[str, Any]], None] = lambda _step: None
    loaded: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # table key -> provenance of its copy
    quality: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    charts: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    frames: dict[str, pd.DataFrame] = field(
        default_factory=dict
    )  # full tables of results, for charts

    @property
    def dictionary(self) -> Any:
        return self.visible.dictionary

    # ------------------------------------------------------------------------------------- tables
    def ensure(self, key: str) -> None:
        """Load a visible table into the workspace, live through the cache (R1)."""
        if self.workspace.has(key):
            return
        ref = self.visible.tables.get(key)
        entry = self.dictionary.datasets.get(key)
        if ref is None or entry is None:
            raise AnalyticsError("unknown_table", key)
        if self.cache is None:
            raise AnalyticsError("table_unavailable", key)
        self.progress({"code": "fetching", "table": entry.name, "connection": ref.connection_name})
        try:
            cached = self.cache.get(ref.dataset_id)
        except ConnectorError as error:
            raise ToolFailure("source_error", error.message) from error
        except DatasetMissing as error:
            raise AnalyticsError("table_unavailable", key) from error
        frame = to_units(cached.frame, entry)
        self.workspace.load(key, frame, {"dataset_id": str(ref.dataset_id)})
        self.loaded[key] = {
            "table": key,
            "name": entry.name,
            "connection": ref.connection_name,
            "connection_type": ref.connection_type,
            "connection_id": str(ref.connection_id),
            "dataset_id": str(ref.dataset_id),
            "fetched_at": cached.fetched_at.isoformat(),
            "checked_at": cached.checked_at.isoformat(),
            "rows": cached.row_count,
        }
        warnings = [q for q in cached.quality if q.get("severity") == "warning"]
        if warnings:
            self.quality[key] = warnings

    def analysis(self) -> Analysis:
        return Analysis(self.workspace, self.dictionary, self.ensure)

    def table_record(self, key: str) -> dict[str, Any]:
        if key in self.loaded:
            return self.loaded[key]
        ref = self.visible.tables.get(key)
        return {
            "table": key,
            "name": ref.name if ref else key,
            "connection": ref.connection_name if ref else None,
            "fetched_at": None,
        }


# ------------------------------------------------------------------------------------------ helpers
def compact(value: Any) -> Any:
    """Numbers rounded for the model (2 decimals, 4 significant digits below 1), dates as text."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if abs(value) >= 1 or value == 0:
            return round(value, 2)
        return float(f"{value:.4g}")
    if isinstance(value, int | str):
        return value
    if isinstance(value, dict):
        return {str(k): compact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [compact(v) for v in value]
    return compact(plain(value))


def table_json(frame: pd.DataFrame, limit: int) -> dict[str, Any]:
    columns = [str(c) for c in frame.columns]
    rows = [[compact(plain(v)) for v in row] for row in frame.head(limit).itertuples(index=False)]
    return {
        "columns": columns,
        "rows": rows,
        "total_rows": len(frame),
        "truncated": len(frame) > limit,
    }


def _obj(args: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = args.get(name)
    return value if isinstance(value, dict) else None


def _str(args: dict[str, Any], name: str, default: str | None = None) -> str | None:
    value = args.get(name, default)
    return str(value) if value not in (None, "") else default


def _list(args: dict[str, Any], name: str) -> list[str]:
    value = args.get(name)
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if isinstance(value, list) else []


def _period(args: dict[str, Any], name: str = "period") -> Period:
    return period_of(args.get(name), name)


def _optional_period(args: dict[str, Any], name: str = "period") -> Period | None:
    return period_of(args[name], name) if isinstance(args.get(name), dict) else None


def _grain(args: dict[str, Any], default: str) -> Any:
    grain = _str(args, "grain", default)
    if grain not in GRAINS:
        raise AnalyticsError("bad_grain", str(grain))
    return grain


def _result(ctx: ToolContext, tool: str, args: dict[str, Any], result: Result) -> dict[str, Any]:
    tables = [ctx.table_record(t) for t in dict.fromkeys(result.tables_used)]
    content = {
        "facts": compact(result.facts),
        **table_json(result.table, ctx.max_rows),
        "notes": result.notes,
    }
    rid = ctx.store.add(
        tool, content, tables=tables, inputs=args, sql=result.sql, row_count=len(result.table)
    )
    ctx.frames[rid] = result.table
    ctx.notes += [n for n in result.notes if n not in ctx.notes]
    quality = [{"table": t, **q} for t in result.tables_used for q in ctx.quality.get(t, [])]
    return {"result_id": rid, **content, **({"quality_warnings": quality} if quality else {})}


# ------------------------------------------------------------------------------------------- schemas
_PERIOD = {
    "type": "object",
    "description": "Dates are inclusive, ISO format.",
    "properties": {
        "start": {"type": "string", "format": "date"},
        "end": {"type": "string", "format": "date"},
    },
    "required": ["start", "end"],
}
_COMPARE = {
    "description": "'previous_period' (default: the same length just before), 'previous_year', 'none', "
    "or an explicit {start, end}.",
    "anyOf": [{"type": "string", "enum": ["previous_period", "previous_year", "none"]}, _PERIOD],
}
_FILTERS = {
    "type": "object",
    "description": "Column -> value, list of values, or {from, to}. A column of a joined table is "
    "'table.column' (e.g. 'customers.region').",
    "additionalProperties": True,
}
_METRIC = {"type": "string", "description": "A metric name from the dictionary."}
_GRAIN = {"type": "string", "enum": list(GRAINS)}


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> ToolSpec:
    return ToolSpec(
        name, description, {"type": "object", "properties": properties, "required": required}
    )


SPECS: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        _spec(
            "list_sources",
            "The connected sources and tables you may use, how fresh they are, the dictionary's metrics "
            "and joins. Start here when you are unsure where data lives.",
            {},
            [],
        ),
        _spec(
            "describe_dataset",
            "A table's columns (type, role, unit, meaning), basic statistics, a few sample rows (personal "
            "columns hidden) and its data-quality warnings.",
            {"table": {"type": "string"}},
            ["table"],
        ),
        _spec(
            "fetch_dataset",
            "Load a table with its latest data into the analysis workspace and report its row count, date "
            "coverage and quality warnings. Other tools load tables themselves; use this to check coverage.",
            {"table": {"type": "string"}, "period": _PERIOD},
            ["table"],
        ),
        _spec(
            "run_sql",
            "A read-only DuckDB SELECT over workspace tables (named by their dictionary table key); joins "
            "across sources are allowed. Returns at most 200 rows: aggregate in SQL.",
            {"sql": {"type": "string"}, "purpose": {"type": "string"}},
            ["sql"],
        ),
        _spec(
            "kpi_summary",
            "Metric values for a period compared with another period: current, previous, change and change %.",
            {
                "metrics": {"type": "array", "items": _METRIC},
                "period": _PERIOD,
                "compare": _COMPARE,
                "filters": _FILTERS,
            },
            ["metrics", "period"],
        ),
        _spec(
            "trend",
            "A metric over time by day, week, month, quarter or year, optionally one series per value of a "
            "dimension.",
            {
                "metric": _METRIC,
                "period": _PERIOD,
                "grain": _GRAIN,
                "split_by": {"type": "string"},
                "filters": _FILTERS,
            },
            ["metric", "period", "grain"],
        ),
        _spec(
            "driver_analysis",
            "Why a metric changed between two periods: contribution of each value of up to 3 dimensions, "
            "volume vs rate effects, and new vs repeat customers.",
            {
                "metric": _METRIC,
                "period": _PERIOD,
                "compare": _COMPARE,
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "filters": _FILTERS,
            },
            ["metric", "period", "dimensions"],
        ),
        _spec(
            "detect_anomalies",
            "Unusual days (or weeks, months) of a metric against its seasonal baseline (same weekday over "
            "the previous 8 weeks), for the total and the top values of a dimension.",
            {
                "metric": _METRIC,
                "period": _PERIOD,
                "dimension": {"type": "string"},
                "grain": {"type": "string", "enum": ["day", "week", "month"]},
                "filters": _FILTERS,
            },
            ["metric", "period"],
        ),
        _spec(
            "forecast",
            "Forecast a metric with an 80% range, the method used and its backtest error.",
            {
                "metric": _METRIC,
                "grain": {"type": "string", "enum": ["day", "week", "month", "quarter"]},
                "horizon": {"type": "integer", "minimum": 1, "maximum": 12},
                "filters": _FILTERS,
            },
            ["metric"],
        ),
        _spec(
            "segment_customers",
            "Customer segments by recency, frequency and value (kind 'rfm') or monthly cohort retention "
            "(kind 'cohorts').",
            {
                "metric": _METRIC,
                "kind": {"type": "string", "enum": ["rfm", "cohorts"]},
                "period": _PERIOD,
            },
            ["metric", "kind"],
        ),
        _spec(
            "marketing_efficiency",
            "Spend, CTR, conversion rate, CPA and ROAS by channel or campaign, from campaign tables.",
            {
                "by": {"type": "string", "enum": ["channel", "campaign"]},
                "period": _PERIOD,
                "tables": {"type": "array", "items": {"type": "string"}},
            },
            [],
        ),
        _spec(
            "finance_summary",
            "Budget vs actual by department/line (kind 'budget_variance'), receivables ageing "
            "('receivables'), or P&L ('pnl', needs accounting data).",
            {
                "kind": {"type": "string", "enum": ["budget_variance", "receivables", "pnl"]},
                "period": _PERIOD,
                "by_month": {"type": "boolean"},
            },
            ["kind"],
        ),
        _spec(
            "make_chart",
            "A chart from an earlier result's rows. The title must state the insight ('South fell 18% while "
            "other regions grew'), not just name the data.",
            {
                "result_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["line", "bar", "horizontal_bar", "stacked_bar"],
                },
                "x": {"type": "string", "description": "Column for the x axis (or categories)."},
                "y": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Value columns.",
                },
                "series": {
                    "type": "string",
                    "description": "Column whose values become separate series.",
                },
                "title": {"type": "string"},
            },
            ["result_id", "type", "x", "y", "title"],
        ),
    ]
}


# ------------------------------------------------------------------------------------------ handlers
def _time_range(entry: Any, ref: Any) -> list[Any] | None:
    profile = ref.profile if ref else {}
    for column in profile.get("columns", []) if isinstance(profile, dict) else []:
        if column.get("name") == entry.time_column:
            return [column.get("min"), column.get("max")]
    return None


def list_sources(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ctx.progress({"code": "checking_sources"})
    d = ctx.dictionary
    tables = []
    for key, entry in d.datasets.items():
        ref = ctx.visible.tables[key]
        tables.append(
            {
                "table": key,
                "name": entry.name,
                "source": ref.connection_name,
                "source_type": ref.connection_type,
                "meaning": entry.meaning,
                "grain": entry.grain or None,
                "time_column": entry.time_column,
                "date_range": _time_range(entry, ref),
                "rows": ref.row_count,
                "columns": len(entry.columns),
                "fetched_at": ctx.loaded.get(key, {}).get("fetched_at"),
            }
        )
    metrics = [
        {
            "metric": k,
            "table": m.dataset,
            "unit": m.unit,
            "good_direction": m.good_direction,
            "meaning": m.meaning or None,
        }
        for k, m in d.metrics.items()
    ]
    content = {
        "today": ctx.today.isoformat(),
        "tables": tables,
        "metrics": metrics,
        "joins": [{"left": r.left, "right": r.right} for r in d.relationships],
        "unavailable_metrics": ctx.visible.unavailable_metrics,
    }
    rid = ctx.store.add(
        "list_sources", content, tables=[], inputs={}, sql=[], row_count=len(tables)
    )
    return {"result_id": rid, **compact(content)}


def describe_dataset(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key = _str(args, "table") or ""
    entry = ctx.dictionary.datasets.get(key)
    if entry is None:
        raise AnalyticsError("unknown_table", key)
    ctx.progress({"code": "describing", "table": entry.name})
    ref = ctx.visible.tables[key]
    listed = ref.profile.get("columns") if isinstance(ref.profile, dict) else None
    profile: dict[str, dict[str, Any]] = {
        str(c.get("name")): c
        for c in (listed if isinstance(listed, list) else [])
        if isinstance(c, dict)
    }
    personal = {name for name, c in profile.items() if c.get("personal")}
    columns = []
    for name, column in entry.columns.items():
        stats = profile.get(name, {})
        columns.append(
            {
                "column": name,
                "label": column.label,
                "type": column.type,
                "role": column.role,
                "unit": column.unit,
                "meaning": column.meaning,
                "synonyms": column.synonyms or None,
                "empty_pct": stats.get("null_pct"),
                "distinct": stats.get("distinct"),
                "min": stats.get("min"),
                "max": stats.get("max"),
                "examples": None if name in personal else stats.get("samples"),
            }
        )
    ctx.ensure(key)
    sample = ctx.workspace.frame_of(key).head(SAMPLE_ROWS).copy()
    for name in personal & set(sample.columns):
        sample[name] = "(hidden)"
    content = {
        "table": key,
        "name": entry.name,
        "meaning": entry.meaning,
        "time_column": entry.time_column,
        "columns": columns,
        "sample_rows": table_json(sample, SAMPLE_ROWS),
        "quality_warnings": ctx.quality.get(key, []),
    }
    rid = ctx.store.add(
        "describe_dataset",
        content,
        tables=[ctx.table_record(key)],
        inputs={"table": key},
        sql=[],
        row_count=len(columns),
    )
    return {"result_id": rid, **compact(content)}


def fetch_dataset(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    key = _str(args, "table") or ""
    entry = ctx.dictionary.datasets.get(key)
    if entry is None:
        raise AnalyticsError("unknown_table", key)
    ctx.ensure(key)
    analysis = ctx.analysis()
    lo, hi = coverage(analysis, key)
    period = _optional_period(args)
    rows = ctx.workspace.tables[key].rows
    in_period = rows
    if period is not None and entry.time_column:
        column = sql_guard.safe_identifier(entry.time_column)
        found = ctx.workspace.frame(
            f"SELECT count(*) AS n FROM {sql_guard.safe_identifier(key)} WHERE {column} >= ? AND {column} < ?",  # noqa: S608
            [period.start, period.end_exclusive],
        )
        in_period = int(found["n"].iloc[0])
    content = {
        "table": key,
        "rows": rows,
        "rows_in_period": in_period if period else None,
        "first_day": lo.isoformat() if lo else None,
        "last_day": hi.isoformat() if hi else None,
        "fetched_at": ctx.loaded.get(key, {}).get("fetched_at"),
        "quality_warnings": ctx.quality.get(key, []),
    }
    rid = ctx.store.add(
        "fetch_dataset",
        content,
        tables=[ctx.table_record(key)],
        inputs=args,
        sql=[],
        row_count=rows,
    )
    return {"result_id": rid, **compact(content)}


def run_sql(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    sql = _str(args, "sql") or ""
    try:
        checked = sql_guard.check_select(
            sql, dialect="duckdb", allowed_tables=ctx.dictionary.datasets
        )
    except sql_guard.UnsafeSql as error:
        raise ToolFailure(
            "unsafe_sql", str(error), "Write one SELECT over the dictionary's table keys."
        ) from error
    ctx.progress({"code": "querying", "purpose": (_str(args, "purpose") or "")[:120]})
    for table in sorted(checked.tables):
        ctx.ensure(table)
    result = ctx.workspace.query(checked.sql, max_rows=ctx.max_rows)
    frame = pd.DataFrame(result.rows, columns=result.columns)
    content = {
        "columns": result.columns,
        "rows": compact(result.rows),
        "total_rows": result.total_rows,
        "truncated": result.truncated,
    }
    tables = [ctx.table_record(t) for t in sorted(checked.tables)]
    rid = ctx.store.add(
        "run_sql",
        content,
        tables=tables,
        inputs={"sql": checked.sql},
        sql=[checked.sql],
        row_count=result.total_rows,
    )
    ctx.frames[rid] = frame
    quality = [{"table": t, **q} for t in checked.tables for q in ctx.quality.get(t, [])]
    return {"result_id": rid, **content, **({"quality_warnings": quality} if quality else {})}


def _computing(ctx: ToolContext, tool: str, metric: str | None) -> None:
    ctx.progress({"code": "computing", "tool": tool, "metric": metric})


def kpi_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metrics = _list(args, "metrics")
    _computing(ctx, "kpi_summary", metrics[0] if metrics else None)
    period = _period(args)
    result = kpis.kpi_summary(
        ctx.analysis(),
        metrics,
        period,
        comparison_of(period, args.get("compare")),
        _obj(args, "filters"),
    )
    return _result(ctx, "kpi_summary", args, result)


def trend(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metric = _str(args, "metric") or ""
    _computing(ctx, "trend", metric)
    result = kpis.trend(
        ctx.analysis(),
        metric,
        _period(args),
        _grain(args, "month"),
        _str(args, "split_by"),
        _obj(args, "filters"),
    )
    return _result(ctx, "trend", args, result)


def driver_analysis(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metric = _str(args, "metric") or ""
    _computing(ctx, "driver_analysis", metric)
    period = _period(args)
    compare = comparison_of(period, args.get("compare")) or period.previous()
    result = drivers.driver_analysis(
        ctx.analysis(), metric, period, compare, _list(args, "dimensions"), _obj(args, "filters")
    )
    return _result(ctx, "driver_analysis", args, result)


def detect_anomalies(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metric = _str(args, "metric") or ""
    _computing(ctx, "detect_anomalies", metric)
    result = anomalies.detect_anomalies(
        ctx.analysis(),
        metric,
        _period(args),
        _str(args, "dimension"),
        _grain(args, "day"),
        _obj(args, "filters"),
    )
    return _result(ctx, "detect_anomalies", args, result)


def run_forecast(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metric = _str(args, "metric") or ""
    _computing(ctx, "forecast", metric)
    horizon = args.get("horizon", 3)
    result = forecast.forecast(
        ctx.analysis(),
        metric,
        _grain(args, "month"),
        int(horizon) if isinstance(horizon, int | float) else 3,
        _obj(args, "filters"),
    )
    return _result(ctx, "forecast", args, result)


def segment_customers(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    metric = _str(args, "metric") or ""
    _computing(ctx, "segment_customers", metric)
    kind = _str(args, "kind", "rfm")
    if kind == "cohorts":
        result = cohorts.cohort_retention(ctx.analysis(), metric)
    else:
        result = rfm.rfm_segments(ctx.analysis(), metric, _optional_period(args))
    return _result(ctx, "segment_customers", args, result)


def marketing_efficiency(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    _computing(ctx, "marketing_efficiency", None)
    tables = _list(args, "tables") or None
    result = marketing.marketing_efficiency(
        ctx.analysis(), tables, _optional_period(args), _str(args, "by", "channel") or "channel"
    )
    return _result(ctx, "marketing_efficiency", args, result)


def finance_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    kind = _str(args, "kind", "budget_variance")
    _computing(ctx, "finance_summary", kind)
    analysis = ctx.analysis()
    if kind == "budget_variance":
        result = finance.budget_variance(
            analysis, _optional_period(args), by_month=bool(args.get("by_month"))
        )
    elif kind == "receivables":
        as_of = parse_date(args["as_of"]) if args.get("as_of") else None
        result = finance.receivables_ageing(analysis, as_of=as_of)
    else:
        result = finance.pnl(analysis, _optional_period(args))
    return _result(ctx, "finance_summary", args, result)


def make_chart(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    rid = _str(args, "result_id") or ""
    frame = ctx.frames.get(rid)
    if frame is None:
        raise ToolFailure(
            "unknown_result", rid, "Use a result_id of a table result from this question."
        )
    ctx.progress({"code": "charting"})
    x = _str(args, "x") or ""
    ys = _list(args, "y")
    split = _str(args, "series")
    kind = _str(args, "type", "bar") or "bar"
    missing = [c for c in [x, *ys, *([split] if split else [])] if c not in frame.columns]
    if missing or not ys:
        raise ToolFailure(
            "unknown_column",
            ", ".join(missing) or "y",
            f"Columns: {', '.join(map(str, frame.columns))}",
        )
    data = frame.head(MAX_CHART_POINTS)
    series: list[dict[str, Any]] = []
    if split:
        categories = list(dict.fromkeys(compact(plain(v)) for v in data[x]))
        for name in dict.fromkeys(data[split]):
            part = data[data[split] == name].set_index(x)[ys[0]]
            values = {compact(plain(k)): compact(plain(v)) for k, v in part.items()}
            series.append(
                {"name": str(compact(plain(name))), "data": [values.get(c) for c in categories]}
            )
    else:
        categories = [compact(plain(v)) for v in data[x]]
        for column in ys:
            series.append({"name": column, "data": [compact(plain(v)) for v in data[column]]})
    chart_id = f"c{len(ctx.charts) + 1}"
    title = (_str(args, "title") or "")[:160]
    ctx.charts[chart_id] = {
        "chart_id": chart_id,
        "type": kind,
        "title": title,
        "x": {"name": x, "values": categories},
        "series": series,
        "result_id": rid,
        "unit": _unit_of(ctx, rid),
    }
    return {"chart_id": chart_id, "title": title, "points": len(categories), "series": len(series)}


def _unit_of(ctx: ToolContext, rid: str) -> str | None:
    facts = ctx.store.data.get(rid, {}).get("facts", {})
    unit = facts.get("unit") if isinstance(facts, dict) else None
    return unit if isinstance(unit, str) else None


HANDLERS: dict[str, Callable[[ToolContext, dict[str, Any]], dict[str, Any]]] = {
    "list_sources": list_sources,
    "describe_dataset": describe_dataset,
    "fetch_dataset": fetch_dataset,
    "run_sql": run_sql,
    "kpi_summary": kpi_summary,
    "trend": trend,
    "driver_analysis": driver_analysis,
    "detect_anomalies": detect_anomalies,
    "forecast": run_forecast,
    "segment_customers": segment_customers,
    "marketing_efficiency": marketing_efficiency,
    "finance_summary": finance_summary,
    "make_chart": make_chart,
}

HINTS = {
    "unknown_metric": "Use a metric name from list_sources, or run_sql for anything else.",
    "metric_unavailable": "That metric's table cannot be used right now.",
    "unknown_table": "Use a table key from list_sources.",
    "unknown_column": "Use a column from describe_dataset; a joined column is 'table.column'.",
    "no_join": "The dictionary has no join between these tables; use run_sql if a key matches.",
    "no_time_column": "This table has no date column; use run_sql.",
    "bad_period": "Give period as {start, end} ISO dates with start <= end.",
    "bad_date": "Dates are YYYY-MM-DD.",
    "too_little_history": "There is not enough history for a forecast at this grain.",
    "needs_accounting_data": "This needs accounting data (Zoho Books or Tally), which is not connected.",
    "no_marketing_data": "No table with spend by channel or campaign is connected.",
    "no_budget_data": "No table with budget and actual columns is connected.",
    "no_customer_column": "No customer column was found in this metric's table.",
}


def execute(ctx: ToolContext, call: ToolCall) -> ToolResult:
    """Run one tool call; failures go back to the model as an error it can act on (never an exception)."""
    started = time.monotonic()
    handler = HANDLERS.get(call.name)
    trace: dict[str, Any] = {"id": call.id, "tool": call.name, "input": compact(call.input)}
    before = len(ctx.store.provenance)
    try:
        if handler is None:
            raise ToolFailure("unknown_tool", call.name)
        content = handler(ctx, dict(call.input or {}))
        is_error = False
    except AnalyticsError as error:
        content = {"error": error.code, "detail": error.detail, "hint": HINTS.get(error.code, "")}
        is_error = True
    except ToolFailure as error:
        content = {"error": error.code, "detail": error.detail, "hint": error.hint}
        is_error = True
    except QueryError as error:
        content = {
            "error": f"sql_{error.code}",
            "detail": error.message,
            "hint": "Fix the SQL and try again.",
        }
        is_error = True
    except (KeyError, TypeError, ValueError) as error:
        logger.info("tool %s got unusable input: %s", call.name, type(error).__name__)
        content = {
            "error": "bad_input",
            "detail": str(error)[:200],
            "hint": "Check the tool's parameters.",
        }
        is_error = True
    trace["ms"] = int((time.monotonic() - started) * 1000)
    if is_error:
        trace["error"] = content.get("error")
    if len(ctx.store.provenance) > before:
        rid = f"r{len(ctx.store.provenance)}"
        trace["result_id"] = rid
        trace["sql"] = ctx.store.provenance[rid].sql
        trace["tables"] = [t.get("table") for t in ctx.store.provenance[rid].tables]
        trace["rows"] = ctx.store.provenance[rid].row_count
    if "chart_id" in content:
        trace["chart_id"] = content["chart_id"]
    ctx.trace.append(trace)
    return ToolResult(call.id, content, is_error)


def specs(ctx: ToolContext | None = None) -> list[ToolSpec]:
    """The tools offered to the model. ``query_source`` (push-down to databases) arrives with the database
    connector (M14); no connection of today's kinds supports it, so it is not offered."""
    return list(SPECS.values())
