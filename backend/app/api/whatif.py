"""The What-if screen's API (BUILD_SPEC 10.4, R12; M9): what can be simulated, the values of a scope, and a run.

A run is the agent's ``what_if`` tool called directly: the same code, the same numbers, **no AI call** (so moving
a slider costs nothing and counts as no question). It uses the asking person's own permissions, like the agent:
tables of connections they may not see are not there (P6). Asking is allowed for Owners and Analysts.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request

from app.agent import tools
from app.agent.access import NoAccess, VisibleDictionary, access_context, visible_dictionary
from app.agent.provenance import ResultStore
from app.analytics import whatif
from app.analytics.common import AnalyticsError, metric_info
from app.analytics.marketing import find_columns
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.connectors.registry import ConnectorRuntime
from app.data.cache import DatasetCache
from app.data.workspace import Workspace
from app.dictionary import service as dictionary_service
from app.schemas.whatif import (
    ChannelSourceOut,
    DimensionOut,
    MetricOptionOut,
    OptionsOut,
    RunOut,
    RunRequest,
    ValuesOut,
)
from app.security.sql_guard import safe_identifier as q

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatif", tags=["what-if"])

Ask = Annotated[Tenant, requires(Permission.ASK)]

RUNS_PER_MINUTE = 60
MAX_VALUES = 50
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    409: {"description": "No business dictionary or no data to simulate on"},
    422: {"description": "The scenario cannot be simulated as asked"},
    429: {"description": "Too many runs in the last minute"},
}


def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _context(request: Request, tenant: Tenant) -> tools.ToolContext:
    """The tool context of this person: their visible dictionary, an empty workspace and the live data cache."""
    runtime = _runtime(request)
    try:
        access = access_context(tenant.db, tenant.org_id, tenant.auth.user.id)
    except NoAccess as error:  # pragma: no cover - the request was authenticated a moment ago
        raise ApiError(403, "forbidden", "You do not have access to this workspace.") from error
    active = dictionary_service.active(tenant.db, tenant.org_id)
    tenant.db.commit()  # nothing more is read from the database; do not hold it while data is fetched
    if active.dictionary is None:
        raise ApiError(409, "no_dictionary", "Connect data and confirm the dictionary first.")
    visible = visible_dictionary(active.dictionary, access)
    if not visible.dictionary.datasets:
        raise ApiError(409, "no_sources", "There is no data you can use yet.")
    settings = runtime.settings
    zone = ZoneInfo(access.timezone)
    return tools.ToolContext(
        access=access,
        visible=visible,
        workspace=Workspace(
            timeout_seconds=settings.source_query_timeout_seconds,
            max_rows=settings.rows_to_llm_per_tool_call,
        ),
        cache=DatasetCache(runtime, tenant.org_id),
        store=ResultStore(),
        today=runtime.clock().astimezone(zone).date(),
        max_rows=settings.rows_to_llm_per_tool_call,
    )


def _limit(request: Request, tenant: Tenant) -> None:
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="whatif",
        limit=RUNS_PER_MINUTE,
        window=timedelta(minutes=1),
    )
    if not limiter.hit(str(tenant.auth.user.id), tenant.service.clock()):
        raise ApiError(
            429,
            "too_many_runs",
            "Too many scenarios in the last minute. Wait a moment and try again.",
        )


def _analytics_error(error: AnalyticsError) -> ApiError:
    """A scenario that cannot be run: the code is stable, the web app writes the sentence (D41)."""
    return ApiError(422, f"whatif_{error.code}", tools.HINTS.get(error.code) or error.code)


@router.get(
    "/options", response_model=OptionsOut, responses=_ERRORS, summary="What can be simulated"
)
def options(request: Request, tenant: Ask) -> OptionsOut:
    try:
        access = access_context(tenant.db, tenant.org_id, tenant.auth.user.id)
    except NoAccess as error:  # pragma: no cover
        raise ApiError(403, "forbidden", "You do not have access to this workspace.") from error
    active = dictionary_service.active(tenant.db, tenant.org_id)
    tenant.db.commit()
    if active.dictionary is None:
        return OptionsOut(
            metrics=[], channels=None, industry=access.industry, max_horizon=whatif.MAX_HORIZON
        )
    visible = visible_dictionary(active.dictionary, access)
    return OptionsOut(
        metrics=_metric_options(visible),
        channels=_channel_source(visible),
        industry=access.industry,
        max_horizon=whatif.MAX_HORIZON,
    )


def _metric_options(visible: VisibleDictionary) -> list[MetricOptionOut]:
    dictionary = visible.dictionary
    out: list[MetricOptionOut] = []
    for key in dictionary.metrics:
        try:
            info = metric_info(dictionary, key)
        except AnalyticsError:
            continue
        if not info.time_column or not info.additive or info.unit not in ("INR", "count"):
            continue
        entry = dictionary.datasets[info.table]
        cols = whatif.sales_columns(entry)
        levers: list[str] = []
        if "gross" in cols or "unit_price" in cols:
            levers.append("price")
            if "discount" in cols:
                levers.append("discount")
        if not levers:
            continue
        dimensions = [
            DimensionOut(table=info.table, column=name, label=column.label)
            for name, column in entry.columns.items()
            if column.role == "dimension"
        ]
        out.append(
            MetricOptionOut(
                key=key,
                label=key.replace("_", " "),
                unit=info.unit,
                levers=levers,
                dimensions=dimensions,
            )
        )
    return out


def _channel_source(visible: VisibleDictionary) -> ChannelSourceOut | None:
    """A table with spend, revenue and a channel column (read from the dictionary only, no data)."""
    for table, entry in visible.dictionary.datasets.items():
        cols = find_columns(entry)
        if {"spend", "revenue", "channel"} <= cols.keys():
            return ChannelSourceOut(table=table, column=cols["channel"])
    return None


@router.get(
    "/values", response_model=ValuesOut, responses=_ERRORS, summary="The values of a scope column"
)
def values(
    request: Request,
    tenant: Ask,
    table: Annotated[str, Query(min_length=1, max_length=120)],
    column: Annotated[str, Query(min_length=1, max_length=120)],
) -> ValuesOut:
    _limit(request, tenant)
    ctx = _context(request, tenant)
    entry = ctx.dictionary.datasets.get(table)
    found = entry.columns.get(column) if entry else None
    if entry is None or found is None or found.role != "dimension":
        raise ApiError(404, "not_found", "Not found.")
    try:
        ctx.ensure(table)
        frame = ctx.workspace.frame(
            f"SELECT CAST({q(column)} AS VARCHAR) AS v, COUNT(*) AS n FROM {q(table)} "  # noqa: S608
            f"WHERE {q(column)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_VALUES}"
        )
    except AnalyticsError as error:
        raise _analytics_error(error) from error
    except tools.ToolFailure as error:
        raise ApiError(
            409, "source_error", error.detail or "The data could not be read."
        ) from error
    return ValuesOut(values=[str(v) for v in frame["v"].tolist()])


@router.post(
    "/run", response_model=RunOut, responses=_ERRORS, summary="Run a scenario (no AI call)"
)
def run(request: Request, tenant: Ask, body: RunRequest) -> RunOut:
    _limit(request, tenant)
    ctx = _context(request, tenant)
    args: dict[str, Any] = {
        "lever": body.lever,
        "change": body.change,
        "metric": body.metric,
        "scope": dict(body.scope),
        "horizon": body.horizon,
        "assumptions": dict(body.assumptions),
    }
    try:
        content = tools.run_what_if(ctx, args)
    except AnalyticsError as error:
        raise _analytics_error(error) from error
    except tools.ToolFailure as error:
        raise ApiError(
            409, "source_error", error.detail or "The data could not be read."
        ) from error
    provenance = ctx.store.provenance[content["result_id"]]
    return RunOut(
        facts=content["facts"],
        columns=content["columns"],
        rows=content["rows"],
        notes=content["notes"],
        tables=provenance.tables,
        sql=provenance.sql,
        quality_warnings=content.get("quality_warnings", []),
    )
