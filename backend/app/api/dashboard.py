"""The pinned dashboard (BUILD_SPEC X8, 6.2, 11 "Dashboard"; M15).

* ``POST /dashboard/tiles`` pins one of your own finished answers as a tile (Owners and Analysts).
* ``GET /dashboard`` lists your tiles in your order, then tiles others show on everyone's dashboard that your access
  covers, each with its latest result computed **for you** (or none yet).
* ``POST /dashboard/refresh`` re-runs tiles in the background with your permissions: the ones whose
  ``refresh_minutes`` have passed, or all of them with ``force``. The screen polls ``GET /dashboard`` meanwhile.
  Re-running uses the analytics only (no AI call) and the live-data cache (R1).

A tile is personal: someone else's tile id answers 404 for changes, like an id of another workspace.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission, allowed
from app.connectors.registry import ConnectorRuntime
from app.dashboard import access as tile_access
from app.dashboard import service as dashboard
from app.models import Conversation, DashboardTile, Message, User
from app.schemas.goals import (
    Dashboard,
    OrderRequest,
    PinRequest,
    TileOut,
    TileRefreshRequest,
    TileUpdate,
)
from app.tasks.queue import JobQueue

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

View = Annotated[Tenant, requires(Permission.DASHBOARD_VIEW)]
Edit = Annotated[Tenant, requires(Permission.DASHBOARD_EDIT)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not yours, or in another workspace)"},
    409: {"description": "Nothing in this answer can be pinned, or too many tiles"},
    422: {"description": "Validation error"},
}


def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _jobs(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def _own(tenant: Tenant, tile_id: uuid.UUID) -> DashboardTile:
    tile = tenant.db.scalar(
        select(DashboardTile).where(
            DashboardTile.id == tile_id,
            DashboardTile.org_id == tenant.org_id,
            DashboardTile.user_id == tenant.auth.user.id,
        )
    )
    if tile is None:
        raise _not_found()
    return tile


def _visible_tiles(tenant: Tenant) -> list[DashboardTile]:
    me = tenant.auth.user.id
    tiles = tile_access.tiles_for(tenant.db, tenant.org_id, me)
    if any(t.user_id != me for t in tiles):
        tables = tile_access.visible_tables(tenant.db, tenant.org_id, me)
        tiles = [t for t in tiles if tile_access.can_see(t, me, tables)]
    return tiles


def _tile_out(
    rt: ConnectorRuntime, tenant: Tenant, tile: DashboardTile, names: dict[uuid.UUID, str]
) -> TileOut:
    me = tenant.auth.user.id
    spec = dict(tile.spec or {})
    result = dashboard.read(rt, tenant.org_id, tile.id, me)
    mine = tile.user_id == me
    conversation_id = None
    if mine and tile.message_id is not None:
        conversation_id = tenant.db.scalar(
            select(Message.conversation_id).where(
                Message.id == tile.message_id, Message.org_id == tenant.org_id
            )
        )
    return TileOut(
        id=tile.id,
        title=tile.title,
        mine=mine,
        owner_name=None if mine else names.get(tile.user_id),
        shared=tile.shared,
        refresh_minutes=tile.refresh_minutes,
        dates=str(spec.get("dates") or "fixed"),
        fixed_sql=bool(spec.get("fixed_sql")),
        question=str(spec.get("question") or ""),
        conversation_id=conversation_id,
        message_id=tile.message_id if conversation_id else None,
        created_at=tile.created_at,
        result=result,
        refreshing=dashboard.is_busy(rt, tenant.org_id, tile.id, me),
        stale=not dashboard.is_fresh(rt, result, tile.refresh_minutes),
    )


def _board(request: Request, tenant: Tenant, tiles: list[DashboardTile] | None = None) -> Dashboard:
    rt = _runtime(request)
    tiles = _visible_tiles(tenant) if tiles is None else tiles
    owners = {t.user_id for t in tiles if t.user_id != tenant.auth.user.id}
    names = (
        {
            u.id: (u.name or u.email)
            for u in tenant.db.scalars(select(User).where(User.id.in_(owners)))
        }
        if owners
        else {}
    )
    out = [_tile_out(rt, tenant, t, names) for t in tiles]
    tenant.db.commit()
    return Dashboard(
        tiles=out,
        can_edit=allowed(tenant.auth, Permission.DASHBOARD_EDIT),
        limit=tile_access.MAX_TILES,
    )


def _audit(tenant: Tenant, action: str, tile: DashboardTile, details: dict[str, Any]) -> None:
    audit.write(
        tenant.db,
        action=action,
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="tile",
        target_id=tile.id,
        details=details,
    )


def _queue(request: Request, tenant: Tenant, tiles: list[DashboardTile], *, force: bool) -> None:
    rt = _runtime(request)
    me = tenant.auth.user.id
    due = []
    for tile in tiles:
        if not force and dashboard.is_fresh(
            rt, dashboard.read(rt, tenant.org_id, tile.id, me), tile.refresh_minutes
        ):
            continue
        if dashboard.reserve(rt, tenant.org_id, tile.id, me):
            due.append(tile.id)
    if due:
        _jobs(request).refresh_tiles(org_id=tenant.org_id, user_id=me, tile_ids=due)


@router.get("", response_model=Dashboard, responses=_ERRORS, summary="Your dashboard")
def get_dashboard(request: Request, tenant: View) -> Dashboard:
    return _board(request, tenant)


@router.post(
    "/tiles",
    response_model=TileOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Pin one of your answers to your dashboard",
)
def pin(body: PinRequest, request: Request, tenant: Edit) -> TileOut:
    me = tenant.auth.user.id
    found = tenant.db.execute(
        select(Message, Conversation)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == body.message_id,
            Message.org_id == tenant.org_id,
            Message.role == "assistant",
            Conversation.user_id == me,
        )
    ).first()
    if found is None:
        raise _not_found()
    message, conversation = found
    if message.status != "done" or not message.answer_json:
        raise ApiError(409, "not_pinnable", "Only a finished answer can be pinned.")
    count = tenant.db.scalar(
        select(func.count())
        .select_from(DashboardTile)
        .where(DashboardTile.org_id == tenant.org_id, DashboardTile.user_id == me)
    )
    if (count or 0) >= tile_access.MAX_TILES:
        raise ApiError(
            409,
            "too_many_tiles",
            f"Your dashboard has {tile_access.MAX_TILES} tiles. Remove one before pinning another.",
        )
    question = tenant.db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == conversation.id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    ) or str(message.content)
    zone = ZoneInfo(tenant.auth.organization.timezone)
    anchor = (message.finished_at or message.created_at).astimezone(zone).date()
    try:
        spec = dashboard.build_spec(message, question=question, dates=body.dates, anchor=anchor)
    except dashboard.NotPinnable as error:
        raise ApiError(
            409,
            "not_pinnable",
            "This answer has no figures from your data to keep up to date, so it cannot be pinned.",
        ) from error
    first = tenant.db.scalar(
        select(func.min(DashboardTile.position)).where(
            DashboardTile.org_id == tenant.org_id, DashboardTile.user_id == me
        )
    )
    tile = DashboardTile(
        org_id=tenant.org_id,
        user_id=me,
        title=body.title or dashboard.title_of(question),
        message_id=message.id,
        spec=spec,
        position=(first - 1) if first is not None else 0,
        refresh_minutes=60,
        shared=False,
    )
    tenant.db.add(tile)
    tenant.db.flush()
    _audit(tenant, "dashboard.tile_pinned", tile, {"dates": body.dates})
    tenant.db.commit()
    rt = _runtime(request)
    # The answer's own results are fresh: the tile runs once now so it shows numbers at once.
    _queue(request, tenant, [tile], force=True)
    tenant.db.expire_all()
    return _tile_out(rt, tenant, tile, {})


@router.patch(
    "/tiles/{tile_id}",
    response_model=TileOut,
    responses=_ERRORS,
    summary="Rename a tile, show it on everyone's dashboard, or change how often it refreshes",
)
def update_tile(tile_id: uuid.UUID, body: TileUpdate, request: Request, tenant: Edit) -> TileOut:
    tile = _own(tenant, tile_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "title" in changes:
        tile.title = changes["title"]
    if "refresh_minutes" in changes:
        tile.refresh_minutes = changes["refresh_minutes"]
    if "shared" in changes and changes["shared"] != tile.shared:
        tile.shared = changes["shared"]
        _audit(
            tenant,
            "dashboard.tile_shared" if tile.shared else "dashboard.tile_unshared",
            tile,
            {},
        )
    rt = _runtime(request)
    if "dates" in changes and changes["dates"] != (tile.spec or {}).get("dates"):
        tile.spec = {**dict(tile.spec or {}), "dates": changes["dates"]}
        dashboard.forget(rt, tenant.org_id, tile.id)
    tenant.db.commit()
    return _tile_out(rt, tenant, tile, {})


@router.delete(
    "/tiles/{tile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_ERRORS,
    summary="Remove a tile from your dashboard",
)
def delete_tile(tile_id: uuid.UUID, request: Request, tenant: Edit) -> Response:
    tile = _own(tenant, tile_id)
    _audit(tenant, "dashboard.tile_removed", tile, {})
    tenant.db.delete(tile)
    tenant.db.commit()
    dashboard.forget(_runtime(request), tenant.org_id, tile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/order",
    response_model=Dashboard,
    responses=_ERRORS,
    summary="Put your tiles in this order (tiles not listed keep their place after them)",
)
def reorder(body: OrderRequest, request: Request, tenant: Edit) -> Dashboard:
    me = tenant.auth.user.id
    mine = list(
        tenant.db.scalars(
            select(DashboardTile)
            .where(DashboardTile.org_id == tenant.org_id, DashboardTile.user_id == me)
            .order_by(DashboardTile.position, DashboardTile.created_at)
        )
    )
    by_id = {t.id: t for t in mine}
    ordered = [by_id[i] for i in dict.fromkeys(body.tile_ids) if i in by_id]
    ordered += [t for t in mine if t not in ordered]
    for position, tile in enumerate(ordered):
        tile.position = position
    tenant.db.commit()
    return _board(request, tenant)


@router.post(
    "/refresh",
    response_model=Dashboard,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERRORS,
    summary="Re-run tiles with your permissions (due ones, or all with force)",
)
def refresh(body: TileRefreshRequest, request: Request, tenant: View) -> Dashboard:
    tiles = _visible_tiles(tenant)
    if body.tile_ids is not None:
        wanted = set(body.tile_ids)
        tiles = [t for t in tiles if t.id in wanted]
    tenant.db.commit()
    _queue(request, tenant, tiles, force=body.force)
    tenant.db.expire_all()
    return _board(request, tenant)
