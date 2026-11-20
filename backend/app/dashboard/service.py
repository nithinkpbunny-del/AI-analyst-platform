"""Pinned dashboard tiles (BUILD_SPEC X8; M15): what a tile keeps, and re-running it for whoever looks at it.

**Pinning** (``build_spec``) keeps the answer's analytics, never its words: the tool calls that produced the results
the answer cited and the charts it showed, which result holds the key figure (and where in it), and the day the
answer was made. Asking again would need the AI; re-running the tools does not (P4: every number is code).

**Dates.** A tile pinned with ``dates = rolling`` moves its periods forward as time passes: a calendar month stays
"that many months back" (likewise quarters, years and Monday-to-Sunday weeks); other periods move by days. ``fixed``
keeps the dates. SQL the agent wrote itself (``run_sql``, ``query_source``) is replayed as written, so its dates do
not move; the tile says so.

**Re-running** (``replay``) happens in the background with the permissions of the person looking (BUILD_SPEC 6.2):
a tile whose tables they may not see is not shown to them, and a tile that no longer fits the dictionary says it is
out of date instead of guessing. Results are kept per tile and person in Redis (aggregates only, at most a day,
BUILD_SPEC 6.6); a tile is re-run when its ``refresh_minutes`` have passed or someone presses Refresh.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from calendar import monthrange
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from app.agent import provenance
from app.agent.provenance import ResultStore
from app.agent.tools import HANDLERS, ToolContext, execute
from app.connectors.registry import ConnectorRuntime
from app.db.tenant import scope_to_org
from app.llm.base import ToolCall
from app.models import DashboardTile, Message

logger = logging.getLogger(__name__)

SPEC_VERSION = 1
MAX_CALLS = 16
MAX_TABLE_ROWS = 8
KEEP_SECONDS = 24 * 3600
BUSY_SECONDS = 300
RESULT = "dashboard:tile:{org}:{tile}:{user}"
BUSY = "dashboard:busy:{org}:{tile}:{user}"
NOT_REPLAYED = {"list_sources", "describe_dataset"}
SQL_TOOLS = {"run_sql", "query_source"}
LOADING_TOOLS = {"fetch_dataset", "query_source"}


class NotPinnable(Exception):
    """The answer has nothing a tile can re-run (no results from data)."""


# ----------------------------------------------------------------------------------------------- pinning
def build_spec(message: Message, *, question: str, dates: str, anchor: date) -> dict[str, Any]:
    answer = dict(message.answer_json or {})
    trace = [t for t in (message.tool_trace or []) if isinstance(t, dict)]
    charts = [c for c in answer.get("charts") or [] if isinstance(c, dict)]
    headline = _headline(answer, dict(message.provenance or {}))
    needed = set(answer.get("cited") or [])
    needed |= {str(c.get("result_id")) for c in charts}
    if headline:
        needed.add(headline["result_id"])
    shown_charts = [str(c.get("chart_id")) for c in charts]
    calls: list[dict[str, Any]] = []
    for item in trace:
        tool = str(item.get("tool"))
        if item.get("error") or tool not in HANDLERS or tool in NOT_REPLAYED:
            continue
        rid = item.get("result_id")
        keep = (
            tool in LOADING_TOOLS
            or (rid in needed)
            or (tool == "make_chart" and item.get("chart_id") in shown_charts)
        )
        if keep:
            calls.append(
                {
                    "id": str(item.get("id") or f"t{len(calls) + 1}"),
                    "tool": tool,
                    "input": item.get("input") or {},
                    "result_id": rid,
                    "chart_id": item.get("chart_id"),
                    "tables": [t for t in item.get("tables") or [] if t],
                }
            )
    calls = calls[-MAX_CALLS:]
    if not any(c["result_id"] for c in calls):
        raise NotPinnable
    produced = {c["result_id"] for c in calls if c["result_id"]}
    table_rid = None
    if headline and headline["result_id"] in produced:
        table_rid = headline["result_id"]
    else:
        headline = None
        table_rid = next((r for r in answer.get("cited") or [] if r in produced), None)
    tables = sorted({t for c in calls for t in c["tables"]})
    return {
        "version": SPEC_VERSION,
        "question": question[:1000],
        "lens": message.lens,
        "anchor": anchor.isoformat(),
        "dates": dates,
        "calls": calls,
        "charts": [c for c in shown_charts if any(k["chart_id"] == c for k in calls)],
        "headline": headline,
        "table_result": table_rid,
        "tables": tables,
        "fixed_sql": any(c["tool"] in SQL_TOOLS for c in calls),
    }


def _headline(answer: dict[str, Any], prov: dict[str, Any]) -> dict[str, Any] | None:
    """Where the answer's key figure lives in its cited result: a column of one row (kept by the row's labels)."""
    lead = str(answer.get("lead") or "")
    key = answer.get("key_figure")
    if not isinstance(key, str) or not key or key not in lead:
        return None
    at = lead.find(key)
    figure = next(
        (f for f in provenance.figures(lead) if at <= f.start < at + len(key) and f.cited), None
    )
    if figure is None:
        return None
    rid = figure.cited[0]
    data = (prov.get(rid) or {}).get("data") or {}
    columns = [str(c) for c in data.get("columns") or []]
    rows = data.get("rows") or []
    percent = "%" in figure.text
    for index, row in enumerate(rows):
        for column, cell in zip(columns, row, strict=False):
            if isinstance(cell, bool) or not isinstance(cell, int | float):
                continue
            for n, candidate in enumerate(figure.candidates):
                tolerance = figure.tolerance if n == 0 else figure.tolerance / 100.0
                if abs(abs(cell) - abs(candidate)) <= tolerance:
                    return {
                        "result_id": rid,
                        "column": column,
                        "row": _labels(columns, row),
                        "index": index,
                        "format": "pct" if percent else "inr" if "₹" in figure.text else "number",
                        "scale": 100.0 if percent and n > 0 else 1.0,
                    }
    return None


def _labels(columns: list[str], row: list[Any]) -> dict[str, Any]:
    """The text cells of a row: they say which row it is ("metric": "revenue", "series": "South")."""
    return {
        c: v for c, v in zip(columns, row, strict=False) if isinstance(v, str) and len(v) <= 200
    }


# ------------------------------------------------------------------------------------------ rolling dates
def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _add_months(day: date, months: int) -> date:
    index = _month_index(day) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def shift_period(start: date, end: date, anchor: date, today: date) -> tuple[date, date]:
    """Move a period by the time between ``anchor`` and ``today`` in its own calendar unit."""
    if anchor == today:
        return start, end
    after = end + timedelta(days=1)
    if start.day == 1 and after.day == 1:
        span = _month_index(after) - _month_index(start)
        unit = 1
        if span % 12 == 0 and start.month == 1:
            unit = 12
        elif span % 3 == 0 and start.month in (1, 4, 7, 10):
            unit = 3
        months = (_month_index(today) // unit - _month_index(anchor) // unit) * unit
        return _add_months(start, months), _add_months(after, months) - timedelta(days=1)
    if start.weekday() == 0 and ((end - start).days + 1) % 7 == 0:
        weeks = (
            (today - timedelta(days=today.weekday())) - (anchor - timedelta(days=anchor.weekday()))
        ).days
        return start + timedelta(days=weeks), end + timedelta(days=weeks)
    days = (today - anchor).days
    return start + timedelta(days=days), end + timedelta(days=days)


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def shift_inputs(value: Any, anchor: date, today: date) -> Any:
    """Every ``{start, end}`` period and ``as_of`` date inside a tool's input, moved forward."""
    if isinstance(value, dict):
        start, end = _as_date(value.get("start")), _as_date(value.get("end"))
        out = {k: shift_inputs(v, anchor, today) for k, v in value.items()}
        if start and end and start <= end:
            new_start, new_end = shift_period(start, end, anchor, today)
            out["start"], out["end"] = new_start.isoformat(), new_end.isoformat()
        as_of = _as_date(value.get("as_of"))
        if as_of:
            out["as_of"] = (as_of + (today - anchor)).isoformat()
        return out
    if isinstance(value, list):
        return [shift_inputs(v, anchor, today) for v in value]
    return value


# ------------------------------------------------------------------------------------------------ replay
def visible_to(spec: dict[str, Any], ctx: ToolContext) -> bool:
    """A tile is shown only to people who may see every table it reads (BUILD_SPEC 6.2)."""
    return all(t in ctx.visible.tables for t in spec.get("tables") or [])


def replay(ctx: ToolContext, spec: dict[str, Any]) -> dict[str, Any]:
    """Re-run a tile's tool calls for the person of ``ctx``; returns what the tile shows."""
    if not visible_to(spec, ctx):
        return {"status": "error", "error": "no_access"}
    anchor = _as_date(spec.get("anchor")) or ctx.today
    rolling = spec.get("dates") == "rolling"
    mapping: dict[str, str] = {}
    charts: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for call in spec.get("calls") or []:
        args = copy.deepcopy(call.get("input") or {})
        if rolling and call.get("tool") not in SQL_TOOLS:
            args = shift_inputs(args, anchor, ctx.today)
        if call.get("tool") == "make_chart":
            args["result_id"] = mapping.get(str(args.get("result_id")), "missing")
        result = execute(ctx, ToolCall(str(call.get("id")), str(call.get("tool")), args))
        content = result.content if isinstance(result.content, dict) else {}
        if result.is_error:
            failed.append(str(content.get("error") or "failed"))
            continue
        if call.get("result_id") and "result_id" in content:
            mapping[str(call["result_id"])] = str(content["result_id"])
        if call.get("chart_id") and "chart_id" in content:
            charts[str(call["chart_id"])] = ctx.charts[str(content["chart_id"])]
    shown = [charts[c] for c in spec.get("charts") or [] if c in charts]
    headline = _read_headline(ctx, spec.get("headline"), mapping)
    table = None
    table_rid = mapping.get(str(spec.get("table_result")))
    if not shown and table_rid:
        data = ctx.store.data.get(table_rid) or {}
        table = {
            "result_id": table_rid,
            "columns": data.get("columns") or [],
            "rows": list(data.get("rows") or [])[:MAX_TABLE_ROWS],
            "total_rows": data.get("total_rows"),
        }
    used = {str(c["result_id"]) for c in shown}
    if headline:
        used.add(str(headline["result_id"]))
    if table:
        used.add(str(table["result_id"]))
    if not used:
        return {"status": "error", "error": _error_code(failed)}
    notes = []
    if rolling and spec.get("fixed_sql"):
        notes.append("fixed_sql_dates")
    if failed:
        notes.append("partly_out_of_date")
    tables = {t for rid in used for t in _tables_of(ctx, rid)}
    return {
        "status": "ok",
        "headline": headline,
        "charts": shown,
        "table": table,
        "provenance": {rid: ctx.store.provenance[rid].as_json() for rid in sorted(used)},
        "quality": [
            {"table": t, "name": ctx.loaded.get(t, {}).get("name", t), **q}
            for t in sorted(tables)
            for q in ctx.quality.get(t, [])[:2]
        ],
        "notes": notes,
    }


def _tables_of(ctx: ToolContext, rid: str) -> list[str]:
    prov = ctx.store.provenance.get(rid)
    return [str(t.get("table")) for t in prov.tables if t.get("table")] if prov else []


def _error_code(failed: list[str]) -> str:
    if any(f == "source_error" for f in failed):
        return "source_error"
    return "out_of_date" if failed else "no_result"


def _read_headline(
    ctx: ToolContext, locator: Any, mapping: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(locator, dict):
        return None
    rid = mapping.get(str(locator.get("result_id")))
    data = ctx.store.data.get(rid or "") or {}
    columns = [str(c) for c in data.get("columns") or []]
    rows = data.get("rows") or []
    column = str(locator.get("column"))
    if column not in columns or not rows:
        return None
    wanted = locator.get("row") or {}
    chosen = None
    if wanted:
        for row in rows:
            labels = _labels(columns, row)
            if all(labels.get(k) == v for k, v in wanted.items()):
                chosen = row
                break
    else:
        index = int(locator.get("index") or 0)
        chosen = rows[index] if index < len(rows) else None
    if chosen is None:
        return None
    cells = dict(zip(columns, chosen, strict=False))
    value = cells.get(column)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    scale = float(locator.get("scale") or 1.0)
    delta = cells.get("change_pct") if column != "change_pct" else None
    return {
        "result_id": rid,
        "value": float(value) * scale,
        "format": locator.get("format") or "number",
        "delta_pct": float(delta) if isinstance(delta, int | float) else None,
        "good_direction": cells.get("good_direction")
        if cells.get("good_direction") in ("up", "down")
        else None,
        "label": next((v for v in _labels(columns, chosen).values()), None),
    }


# ------------------------------------------------------------------------------------------------- cache
def _key(template: str, org_id: uuid.UUID, tile_id: uuid.UUID | str, user_id: uuid.UUID) -> str:
    return template.format(org=org_id, tile=tile_id, user=user_id)


def read(
    rt: ConnectorRuntime, org_id: uuid.UUID, tile_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any] | None:
    raw = rt.redis.get(_key(RESULT, org_id, tile_id, user_id))
    if not raw:
        return None
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def save(
    rt: ConnectorRuntime,
    org_id: uuid.UUID,
    tile_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    stored = {**payload, "computed_at": rt.clock().isoformat()}
    rt.redis.set(
        _key(RESULT, org_id, tile_id, user_id), json.dumps(stored, default=str), ex=KEEP_SECONDS
    )


def is_fresh(rt: ConnectorRuntime, payload: dict[str, Any] | None, minutes: int) -> bool:
    if not payload or "computed_at" not in payload:
        return False
    try:
        at = datetime.fromisoformat(str(payload["computed_at"]))
    except ValueError:
        return False
    return rt.clock() - at < timedelta(minutes=minutes)


def is_busy(
    rt: ConnectorRuntime, org_id: uuid.UUID, tile_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    return bool(rt.redis.exists(_key(BUSY, org_id, tile_id, user_id)))


def reserve(
    rt: ConnectorRuntime, org_id: uuid.UUID, tile_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    return bool(rt.redis.set(_key(BUSY, org_id, tile_id, user_id), "1", nx=True, ex=BUSY_SECONDS))


def forget(rt: ConnectorRuntime, org_id: uuid.UUID, tile_id: uuid.UUID) -> None:
    """Drop every person's kept result of a tile (its dates changed, or it was removed)."""
    for key in rt.redis.scan_iter(match=RESULT.format(org=org_id, tile=tile_id, user="*")):
        rt.redis.delete(key)


# --------------------------------------------------------------------------------------------------- job
def refresh(
    rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID, tile_ids: list[uuid.UUID]
) -> int:
    """The background job: re-run these tiles for this person and keep the results. Returns how many ran."""
    from app.agent.viewer import NotReady, viewer_context
    from app.dashboard.access import tiles_for

    done = 0
    try:
        with rt.session_factory() as db:
            scope_to_org(db, org_id)
            tiles = {
                t.id: (t.id, dict(t.spec))
                for t in tiles_for(db, org_id, user_id)
                if t.id in tile_ids
            }
            db.commit()
        try:
            with viewer_context(rt, org_id, user_id, pushdown=True) as viewer:
                for tile_id, spec in tiles.values():
                    ctx = replace(
                        viewer.ctx,
                        store=ResultStore(),
                        charts={},
                        frames={},
                        trace=[],
                        notes=[],
                    )
                    try:
                        payload = replay(ctx, spec)
                    except Exception:
                        logger.exception("a dashboard tile failed")
                        payload = {"status": "error", "error": "internal"}
                    save(rt, org_id, tile_id, user_id, payload)
                    done += 1
        except NotReady as error:
            for tile_id, _ in tiles.values():
                save(rt, org_id, tile_id, user_id, {"status": "error", "error": error.code})
    finally:
        for tile_id in tile_ids:
            rt.redis.delete(_key(BUSY, org_id, tile_id, user_id))
    return done


def title_of(question: str) -> str:
    text = " ".join(question.split())
    return (text[:197] + "…") if len(text) > 200 else (text or "Pinned answer")


def tile_tables(tile: DashboardTile) -> list[str]:
    return [str(t) for t in (tile.spec or {}).get("tables") or []]
