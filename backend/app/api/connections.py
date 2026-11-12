"""``/connections``: the workspace's data sources (BUILD_SPEC R2, R8, 8.1-8.3, 11 "Connections"; M6).

Who may do what (BUILD_SPEC 6.2, DECISIONS D49-D50):

* Owners (``connections.manage``) add, edit, test, refresh, re-authenticate and remove connections, upload new
  file versions and decide which connections each person sees.
* Analysts (``connections.view``) see the connections they are allowed to use, with freshness, columns, data
  notes and a preview of the first rows. Viewers do not use connections directly.
* A connection someone may not see answers 404, like one that does not exist (also for another workspace's ids).

Reading a whole source runs in the background (Celery, ``app.tasks.jobs.sync_connection``); the response says
``sync_state: "queued"`` and the web app polls until it is ``idle``. A preview reads live through the cache.
Every change is written to the activity log (``connection.*``); file names and spreadsheet titles may appear
there, data rows never do.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import re
import secrets
import uuid
from collections import defaultdict
from datetime import timedelta
from pathlib import PurePath
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode

import pandas as pd
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.connectors import access, file_formats
from app.connectors.base import ConnectorError
from app.connectors.file_formats import FileFormatError
from app.connectors.google_sheets import (
    GoogleSheetsConnector,
    GoogleSheetsSetup,
    authorization_url,
    exchange_code,
    revoke,
)
from app.connectors.google_stand_in import issue_code, stand_in_files
from app.connectors.registry import (
    SECRET_PURPOSES,
    ConnectorRuntime,
    build_connector,
    close_connector,
    file_in_use,
    spreadsheets,
    version_in_use,
)
from app.connectors.service import remove_connection
from app.data.cache import DatasetCache, DatasetMissing
from app.data.objects import file_key
from app.dictionary import replace as dictionary_replace
from app.dictionary import service as dictionary_service
from app.dictionary.model import parse as parse_dictionary
from app.models import (
    Connection,
    ConnectionGrant,
    Dataset,
    FileVersion,
    Membership,
    User,
)
from app.schemas.connections import (
    AccessList,
    AccessRequest,
    ColumnOut,
    ConnectionDetailOut,
    ConnectionList,
    ConnectionOut,
    ConnectionPatch,
    DatasetOut,
    DictionaryImpactOut,
    FileOut,
    FileVersionOut,
    GoogleFinishOut,
    GoogleFinishRequest,
    GoogleOut,
    GoogleSourceOut,
    GoogleStartOut,
    GoogleStartRequest,
    PersonAccessOut,
    PickerOut,
    PreviewOut,
    QualityOut,
    SourcesOut,
    SpreadsheetOut,
    SpreadsheetsRequest,
    TestCheckOut,
    TestOut,
)
from app.tasks.queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connections", tags=["connections"])

ViewConnections = Annotated[Tenant, requires(Permission.CONNECTIONS_VIEW)]
ManageConnections = Annotated[Tenant, requires(Permission.CONNECTIONS_MANAGE)]

PREVIEW_ROWS = 20
TESTS_PER_HOUR = 30
UPLOADS_PER_HOUR = 60
GOOGLE_STATE_TTL_SECONDS = 600
_GOOGLE_STATE = "gsheets:state:"
# A sync that has not finished after this long is assumed lost (a worker restarted); it may be started again.
STALE_SYNC = timedelta(minutes=15)
_MULTIPART_OVERHEAD = 64 * 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in"},
    403: {"description": "Your role does not allow this"},
    404: {"description": "Not found (or not visible to you, or in another workspace)"},
    409: {"description": "Not possible in the connection's current state"},
    422: {"description": "Validation error, or the file or source could not be read"},
    429: {"description": "Too many requests"},
}


# ---------------------------------------------------------------------------------------------- helpers
def _runtime(request: Request) -> ConnectorRuntime:
    runtime: ConnectorRuntime = request.app.state.connector_runtime
    return runtime


def _jobs(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def _google(request: Request) -> GoogleSheetsSetup:
    return _runtime(request).google


def _limit(request: Request, tenant: Tenant, prefix: str, limit: int, message: str) -> None:
    limiter = FixedWindowLimiter(
        request.app.state.redis, prefix=prefix, limit=limit, window=timedelta(hours=1)
    )
    if not limiter.hit(str(tenant.org_id), tenant.service.clock()):
        raise ApiError(429, f"too_many_{prefix.replace('-', '_')}", message)


def _audit(tenant: Tenant, action: str, target: Connection | None, details: dict[str, Any]) -> None:
    audit.write(
        tenant.db,
        action=action,
        at=tenant.service.clock(),
        meta=tenant.service.meta,
        org_id=tenant.org_id,
        user_id=tenant.auth.user.id,
        target_type="connection" if target is not None else None,
        target_id=target.id if target is not None else None,
        details=details,
    )


def _timezone(tenant: Tenant) -> str:
    return tenant.auth.organization.timezone


def _source_error(error: ConnectorError) -> ApiError:
    logger.info("source error: %s (%s)", error.kind, error.detail)
    if error.kind == "needs_reauth":
        return ApiError(409, "needs_reauth", error.message)
    if error.kind == "busy":
        return ApiError(409, "source_busy", error.message)
    if error.kind == "rate_limited":
        return ApiError(429, "source_rate_limited", error.message)
    if error.kind == "invalid":
        return ApiError(422, "source_invalid", error.message)
    if error.kind == "not_found":
        return ApiError(409, "source_missing", error.message)
    return ApiError(502, "source_unavailable", error.message)


def _queue_sync(
    request: Request,
    tenant: Tenant,
    connection: Connection,
    *,
    force: bool = False,
    file_version_id: uuid.UUID | None = None,
    accept_changes: bool = False,
) -> None:
    """Mark the connection as queued, commit, then hand the job to the worker."""
    connection.sync_state = "queued"
    connection.sync_requested_at = tenant.service.clock()
    tenant.db.commit()
    _jobs(request).sync_connection(
        org_id=tenant.org_id,
        connection_id=connection.id,
        force=force,
        file_version_id=file_version_id,
        accept_changes=accept_changes,
    )
    tenant.db.refresh(connection)


def _syncing(tenant: Tenant, connection: Connection) -> bool:
    if connection.sync_state == "idle" or connection.sync_requested_at is None:
        return False
    return connection.sync_requested_at > tenant.service.clock() - STALE_SYNC


# --------------------------------------------------------------------------------------- serialisation
def _columns(dataset: Dataset) -> list[ColumnOut]:
    return [
        ColumnOut(
            name=str(c.get("name", "")),
            label=str(c.get("label", c.get("name", ""))),
            type=str(c.get("type", "text")),
            unit=c.get("unit"),
            date_format=c.get("date_format"),
        )
        for c in dataset.schema_json.get("columns", [])
    ]


def _quality(items: list[dict[str, Any]]) -> list[QualityOut]:
    return [QualityOut(**item) for item in items]


def _warnings(dataset: Dataset) -> int:
    return sum(1 for q in dataset.quality_json if q.get("severity") == "warning")


class _Loaded:
    """Everything the list needs, loaded in a few queries for all connections at once."""

    def __init__(self, tenant: Tenant, connections: list[Connection]) -> None:
        ids = [c.id for c in connections]
        db = tenant.db
        self.datasets: dict[uuid.UUID, list[Dataset]] = defaultdict(list)
        self.versions: dict[uuid.UUID, list[FileVersion]] = defaultdict(list)
        if ids:
            for dataset in db.scalars(
                select(Dataset)
                .where(Dataset.connection_id.in_(ids))
                .order_by(Dataset.position, Dataset.created_at)
            ):
                self.datasets[dataset.connection_id].append(dataset)
            for version in db.scalars(
                select(FileVersion)
                .where(FileVersion.connection_id.in_(ids))
                .order_by(FileVersion.version.desc())
            ):
                self.versions[version.connection_id].append(version)
        people = {c.created_by for c in connections if c.created_by} | {
            v.uploaded_by for vs in self.versions.values() for v in vs if v.uploaded_by
        }
        self.names: dict[uuid.UUID, str] = (
            {
                row.id: row.name
                for row in db.execute(select(User.id, User.name).where(User.id.in_(people)))
            }
            if people
            else {}
        )


def _file_out(connection: Connection, versions: list[FileVersion]) -> FileOut | None:
    in_use = version_in_use(connection, versions)
    newest = versions[0] if versions else None
    shown = in_use or newest
    if shown is None or newest is None:
        return None
    waiting = newest is not in_use
    failed = waiting and newest.status == "failed"
    review = waiting and newest.status == "needs_confirmation"
    return FileOut(
        version=shown.version,
        original_name=shown.original_name,
        size_bytes=shown.size_bytes,
        uploaded_at=shown.created_at,
        latest_upload_failed=failed,
        latest_upload_error=newest.error if failed else None,
        latest_upload_needs_review=review,
        latest_upload_version=newest.version if failed or review else None,
    )


def _connection_out(connection: Connection, loaded: _Loaded) -> ConnectionOut:
    datasets = loaded.datasets.get(connection.id, [])
    google = None
    if connection.type == "google_sheets":
        google = GoogleOut(
            account_email=connection.config.get("account_email"),
            spreadsheets=[SpreadsheetOut(id=s.id, name=s.name) for s in spreadsheets(connection)],
        )
    return ConnectionOut(
        id=connection.id,
        type=connection.type,
        name=connection.name,
        status=connection.status,
        sync_state=connection.sync_state,
        last_success_at=connection.last_success_at,
        last_error=connection.last_error,
        created_at=connection.created_at,
        created_by_name=loaded.names.get(connection.created_by) if connection.created_by else None,
        datasets_count=len(datasets),
        rows_total=sum(d.row_count_estimate or 0 for d in datasets),
        warnings_count=sum(_warnings(d) for d in datasets),
        file=_file_out(connection, loaded.versions.get(connection.id, []))
        if connection.type == "file"
        else None,
        google=google,
    )


def _one(tenant: Tenant, connection: Connection) -> ConnectionOut:
    return _connection_out(connection, _Loaded(tenant, [connection]))


def _detail(tenant: Tenant, connection: Connection) -> ConnectionDetailOut:
    loaded = _Loaded(tenant, [connection])
    base = _connection_out(connection, loaded)
    versions = loaded.versions.get(connection.id, [])
    chosen = version_in_use(connection, versions)
    in_use = chosen.id if chosen else None
    return ConnectionDetailOut(
        **base.model_dump(),
        datasets=[
            DatasetOut(
                id=d.id,
                name=d.name,
                row_count=d.row_count_estimate,
                columns=_columns(d),
                last_fetched_at=d.last_fetched_at,
                quality=_quality(d.quality_json),
                last_error=d.last_error,
            )
            for d in loaded.datasets.get(connection.id, [])
        ],
        versions=[
            FileVersionOut(
                id=v.id,
                version=v.version,
                original_name=v.original_name,
                size_bytes=v.size_bytes,
                status=v.status,
                error=v.error,
                uploaded_at=v.created_at,
                uploaded_by_name=loaded.names.get(v.uploaded_by) if v.uploaded_by else None,
                in_use=v.id == in_use,
                changes=list(v.review_json),
            )
            for v in versions
        ],
        dictionary_impact=_dictionary_impact(tenant, connection),
    )


def _dictionary_impact(tenant: Tenant, connection: Connection) -> DictionaryImpactOut | None:
    """For the warning before removal: which metrics move to another source's same-named table and which would
    point at nothing (D64). ``None`` when no dictionary uses this connection."""
    base = dictionary_service.current_draft(
        tenant.db, tenant.org_id
    ) or dictionary_service.latest_confirmed(tenant.db, tenant.org_id)
    if base is None:
        return None
    impact = dictionary_replace.removal_impact(parse_dictionary(base.content), str(connection.id))
    if not (impact["moves"] or impact["orphaned_metrics"] or impact["joins"]):
        return None
    return DictionaryImpactOut.model_validate(impact)


def _json_cell(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat() if value == value.normalize() else value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    return value


# ------------------------------------------------------------------------------------------------- list
@router.get(
    "",
    response_model=ConnectionList,
    responses=_ERRORS,
    summary="The connections you may see, and which kinds of source can be added",
)
def list_connections(request: Request, tenant: ViewConnections) -> ConnectionList:
    connections = list(
        tenant.db.scalars(access.visible_connections(tenant.auth).order_by(Connection.created_at))
    )
    loaded = _Loaded(tenant, connections)
    google = _google(request)
    return ConnectionList(
        connections=[_connection_out(c, loaded) for c in connections],
        sources=SourcesOut(
            upload_max_mb=_runtime(request).settings.upload_max_mb,
            google_sheets=GoogleSourceOut(
                available=google.available, stand_in=google.mode == "stand_in"
            ),
        ),
    )


# ----------------------------------------------------------------------------------------------- access
def _active_members(tenant: Tenant) -> list[tuple[Membership, User]]:
    rows = tenant.db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == tenant.org_id, Membership.status == "active")
        .order_by(Membership.created_at)
    )
    return [(membership, user) for membership, user in rows]


def _person_access(
    membership: Membership, user: User, grants: dict[uuid.UUID, list[uuid.UUID]]
) -> PersonAccessOut:
    kind = "owner" if membership.role == "owner" else membership.connection_access
    return PersonAccessOut(
        membership_id=membership.id,
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        access=kind,
        connection_ids=grants.get(user.id, []) if kind == "selected" else [],
    )


def _grants(tenant: Tenant) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for grant in tenant.db.scalars(
        select(ConnectionGrant).where(ConnectionGrant.org_id == tenant.org_id)
    ):
        out[grant.user_id].append(grant.connection_id)
    return out


@router.get(
    "/access",
    response_model=AccessList,
    responses=_ERRORS,
    summary="Which connections each person in the workspace may see",
)
def list_access(tenant: ManageConnections) -> AccessList:
    grants = _grants(tenant)
    return AccessList(people=[_person_access(m, u, grants) for m, u in _active_members(tenant)])


@router.put(
    "/access/{membership_id}",
    response_model=PersonAccessOut,
    responses=_ERRORS,
    summary="Let someone see every connection, or only the ones listed",
)
def set_access(
    membership_id: uuid.UUID, body: AccessRequest, tenant: ManageConnections
) -> PersonAccessOut:
    membership = tenant.db.scalar(
        select(Membership)
        .where(
            Membership.id == membership_id,
            Membership.org_id == tenant.org_id,
            Membership.status == "active",
        )
        .with_for_update()
    )
    if membership is None:
        raise access.not_found()
    user = tenant.db.get(User, membership.user_id)
    assert user is not None
    if membership.role == "owner":
        raise ApiError(409, "owners_see_everything", "Owners always see every connection.")
    wanted = set(body.connection_ids) if body.access == "selected" else set()
    found = {
        c.id: c.name
        for c in tenant.db.scalars(
            select(Connection).where(Connection.org_id == tenant.org_id, Connection.id.in_(wanted))
        )
    }
    if set(found) != wanted:
        raise access.not_found()
    existing = {
        g.connection_id: g
        for g in tenant.db.scalars(
            select(ConnectionGrant).where(
                ConnectionGrant.org_id == tenant.org_id, ConnectionGrant.user_id == user.id
            )
        )
    }
    for connection_id, grant in existing.items():
        if connection_id not in wanted:
            tenant.db.delete(grant)
    for connection_id in wanted - set(existing):
        tenant.db.add(
            ConnectionGrant(org_id=tenant.org_id, connection_id=connection_id, user_id=user.id)
        )
    before = membership.connection_access
    membership.connection_access = body.access
    _audit(
        tenant,
        "connection.access_changed",
        None,
        {
            "user_id": str(user.id),
            "user_name": user.name,
            "from": before,
            "access": body.access,
            "connections": sorted(found.values()),
        },
    )
    tenant.db.commit()
    return _person_access(membership, user, {user.id: sorted(wanted, key=str)})


# ----------------------------------------------------------------------------------------------- upload
def _clean_filename(raw: str | None) -> str:
    name = PurePath((raw or "").replace("\\", "/")).name
    name = _CONTROL_CHARS.sub("", name).strip()
    return name[:255] or "upload"


async def _read_upload(request: Request) -> tuple[str, bytes, str | None]:
    limit = _runtime(request).settings.upload_max_mb * 1024 * 1024
    too_large = ApiError(
        413,
        "file_too_large",
        f"Files can be at most {_runtime(request).settings.upload_max_mb} MB. "
        "Remove sheets or rows you do not need, or split the file.",
    )
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise ApiError(411, "length_required", "The upload did not say how large it is. Try again.")
    if int(length) > limit + _MULTIPART_OVERHEAD:
        raise too_large
    form = await request.form(max_files=1, max_fields=4)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(
                422,
                "validation_error",
                "Check the highlighted fields.",
                fields={"file": "Choose a file to upload."},
            )
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise too_large
        name_field = form.get("name")
        name = name_field.strip()[:120] if isinstance(name_field, str) else None
        return _clean_filename(upload.filename), data, name or None
    finally:
        await form.close()


def _check_file(filename: str, data: bytes) -> None:
    try:
        kind = file_formats.file_kind(filename, data)
        file_formats.sheets(kind, data, filename)
    except FileFormatError as error:
        raise ApiError(422, "file_unreadable", str(error), fields={"file": str(error)}) from error


def _store_version(
    request: Request, tenant: Tenant, connection: Connection, filename: str, data: bytes
) -> FileVersion:
    number = (
        tenant.db.scalar(
            select(func.max(FileVersion.version)).where(FileVersion.connection_id == connection.id)
        )
        or 0
    ) + 1
    version = FileVersion(
        org_id=tenant.org_id,
        connection_id=connection.id,
        original_name=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        uploaded_by=tenant.auth.user.id,
        version=number,
        status="processing",
        storage_key="",
    )
    tenant.db.add(version)
    tenant.db.flush()
    version.storage_key = file_key(tenant.org_id, connection.id, version.id)
    content_type = (
        "text/csv"
        if filename.lower().endswith((".csv", ".tsv", ".txt"))
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    _runtime(request).objects.put(version.storage_key, data, content_type=content_type)
    return version


def _new_file_connection(
    request: Request, tenant: Tenant, filename: str, data: bytes, name: str | None
) -> ConnectionOut:
    _limit(request, tenant, "uploads", UPLOADS_PER_HOUR, "Too many uploads this hour. Try later.")
    _check_file(filename, data)
    connection = Connection(
        org_id=tenant.org_id,
        type="file",
        name=name or (PurePath(filename).stem[:120] or "Uploaded file"),
        status="active",
        config={},
        sync_state="idle",
        created_by=tenant.auth.user.id,
    )
    tenant.db.add(connection)
    tenant.db.flush()
    version = _store_version(request, tenant, connection, filename, data)
    _audit(
        tenant,
        "connection.added",
        connection,
        {"type": "file", "name": connection.name, "file": filename, "size_bytes": len(data)},
    )
    _queue_sync(request, tenant, connection, file_version_id=version.id)
    return _one(tenant, connection)


_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "name": {"type": "string", "maxLength": 120},
                    },
                }
            }
        },
    }
}


@router.post(
    "/files",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_ERRORS,
        411: {"description": "No Content-Length"},
        413: {"description": "Too large"},
    },
    summary="Upload an Excel (.xlsx) or CSV file as a new connection",
    openapi_extra=_UPLOAD_BODY,
)
async def upload_file(request: Request, tenant: ManageConnections) -> ConnectionOut:
    filename, data, name = await _read_upload(request)
    return await run_in_threadpool(_new_file_connection, request, tenant, filename, data, name)


def _new_version(
    request: Request, tenant: Tenant, connection_id: uuid.UUID, filename: str, data: bytes
) -> ConnectionOut:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    if connection.type != "file":
        raise ApiError(409, "not_a_file", "Only file connections take a new file.")
    _limit(request, tenant, "uploads", UPLOADS_PER_HOUR, "Too many uploads this hour. Try later.")
    _check_file(filename, data)
    version = _store_version(request, tenant, connection, filename, data)
    _audit(
        tenant,
        "connection.file_uploaded",
        connection,
        {
            "name": connection.name,
            "file": filename,
            "version": version.version,
            "size_bytes": len(data),
        },
    )
    _queue_sync(request, tenant, connection, file_version_id=version.id)
    return _one(tenant, connection)


@router.post(
    "/{connection_id}/files",
    response_model=ConnectionOut,
    responses={
        **_ERRORS,
        411: {"description": "No Content-Length"},
        413: {"description": "Too large"},
    },
    summary="Upload a new version of a file connection (the newest readable version is used)",
    openapi_extra=_UPLOAD_BODY,
)
async def upload_version(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> ConnectionOut:
    filename, data, _ = await _read_upload(request)
    return await run_in_threadpool(_new_version, request, tenant, connection_id, filename, data)


# ----------------------------------------------------------------------------------------------- Google
def _picker(request: Request, access_token: str, expires_in: int) -> PickerOut:
    google = _google(request)
    files = (
        [SpreadsheetOut(id=f.id, name=f.name) for f in stand_in_files()]
        if google.mode == "stand_in"
        else []
    )
    return PickerOut(
        access_token=access_token,
        expires_in=expires_in,
        developer_key=google.picker_api_key,
        app_id=google.app_id,
        stand_in=google.mode == "stand_in",
        stand_in_files=files,
    )


def _require_google(request: Request) -> GoogleSheetsSetup:
    google = _google(request)
    if not google.available:
        raise ApiError(
            409,
            "google_unavailable",
            "Google Sheets is not set up on this server yet. See docs/SETUP_GOOGLE.md.",
        )
    return google


def _google_connection(
    tenant: Tenant, connection_id: uuid.UUID, *, lock: bool = False
) -> Connection:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=lock)
    if connection.type != "google_sheets":
        raise ApiError(409, "not_google", "This is not a Google Sheets connection.")
    return connection


@router.post(
    "/google/start",
    response_model=GoogleStartOut,
    responses=_ERRORS,
    summary="Start connecting Google Sheets (or signing in again): returns Google's consent page",
)
def google_start(
    body: GoogleStartRequest, request: Request, tenant: ManageConnections
) -> GoogleStartOut:
    google = _require_google(request)
    login_hint = None
    if body.connection_id is not None:
        connection = _google_connection(tenant, body.connection_id)
        login_hint = connection.config.get("account_email")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    request.app.state.redis.set(
        f"{_GOOGLE_STATE}{state}",
        json.dumps(
            {
                "org_id": str(tenant.org_id),
                "user_id": str(tenant.auth.user.id),
                "verifier": verifier,
                "connection_id": str(body.connection_id) if body.connection_id else None,
            }
        ),
        ex=GOOGLE_STATE_TTL_SECONDS,
    )
    return GoogleStartOut(
        authorization_url=authorization_url(
            google,
            state=state,
            code_challenge=create_s256_code_challenge(verifier),
            login_hint=login_hint,
        )
    )


@router.get(
    "/google/callback",
    summary="Google sends the browser back here; it continues in the web app",
    response_class=RedirectResponse,
    status_code=303,
)
def google_callback(
    request: Request, state: str = "", code: str = "", error: str = ""
) -> RedirectResponse:
    """Public: the browser arrives from Google without our access token. Nothing is exchanged here; the code
    goes to the web app in the URL fragment (never sent to a server or logged), and the signed-in Owner who
    started the flow finishes it with ``POST /connections/google/finish``."""
    known = bool(state) and bool(request.app.state.redis.exists(f"{_GOOGLE_STATE}{state}"))
    if not known:
        fragment = {"error": "expired"}
    elif error or not code:
        fragment = {"error": "access_denied" if error == "access_denied" else "google_failed"}
    else:
        fragment = {"state": state, "code": code}
    base = request.app.state.settings.app_base_url.rstrip("/")
    response = RedirectResponse(f"{base}/connections/google/return#{urlencode(fragment)}", 303)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def _store_refresh_token(request: Request, tenant: Tenant, token: str) -> uuid.UUID:
    row = _runtime(request).secrets.put(
        tenant.db, org_id=tenant.org_id, purpose=SECRET_PURPOSES["google_sheets"], value=token
    )
    return row.id


@router.post(
    "/google/finish",
    response_model=GoogleFinishOut,
    responses=_ERRORS,
    summary="Finish connecting Google Sheets with the code Google returned",
)
def google_finish(
    body: GoogleFinishRequest, request: Request, tenant: ManageConnections
) -> GoogleFinishOut:
    google = _require_google(request)
    raw = request.app.state.redis.getdel(f"{_GOOGLE_STATE}{body.state}")
    expired = ApiError(
        400,
        "google_link_expired",
        "This Google sign-in expired or was already used. Start again with 'Connect Google Sheets'.",
    )
    if raw is None:
        raise expired
    saved = json.loads(raw)
    if saved["org_id"] != str(tenant.org_id) or saved["user_id"] != str(tenant.auth.user.id):
        raise expired
    http = google.http()
    try:
        try:
            tokens = exchange_code(google, http, code=body.code, code_verifier=saved["verifier"])
        except ConnectorError as error:
            raise ApiError(422, "google_refused", error.message) from error
        assert tokens.refresh_token is not None
        if saved["connection_id"]:
            connection = _google_connection(tenant, uuid.UUID(saved["connection_id"]), lock=True)
            expected = connection.config.get("account_email")
            if expected and tokens.email and expected.lower() != tokens.email.lower():
                revoke(http, tokens.refresh_token)
                raise ApiError(
                    409,
                    "google_account_mismatch",
                    f"Sign in with {expected}, the Google account this connection uses.",
                )
            old_secret = connection.secret_ref
            connection.secret_ref = _store_refresh_token(request, tenant, tokens.refresh_token)
            if tokens.email:
                connection.config = {**connection.config, "account_email": tokens.email}
            connection.status = "active"
            connection.last_error = None
            tenant.db.flush()
            if old_secret is not None:
                _runtime(request).secrets.delete(
                    tenant.db, org_id=tenant.org_id, secret_id=old_secret
                )
            _audit(
                tenant,
                "connection.reauthorized",
                connection,
                {"type": "google_sheets", "name": connection.name},
            )
            _queue_sync(request, tenant, connection)
            reconnected = True
        else:
            label = f"Google Sheets ({tokens.email})" if tokens.email else "Google Sheets"
            connection = Connection(
                org_id=tenant.org_id,
                type="google_sheets",
                name=label[:120],
                status="active",
                config={"account_email": tokens.email, "spreadsheets": []},
                secret_ref=_store_refresh_token(request, tenant, tokens.refresh_token),
                sync_state="idle",
                created_by=tenant.auth.user.id,
            )
            tenant.db.add(connection)
            tenant.db.flush()
            _audit(
                tenant,
                "connection.added",
                connection,
                {"type": "google_sheets", "name": connection.name},
            )
            tenant.db.commit()
            reconnected = False
    finally:
        http.close()
    return GoogleFinishOut(
        connection=_one(tenant, connection),
        picker=_picker(request, tokens.access_token, tokens.expires_in),
        reconnected=reconnected,
    )


def _sheets_connector(
    request: Request, tenant: Tenant, connection: Connection
) -> GoogleSheetsConnector:
    try:
        connector = build_connector(
            tenant.db, connection, _runtime(request), timezone=_timezone(tenant)
        )
    except ConnectorError as error:
        raise _source_error(error) from error
    assert isinstance(connector, GoogleSheetsConnector)
    return connector


def _mark_reauth(tenant: Tenant, connection: Connection, error: ConnectorError) -> None:
    if error.kind == "needs_reauth":
        connection.status = "needs_reauth"
        connection.last_error = error.message
        tenant.db.commit()


@router.post(
    "/{connection_id}/google/picker-token",
    response_model=PickerOut,
    responses=_ERRORS,
    summary="A short-lived Google access token for the Picker, to choose more spreadsheets",
)
def picker_token(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> PickerOut:
    _require_google(request)
    connection = _google_connection(tenant, connection_id)
    connector = _sheets_connector(request, tenant, connection)
    try:
        tokens = connector.access_token()
    except ConnectorError as error:
        _mark_reauth(tenant, connection, error)
        raise _source_error(error) from error
    finally:
        connector.close()
    return _picker(request, tokens.access_token, tokens.expires_in)


@router.put(
    "/{connection_id}/google/spreadsheets",
    response_model=ConnectionOut,
    responses=_ERRORS,
    summary="Set which picked spreadsheets this connection reads (each tab becomes a dataset)",
)
def set_spreadsheets(
    connection_id: uuid.UUID,
    body: SpreadsheetsRequest,
    request: Request,
    tenant: ManageConnections,
) -> ConnectionOut:
    _require_google(request)
    connection = _google_connection(tenant, connection_id, lock=True)
    before = {s.id: s.name for s in spreadsheets(connection)}
    connector = _sheets_connector(request, tenant, connection)
    chosen: list[dict[str, str]] = []
    try:
        for spreadsheet_id in body.spreadsheet_ids:
            try:
                title = connector.spreadsheet_title(spreadsheet_id)
            except ConnectorError as error:
                _mark_reauth(tenant, connection, error)
                if error.kind == "needs_reauth":
                    raise _source_error(error) from error
                raise ApiError(
                    422,
                    "spreadsheet_unreadable",
                    "One of the chosen spreadsheets cannot be read. Choose it again with the Picker.",
                    fields={"spreadsheet_ids": error.message},
                ) from error
            chosen.append({"id": spreadsheet_id, "name": title[:200]})
    finally:
        close_connector(connector)
    after = {s["id"]: s["name"] for s in chosen}
    connection.config = {**connection.config, "spreadsheets": chosen}
    _audit(
        tenant,
        "connection.spreadsheets_changed",
        connection,
        {
            "name": connection.name,
            "added": sorted(after[i] for i in after if i not in before),
            "removed": sorted(before[i] for i in before if i not in after),
        },
    )
    _queue_sync(request, tenant, connection)
    return _one(tenant, connection)


# -------------------------------------------------------------------------------------- one connection
@router.get(
    "/{connection_id}",
    response_model=ConnectionDetailOut,
    responses=_ERRORS,
    summary="A connection with its datasets (columns, data notes) and file versions",
)
def get_connection(connection_id: uuid.UUID, tenant: ViewConnections) -> ConnectionDetailOut:
    return _detail(tenant, access.visible_connection(tenant.db, tenant.auth, connection_id))


@router.patch(
    "/{connection_id}",
    response_model=ConnectionOut,
    responses=_ERRORS,
    summary="Rename a connection, or pause and resume it",
)
def update_connection(
    connection_id: uuid.UUID, body: ConnectionPatch, request: Request, tenant: ManageConnections
) -> ConnectionOut:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    resume = False
    if body.name is not None and body.name != connection.name:
        _audit(tenant, "connection.renamed", connection, {"from": connection.name, "to": body.name})
        connection.name = body.name
    if body.paused is True and connection.status != "disabled":
        connection.status = "disabled"
        _audit(tenant, "connection.paused", connection, {"name": connection.name})
    elif body.paused is False and connection.status == "disabled":
        connection.status = "active"
        resume = True
        _audit(tenant, "connection.resumed", connection, {"name": connection.name})
    if resume:
        _queue_sync(request, tenant, connection)
    else:
        tenant.db.commit()
    return _one(tenant, connection)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Remove a connection: its stored files, cached data and credentials are deleted at once",
)
def delete_connection(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> Response:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    _audit(
        tenant,
        "connection.removed",
        connection,
        {"type": connection.type, "name": connection.name},
    )
    remove_connection(tenant.db, _runtime(request), connection)
    tenant.db.commit()
    # Its tables leave the business dictionary's draft (the confirmed version stays until an Owner confirms).
    if dictionary_service.mark_queued(_runtime(request), tenant.org_id):
        _jobs(request).draft_dictionary(org_id=tenant.org_id, user_id=tenant.auth.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{connection_id}/test",
    response_model=TestOut,
    responses=_ERRORS,
    summary="Check that the source can still be read (read-only), without reading the data",
)
def test_connection(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> TestOut:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    _limit(
        request,
        tenant,
        "connection-tests",
        TESTS_PER_HOUR,
        f"Connections were tested {TESTS_PER_HOUR} times in the last hour. Try again later.",
    )
    try:
        connector = build_connector(
            tenant.db, connection, _runtime(request), timezone=_timezone(tenant)
        )
    except ConnectorError as error:
        _mark_reauth(tenant, connection, error)
        return TestOut(ok=False, message=error.message, checks=[])
    try:
        result = connector.test()
    finally:
        close_connector(connector)
    if result.ok and connection.status in ("error", "needs_reauth"):
        connection.status = "active"
        connection.last_error = None
    elif not result.ok and connection.status != "disabled":
        reauth = any(c.label == "signed_in" and not c.ok for c in result.checks)
        connection.status = "needs_reauth" if reauth else "error"
        connection.last_error = result.message
    tenant.db.commit()
    return TestOut(
        ok=result.ok,
        message=result.message,
        checks=[TestCheckOut(label=c.label, ok=c.ok) for c in result.checks],
    )


@router.post(
    "/{connection_id}/refresh",
    response_model=ConnectionOut,
    responses=_ERRORS,
    summary="Read every dataset of the connection again now (in the background)",
)
def refresh_connection(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> ConnectionOut:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    if connection.status == "disabled":
        raise ApiError(409, "connection_paused", "This connection is paused. Resume it first.")
    if _syncing(tenant, connection):
        raise ApiError(409, "already_reading", "This connection is being read already.")
    _queue_sync(request, tenant, connection, force=True)
    return _one(tenant, connection)


def _file_version(tenant: Tenant, connection: Connection, version_id: uuid.UUID) -> FileVersion:
    if connection.type != "file":
        raise ApiError(409, "not_a_file", "Only file connections have versions.")
    version = tenant.db.scalar(
        select(FileVersion)
        .where(
            FileVersion.id == version_id,
            FileVersion.connection_id == connection.id,
            FileVersion.org_id == tenant.org_id,
        )
        .with_for_update()
    )
    if version is None:
        raise access.not_found()
    return version


@router.post(
    "/{connection_id}/versions/{version_id}/use",
    response_model=ConnectionOut,
    responses=_ERRORS,
    summary="Use this file version: a new upload that waits for review, or an earlier version (switch back)",
)
def use_version(
    connection_id: uuid.UUID, version_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> ConnectionOut:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    version = _file_version(tenant, connection, version_id)
    if _syncing(tenant, connection):
        raise ApiError(409, "already_reading", "This connection is being read already.")
    current = file_in_use(tenant.db, connection)
    if current is not None and current.id == version.id:
        raise ApiError(409, "already_in_use", f"Version {version.version} is already in use.")
    if version.status not in ("ready", "needs_confirmation"):
        raise ApiError(
            409,
            "version_unusable",
            f"Version {version.version} could not be read, so it cannot be used. Upload the file again.",
        )
    if version.status == "needs_confirmation":
        version.status = "processing"
    _audit(
        tenant,
        "connection.version_used",
        connection,
        {"name": connection.name, "version": version.version, "file": version.original_name},
    )
    _queue_sync(request, tenant, connection, file_version_id=version.id, accept_changes=True)
    return _one(tenant, connection)


@router.delete(
    "/{connection_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="Discard a file version that is not in use (its stored file is deleted)",
)
def discard_version(
    connection_id: uuid.UUID, version_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> Response:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    version = _file_version(tenant, connection, version_id)
    current = file_in_use(tenant.db, connection)
    if current is not None and current.id == version.id:
        raise ApiError(
            409,
            "version_in_use",
            "This version is in use. Use another version first, or remove the connection.",
        )
    if version.status == "processing":
        raise ApiError(409, "already_reading", "This version is being read. Try again in a moment.")
    _audit(
        tenant,
        "connection.version_discarded",
        connection,
        {"name": connection.name, "version": version.version, "file": version.original_name},
    )
    _runtime(request).objects.delete(version.storage_key)
    tenant.db.delete(version)
    tenant.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{connection_id}/datasets/{dataset_id}/preview",
    response_model=PreviewOut,
    responses=_ERRORS,
    summary=f"The first {PREVIEW_ROWS} rows of a dataset, read live (through the short reuse cache)",
)
def preview_dataset(
    connection_id: uuid.UUID, dataset_id: uuid.UUID, request: Request, tenant: ViewConnections
) -> PreviewOut:
    connection, dataset = access.visible_dataset(tenant.db, tenant.auth, connection_id, dataset_id)
    if connection.status == "disabled":
        raise ApiError(409, "connection_paused", "This connection is paused. Resume it first.")
    tenant.db.commit()  # no transaction stays open while the source is read
    try:
        cached = DatasetCache(_runtime(request), tenant.org_id).get(dataset.id)
    except DatasetMissing as error:
        raise access.not_found() from error
    except ConnectorError as error:
        raise _source_error(error) from error
    tenant.db.refresh(dataset)
    head = cached.frame.head(PREVIEW_ROWS).astype(object)
    rows = [[_json_cell(v) for v in row] for row in head.itertuples(index=False, name=None)]
    return PreviewOut(
        dataset_id=dataset.id,
        columns=_columns(dataset),
        rows=rows,
        row_count=cached.row_count,
        fetched_at=cached.fetched_at,
        checked_at=cached.checked_at,
        source=cached.source,
        quality=_quality(cached.quality),
    )


# ------------------------------------------------------------------------- local development stand-in
_STAND_IN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google Sheets stand-in</title>
<style>
body{{margin:0;font:16px/1.55 system-ui,sans-serif;background:#F5F6FA;color:#1E1C3A}}
main{{max-width:440px;margin:48px 16px;padding:24px;background:#fff;border:1px solid #E2E4EE;border-radius:16px}}
h1{{font-size:24px;line-height:1.15;margin:0 0 8px}} p{{margin:0 0 16px;color:#5A5874}}
ul{{margin:0 0 16px;padding-left:20px}} label{{display:block;font-weight:500;margin:16px 0 6px}}
input[type=email]{{box-sizing:border-box;width:100%;min-height:44px;padding:0 12px;border:1px solid #85839F;
border-radius:10px;font:inherit}}
.actions{{display:flex;gap:12px;margin-top:24px}}
button{{min-height:44px;padding:0 20px;border-radius:10px;font:inherit;font-weight:600}}
.allow{{border:0;background:#3A36A8;color:#fff}} .deny{{border:1px solid #85839F;background:#fff;color:#1E1C3A}}
</style></head><body><main>
<h1>Google Sheets stand-in</h1>
<p>Local development only. Nothing goes to Google: the spreadsheets come from the demo data on this computer.
Add real Google credentials with docs/SETUP_GOOGLE.md.</p>
<p>Analyst wants to:</p>
<ul><li>See your Google Sheets spreadsheets (read only)</li>
<li>See the spreadsheets you choose with the Picker</li></ul>
<form method="post" action="/connections/google/stand-in">
<input type="hidden" name="state" value="{state}">
<label for="email">Google account</label>
<input id="email" name="email" type="email" required autocomplete="email" value="{email}">
<div class="actions"><button class="allow" type="submit" name="decision" value="allow">Allow</button>
<button class="deny" type="submit" name="decision" value="deny" formnovalidate>Cancel</button></div>
</form></main></body></html>"""


def _stand_in_only(request: Request) -> None:
    if _google(request).mode != "stand_in":
        raise access.not_found()


@router.get("/google/stand-in", include_in_schema=False)
def stand_in_consent(request: Request, state: str = "", login_hint: str = "") -> HTMLResponse:
    _stand_in_only(request)
    page = _STAND_IN_PAGE.format(state=html.escape(state), email=html.escape(login_hint))
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@router.post("/google/stand-in", include_in_schema=False)
async def stand_in_decide(request: Request) -> RedirectResponse:
    _stand_in_only(request)
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    query: dict[str, str] = {"state": form.get("state", "")}
    email = form.get("email", "").strip()
    if form.get("decision") == "allow" and "@" in email:
        query["code"] = issue_code(email)
    else:
        query["error"] = "access_denied"
    return RedirectResponse(f"{_google(request).redirect_uri()}?{urlencode(query)}", 303)
