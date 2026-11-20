"""Goals and targets (BUILD_SPEC X7, 6.2, 11 "Goals"; M15).

Owners and Analysts set goals for the workspace; everyone may look at them. Each person sees only goals whose metric
(and filter columns) are in the part of the dictionary they may see; anything else answers 404, like a goal of
another workspace. Progress reads live data, so it is computed in the background with the person's permissions
(``POST /goals/refresh``) and kept for a few minutes; the screen polls ``GET /goals`` while it runs. No AI call.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.agent.access import NoAccess, VisibleDictionary, access_context, visible_dictionary
from app.analytics import goals as goal_analytics
from app.analytics.common import AnalyticsError, metric_info
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.connectors.registry import ConnectorRuntime
from app.dictionary import service as dictionary_service
from app.goals import service as goal_service
from app.models import Goal, User
from app.schemas.goals import (
    GoalList,
    GoalOptions,
    GoalOut,
    GoalRequest,
    GoalUpdate,
    MetricOption,
    RefreshRequest,
)
from app.tasks.queue import JobQueue

router = APIRouter(prefix="/goals", tags=["goals"])

View = Annotated[Tenant, requires(Permission.GOALS_VIEW)]
Edit = Annotated[Tenant, requires(Permission.GOALS_EDIT)]
MAX_GOALS = 50

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not visible to you, or in another workspace)"},
    409: {"description": "Too many goals"},
    422: {"description": "Validation error, or a metric or column you cannot use"},
}


def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _jobs(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def _visible(tenant: Tenant) -> VisibleDictionary | None:
    try:
        access = access_context(tenant.db, tenant.org_id, tenant.auth.user.id)
    except NoAccess:  # pragma: no cover - the permission check already needs an active membership
        return None
    active = dictionary_service.active(tenant.db, tenant.org_id)
    if active.dictionary is None:
        return None
    return visible_dictionary(active.dictionary, access)


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def _goal(tenant: Tenant, goal_id: uuid.UUID, seen: VisibleDictionary | None) -> Goal:
    goal = tenant.db.scalar(select(Goal).where(Goal.id == goal_id, Goal.org_id == tenant.org_id))
    if goal is None or seen is None or not goal_service.visible(seen.dictionary, goal):
        raise _not_found()
    return goal


def _goal_out(goal: Goal, seen: VisibleDictionary | None, names: dict[uuid.UUID, str]) -> GoalOut:
    unit = None
    if seen is not None:
        try:
            unit = metric_info(seen.dictionary, goal.metric_key).unit
        except AnalyticsError:
            unit = None
    return GoalOut(
        id=goal.id,
        name=goal.name,
        metric=goal.metric_key,
        metric_label=goal_service.metric_label(goal.metric_key),
        unit=unit,
        target=goal.target_value,
        direction=goal.direction,
        period_start=goal.period_start,
        period_end=goal.period_end,
        filters=dict(goal.filters or {}),
        created_by=names.get(goal.created_by) if goal.created_by else None,
        created_at=goal.created_at,
    )


def _names(tenant: Tenant, goals: list[Goal]) -> dict[uuid.UUID, str]:
    ids = {g.created_by for g in goals if g.created_by}
    if not ids:
        return {}
    return {
        u.id: (u.name or u.email) for u in tenant.db.scalars(select(User).where(User.id.in_(ids)))
    }


def _check(seen: VisibleDictionary | None, metric: str, filters: dict[str, Any]) -> Any:
    """The metric and filters must be usable by the person setting the goal."""
    if seen is None or metric not in seen.dictionary.metrics:
        raise ApiError(422, "unknown_metric", "Choose a metric from your business dictionary.")
    try:
        info = metric_info(seen.dictionary, metric)
    except AnalyticsError as error:
        raise ApiError(
            422, "metric_unavailable", "This metric cannot be used right now."
        ) from error
    if not info.time_column:
        raise ApiError(422, "no_time_column", "This metric's table has no date column.")
    if not goal_service.filters_visible(seen.dictionary, metric, filters):
        raise ApiError(422, "unknown_column", "Choose a column of this metric's table.")
    return info


def _list(request: Request, tenant: Tenant) -> GoalList:
    rt = _runtime(request)
    seen = _visible(tenant)
    goals = [
        g
        for g in goal_service.all_goals(tenant.db, tenant.org_id)
        if seen and goal_service.visible(seen.dictionary, g)
    ]
    names = _names(tenant, goals)
    tenant.db.commit()
    payload = goal_service.read(rt, tenant.org_id, tenant.auth.user.id)
    out = [_goal_out(g, seen, names) for g in goals]
    rows = (payload or {}).get("goals") or {}
    for item in out:
        found = rows.get(str(item.id))
        item.progress = found if isinstance(found, dict) else None
    computed = (payload or {}).get("computed_at")
    return GoalList(
        goals=out,
        computed_at=computed,
        refreshing=goal_service.is_busy(rt, tenant.org_id, tenant.auth.user.id),
        stale=bool(goals) and not goal_service.is_fresh(rt, payload),
        error=(payload or {}).get("error"),
        provenance=(payload or {}).get("provenance") or {},
        quality=(payload or {}).get("quality") or [],
        notes=(payload or {}).get("notes") or [],
    )


def _audit(tenant: Tenant, action: str, goal: Goal, details: dict[str, Any]) -> None:
    audit.write(
        tenant.db,
        action=action,
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="goal",
        target_id=goal.id,
        details=details,
    )


@router.get(
    "", response_model=GoalList, responses=_ERRORS, summary="The goals you may see, with progress"
)
def list_goals(request: Request, tenant: View) -> GoalList:
    return _list(request, tenant)


@router.get(
    "/options",
    response_model=GoalOptions,
    responses=_ERRORS,
    summary="Metrics (with a date) and columns you may set a goal on",
)
def options(tenant: Edit) -> GoalOptions:
    seen = _visible(tenant)
    tenant.db.commit()
    today = tenant.service.clock().date()
    if seen is None:
        return GoalOptions(metrics=[], today=today)
    metrics = []
    for key in seen.dictionary.metrics:
        try:
            info = metric_info(seen.dictionary, key)
        except AnalyticsError:
            continue
        if not info.time_column:
            continue
        entry = seen.dictionary.datasets[info.table]
        dimensions = [
            {"key": name, "label": column.label}
            for name, column in entry.columns.items()
            if column.role == "dimension"
        ]
        metrics.append(
            MetricOption(
                key=key,
                label=goal_service.metric_label(key),
                unit=info.unit,
                good_direction=info.good_direction,
                direction=goal_analytics.default_direction(info),
                additive=info.additive,
                dimensions=dimensions[:30],
            )
        )
    return GoalOptions(metrics=metrics, today=today)


@router.post(
    "",
    response_model=GoalOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Set a goal for the workspace",
)
def create_goal(body: GoalRequest, request: Request, tenant: Edit) -> GoalOut:
    seen = _visible(tenant)
    info = _check(seen, body.metric, body.filters)
    count = len(goal_service.all_goals(tenant.db, tenant.org_id))
    if count >= MAX_GOALS:
        raise ApiError(
            409, "too_many_goals", f"A workspace can have {MAX_GOALS} goals. Remove old ones first."
        )
    goal = Goal(
        org_id=tenant.org_id,
        name=body.name,
        metric_key=body.metric,
        target_value=body.target,
        direction=body.direction or goal_analytics.default_direction(info),
        period_start=body.period_start,
        period_end=body.period_end,
        filters=body.filters,
        created_by=tenant.auth.user.id,
        updated_by=tenant.auth.user.id,
    )
    tenant.db.add(goal)
    tenant.db.flush()
    _audit(tenant, "goal.created", goal, {"metric": goal.metric_key})
    tenant.db.commit()
    goal_service.forget(_runtime(request), tenant.org_id)
    return _goal_out(goal, seen, _names(tenant, [goal]))


@router.patch("/{goal_id}", response_model=GoalOut, responses=_ERRORS, summary="Change a goal")
def update_goal(goal_id: uuid.UUID, body: GoalUpdate, request: Request, tenant: Edit) -> GoalOut:
    seen = _visible(tenant)
    goal = _goal(tenant, goal_id, seen)
    changes = body.model_dump(exclude_unset=True)
    if "filters" in changes and changes["filters"] is not None:
        _check(seen, goal.metric_key, changes["filters"])
        goal.filters = changes["filters"]
    if changes.get("target") is not None:
        goal.target_value = changes["target"]
    if changes.get("direction") is not None:
        goal.direction = changes["direction"]
    if "name" in changes:
        goal.name = changes["name"]
    start = changes.get("period_start") or goal.period_start
    end = changes.get("period_end") or goal.period_end
    if end < start:
        raise ApiError(422, "bad_period", "The period ends before it starts.")
    goal.period_start, goal.period_end = start, end
    goal.updated_by = tenant.auth.user.id
    _audit(tenant, "goal.updated", goal, {"metric": goal.metric_key, "fields": sorted(changes)})
    tenant.db.commit()
    goal_service.forget(_runtime(request), tenant.org_id)
    return _goal_out(goal, seen, _names(tenant, [goal]))


@router.delete(
    "/{goal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_ERRORS,
    summary="Remove a goal",
)
def delete_goal(goal_id: uuid.UUID, request: Request, tenant: Edit) -> Response:
    goal = _goal(tenant, goal_id, _visible(tenant))
    _audit(tenant, "goal.deleted", goal, {"metric": goal.metric_key})
    tenant.db.delete(goal)
    tenant.db.commit()
    goal_service.forget(_runtime(request), tenant.org_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/refresh",
    response_model=GoalList,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Compute your goals' progress from live data (in the background)",
)
def refresh(body: RefreshRequest, request: Request, tenant: View) -> GoalList:
    rt = _runtime(request)
    me = tenant.auth.user.id
    payload = goal_service.read(rt, tenant.org_id, me)
    has_goals = tenant.db.scalar(select(Goal.id).where(Goal.org_id == tenant.org_id).limit(1))
    tenant.db.commit()
    due = has_goals and (body.force or not goal_service.is_fresh(rt, payload))
    if due and goal_service.reserve(rt, tenant.org_id, me):
        _jobs(request).refresh_goals(org_id=tenant.org_id, user_id=me)
    return _list(request, tenant)
