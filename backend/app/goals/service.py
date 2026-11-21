"""Goals (BUILD_SPEC X7; M15): who may see which goal, and their progress for one person.

* A goal is shown to a person only when its metric is in the part of the dictionary they may see, and every filter
  names a column they may see (``visible_goals``). Others do not learn that it exists.
* Progress reads live data (cache-aware, R1), so it is computed in the background (``refresh``) with the person's
  permissions, through the same ``goal_progress`` tool the agent uses (every figure has provenance), and kept in
  Redis for ``FRESH_MINUTES``. No AI call is made. A briefing stores the progress it computed the same way, so
  Today can show it without reading any source.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.agent.access import VisibleDictionary
from app.agent.tools import ToolContext, execute
from app.analytics.common import AnalyticsError, Period, metric_info
from app.analytics.goals import GoalSpec
from app.connectors.registry import ConnectorRuntime
from app.dictionary import labels
from app.dictionary.model import Dictionary
from app.llm.base import ToolCall
from app.models import Goal

logger = logging.getLogger(__name__)

FRESH_MINUTES = 15
KEEP_SECONDS = 24 * 3600  # aggregates only; no longer than a snapshot (BUILD_SPEC 6.6)
BUSY_SECONDS = 300
PROGRESS = "goals:progress:{org}:{user}"
BUSY = "goals:busy:{org}:{user}"


def metric_label(key: str, dictionary: Dictionary | None = None) -> str:
    """The metric in words ("Total fee" for ``sum_fee``; M19), from the dictionary when it is at hand."""
    return labels.metric_label(dictionary, key)


# ------------------------------------------------------------------------------------------- visibility
def filters_visible(dictionary: Dictionary, metric: str, filters: dict[str, Any]) -> bool:
    """Every filter is a column of the metric's table, or ``table.column`` of another table the person may see."""
    try:
        info = metric_info(dictionary, metric)
    except AnalyticsError:
        return False
    for spec in filters:
        table, _, column = spec.partition(".") if "." in spec else (info.table, "", spec)
        entry = dictionary.datasets.get(table)
        if entry is None or column not in entry.columns:
            return False
    return True


def visible(dictionary: Dictionary, goal: Goal | GoalSpec) -> bool:
    metric = goal.metric_key if isinstance(goal, Goal) else goal.metric
    filters = goal.filters or {}
    if metric not in dictionary.metrics:
        return False
    return filters_visible(dictionary, metric, dict(filters))


def spec_of(goal: Goal) -> GoalSpec:
    return GoalSpec(
        metric=goal.metric_key,
        target=float(goal.target_value),
        period=Period(goal.period_start, goal.period_end),
        direction="at_most" if goal.direction == "at_most" else "at_least",
        filters=dict(goal.filters or {}),
        goal_id=str(goal.id),
        name=goal.name,
    )


def all_goals(db: DbSession, org_id: uuid.UUID) -> list[Goal]:
    """Every goal of the workspace, the newest period first."""
    return list(
        db.scalars(
            select(Goal)
            .where(Goal.org_id == org_id)
            .order_by(Goal.period_end.desc(), Goal.created_at.desc())
        )
    )


def all_specs(db: DbSession, org_id: uuid.UUID) -> list[GoalSpec]:
    """Plain copies (usable after the session closes)."""
    return [spec_of(g) for g in all_goals(db, org_id)]


def visible_specs(specs: list[GoalSpec], seen: VisibleDictionary) -> list[GoalSpec]:
    return [s for s in specs if visible(seen.dictionary, s)]


def visible_goals(
    db: DbSession, org_id: uuid.UUID, seen: VisibleDictionary
) -> list[tuple[Goal, GoalSpec]]:
    """The workspace's goals this person may see, newest period first."""
    return [(g, spec_of(g)) for g in all_goals(db, org_id) if visible(seen.dictionary, g)]


def current(specs: list[GoalSpec], today: date, *, ended_within_days: int = 7) -> list[GoalSpec]:
    """Goals worth a mention today: running, or ended in the last week (briefings)."""
    since = today - timedelta(days=ended_within_days)
    return [g for g in specs if g.period.start <= today and g.period.end >= since]


# --------------------------------------------------------------------------------------------- progress
def _key(template: str, org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return template.format(org=org_id, user=user_id)


def compute(ctx: ToolContext, specs: list[GoalSpec] | None = None) -> dict[str, Any]:
    """Progress of ``specs`` (default: every goal in ``ctx``) through the ``goal_progress`` tool."""
    chosen = ctx.goals if specs is None else specs
    if not chosen:
        return {"goals": {}, "provenance": {}, "notes": [], "quality": []}
    args: dict[str, Any] = {"goal_ids": [g.goal_id for g in chosen if g.goal_id]}
    result = execute(ctx, ToolCall("g1", "goal_progress", args))
    content = result.content if isinstance(result.content, dict) else {}
    if result.is_error or "result_id" not in content:
        logger.info("goals: progress failed (%s)", content.get("error"))
        return {
            "goals": {},
            "provenance": {},
            "notes": [],
            "quality": [],
            "error": str(content.get("error") or "failed"),
        }
    rid = str(content["result_id"])
    columns = list(content.get("columns") or [])
    rows = {}
    for raw in content.get("rows") or []:
        row = dict(zip(columns, raw, strict=False))
        row["result_id"] = rid
        rows[str(row.get("goal_id"))] = row
    prov = ctx.store.provenance[rid].as_json()
    quality = [q for items in ctx.quality.values() for q in items]
    return {
        "goals": rows,
        "provenance": {rid: prov},
        "notes": list(content.get("notes") or []),
        "quality": quality,
    }


def save(
    rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    stored = {**payload, "computed_at": rt.clock().isoformat()}
    rt.redis.set(_key(PROGRESS, org_id, user_id), json.dumps(stored, default=str), ex=KEEP_SECONDS)


def read(rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
    raw = rt.redis.get(_key(PROGRESS, org_id, user_id))
    if not raw:
        return None
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def is_fresh(rt: ConnectorRuntime, payload: dict[str, Any] | None) -> bool:
    if not payload or "computed_at" not in payload:
        return False
    try:
        at = datetime.fromisoformat(str(payload["computed_at"]))
    except ValueError:
        return False
    return rt.clock() - at < timedelta(minutes=FRESH_MINUTES)


def is_busy(rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return bool(rt.redis.exists(_key(BUSY, org_id, user_id)))


def reserve(rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Mark a refresh as queued; ``False`` when one is already on its way."""
    return bool(rt.redis.set(_key(BUSY, org_id, user_id), "1", nx=True, ex=BUSY_SECONDS))


def forget(rt: ConnectorRuntime, org_id: uuid.UUID) -> None:
    """Drop everyone's cached progress of the workspace (a goal changed)."""
    for key in rt.redis.scan_iter(match=PROGRESS.format(org=org_id, user="*")):
        rt.redis.delete(key)


def refresh(rt: ConnectorRuntime, org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """The background job: compute this person's progress and keep it. Returns ``done`` or why not."""
    from app.agent.viewer import NotReady, viewer_context

    try:
        with viewer_context(rt, org_id, user_id) as viewer:
            payload = compute(viewer.ctx)
        status = "done"
    except NotReady as error:
        payload = {"goals": {}, "provenance": {}, "notes": [], "quality": [], "error": error.code}
        status = error.code
    except Exception:
        logger.exception("goal progress failed")
        payload = {"goals": {}, "provenance": {}, "notes": [], "quality": [], "error": "internal"}
        status = "internal"
    finally:
        rt.redis.delete(_key(BUSY, org_id, user_id))
    save(rt, org_id, user_id, payload)
    return status
