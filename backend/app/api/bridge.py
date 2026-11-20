"""The API the Tally bridge calls (BUILD_SPEC 8.8, M16; DECISIONS D108-D110). Outbound only: the PC calls these;
the platform never connects to the PC.

* ``POST /bridge/tally/pair``: a one-time pairing code -> a device token (public; limited per IP address).
* ``POST /bridge/tally/poll``: heartbeat (Tally reachable? company? last sync?) and a long-poll of up to 25 s for
  "read again now". That flag is the **only** thing the platform can ask: there is no way to send the PC a
  command, a query or anything for Tally (D109).
* ``POST /bridge/tally/batches``: up to ``TALLY_MAX_BATCH_ROWS`` rows of one dataset, JSON, optionally gzip, at
  most ``TALLY_MAX_BATCH_BYTES`` before and after un-gzipping; idempotent by (sync, dataset, batch number).
* ``POST /bridge/tally/syncs/{sync_id}/complete``: all batches arrived -> remove rows Tally no longer has, then
  rebuild the connection's tables in the background.

Everything except ``pair`` needs ``Authorization: Bearer <device token>``; only its hash is stored. A revoked or
unknown token is 401. The device decides the workspace (its connection's ``org_id``): every database action runs
scoped to it under RLS, so a device can only ever reach its own connection's rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import zlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.deps import get_db, request_meta
from app.auth.ratelimit import FixedWindowLimiter
from app.config import Settings
from app.db.tenant import scope_to_org
from app.models import Connection, Organization, TallyDevice, User
from app.schemas.tally import (
    BatchIn,
    BatchOut,
    BridgeSettingsOut,
    CompleteIn,
    CompleteOut,
    PairIn,
    PairOut,
    PollIn,
    PollOut,
)
from app.tally import pairing
from app.tally.ingest import complete_sync, receive_batch, set_tally_config
from app.tasks.queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bridge/tally", tags=["tally bridge"])

Db = Annotated[DbSession, Depends(get_db)]
SMALL_BODY = 16 * 1024
REQUESTS_PER_MINUTE = 300
PAIR_WINDOW = timedelta(minutes=15)
NOT_PAIRED = (
    "This PC is not paired (or was unpaired). Pair it again with a new code from Connections."
)
BAD_CODE = (
    "This pairing code is not valid. A code works once and for a few minutes: get a new one on the "
    "Connections screen."
)

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Pairing code not valid (wrong, used or expired)"},
    401: {"description": "Not paired: no device token, or a revoked one"},
    409: {
        "description": "Not possible now (another company, paused connection, sync closed, missing batches)"
    },
    413: {"description": "Too large"},
    415: {"description": "Unknown content encoding"},
    422: {"description": "Validation error"},
    429: {"description": "Too many requests"},
}


# ---------------------------------------------------------------------------------------------- helpers
def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def bridge_settings(settings: Settings) -> BridgeSettingsOut:
    return BridgeSettingsOut(
        interval_minutes=settings.tally_sync_interval_minutes,
        full_sync_hours=settings.tally_full_sync_hours,
        max_batch_rows=settings.tally_max_batch_rows,
        max_batch_bytes=settings.tally_max_batch_bytes,
        history_months=settings.tally_history_months,
        poll_wait_seconds=settings.tally_poll_wait_seconds,
    )


def _too_large(limit: int) -> ApiError:
    return ApiError(
        413, "too_large", f"The request is larger than {limit} bytes. Send smaller batches."
    )


def _gunzip(data: bytes, limit: int) -> bytes:
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(data, limit + 1)
    except zlib.error as error:
        raise ApiError(422, "validation_error", "The request could not be unpacked.") from error
    if len(out) > limit or inflater.unconsumed_tail:
        raise _too_large(limit)
    return out


async def read_json(request: Request, limit: int) -> Any:
    """The JSON body, refused early when it is too large (before and after un-gzipping)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    data = b"".join(chunks)
    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding == "gzip":
        data = _gunzip(data, limit)
    elif encoding not in ("", "identity"):
        raise ApiError(415, "unsupported_encoding", "Send JSON, optionally gzip-compressed.")
    try:
        return json.loads(data)
    except ValueError as error:
        raise ApiError(422, "validation_error", "The request is not valid JSON.") from error


def validate[M: BaseModel](model: type[M], payload: Any) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e.get("loc", ()))} for e in error.errors(include_url=False)]
        ) from error


@dataclass
class Device:
    db: DbSession
    device: TallyDevice
    connection: Connection


def authenticate(request: Request, db: DbSession) -> Device:
    """The device behind the bearer token, with ``db`` scoped to its workspace. 401 for anything else."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not pairing.looks_like_token(token):
        raise ApiError(401, "not_paired", NOT_PAIRED)
    # Not scoped yet (the token says which workspace): only the hash is compared, in the database.
    row = db.execute(
        select(TallyDevice.id, TallyDevice.org_id).where(
            TallyDevice.token_hash == pairing.token_hash(token), TallyDevice.status == "active"
        )
    ).first()
    if row is None:
        raise ApiError(401, "not_paired", NOT_PAIRED)
    device_id, org_id = row
    scope_to_org(db, org_id)  # from here on RLS keeps this request inside the device's workspace
    device = db.scalar(
        select(TallyDevice)
        .where(TallyDevice.id == device_id, TallyDevice.status == "active")
        .with_for_update()
    )
    connection = db.get(Connection, device.connection_id) if device is not None else None
    if device is None or connection is None:
        raise ApiError(401, "not_paired", NOT_PAIRED)
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="tally-device",
        limit=REQUESTS_PER_MINUTE,
        window=timedelta(minutes=1),
    )
    if not limiter.hit(str(device.id), request.app.state.clock()):
        raise ApiError(
            429,
            "too_many_requests",
            "Too many requests from this PC.",
            headers={"Retry-After": "30"},
        )
    return Device(db=db, device=device, connection=connection)


# ------------------------------------------------------------------------------------------------ pairing
def _pair(request: Request, db: DbSession, body: PairIn) -> PairOut:
    settings = _settings(request)
    clock = request.app.state.clock
    now = clock()
    redis_client = request.app.state.redis
    ip = request_meta(request).ip or "unknown"
    limiter = FixedWindowLimiter(
        redis_client, prefix="tally-pair", limit=pairing.PAIR_ATTEMPTS, window=PAIR_WINDOW
    )
    if not limiter.hit(ip, now):
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many pairing attempts from this network. Wait 15 minutes and try again.",
            headers={"Retry-After": str(int(PAIR_WINDOW.total_seconds()))},
        )
    data = pairing.redeem_code(redis_client, body.code, now)
    if data is None:
        raise ApiError(400, "pairing_code_invalid", BAD_CODE)
    org_id = uuid.UUID(str(data["org_id"]))
    scope_to_org(db, org_id)
    connection = db.scalar(
        select(Connection)
        .where(Connection.id == uuid.UUID(str(data["connection_id"])), Connection.org_id == org_id)
        .with_for_update()
    )
    organization = db.get(Organization, org_id)
    if connection is None or connection.type != "tally" or organization is None:
        raise ApiError(400, "pairing_code_invalid", BAD_CODE)
    issued_by = db.get(User, uuid.UUID(str(data["user_id"])))
    for old in db.scalars(
        select(TallyDevice).where(
            TallyDevice.connection_id == connection.id, TallyDevice.status == "active"
        )
    ):
        old.status = "revoked"  # one PC per connection: pairing another one replaces it
        old.revoked_at = now
    db.flush()
    token = pairing.new_token()
    device = TallyDevice(
        org_id=org_id,
        connection_id=connection.id,
        name=body.device_name,
        token_hash=pairing.token_hash(token),
        status="active",
        bridge_version=body.bridge_version,
        paired_at=now,
        paired_by=issued_by.id if issued_by else None,
        last_seen_at=now,
        status_json={},
    )
    db.add(device)
    set_tally_config(connection, paired_at=now.isoformat(), unpaired_at=None)
    if connection.status != "disabled":
        connection.status = "active"
        connection.last_error = None
    db.flush()
    audit.write(
        db,
        action="connection.tally_paired",
        at=now,
        meta=request_meta(request),
        org_id=org_id,
        user_id=issued_by.id if issued_by else None,
        target_type="connection",
        target_id=connection.id,
        details={"name": connection.name, "device": body.device_name},
    )
    db.commit()
    return PairOut(
        device_id=device.id,
        token=token,
        connection_name=connection.name,
        workspace_name=organization.name,
        settings=bridge_settings(settings),
    )


@router.post(
    "/pair",
    response_model=PairOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Pair a Tally PC with a one-time code; returns its device token (shown once)",
)
async def pair(request: Request, db: Db) -> PairOut:
    body = validate(PairIn, await read_json(request, SMALL_BODY))
    return await run_in_threadpool(_pair, request, db, body)


# ------------------------------------------------------------------------------------------- heartbeat
def _heartbeat(request: Request, db: DbSession, body: PollIn) -> uuid.UUID:
    ctx = authenticate(request, db)
    now = request.app.state.clock()
    ctx.device.last_seen_at = now
    ctx.device.status_json = body.status.model_dump(mode="json")
    if body.status.bridge_version:
        ctx.device.bridge_version = body.status.bridge_version
    connection_id = ctx.connection.id
    db.commit()
    return connection_id


@router.post(
    "/poll",
    response_model=PollOut,
    responses=_ERRORS,
    summary="Heartbeat, and wait up to 25 s for a 'read Tally again now' request (the only request there is)",
)
async def poll(request: Request, db: Db) -> PollOut:
    body = validate(PollIn, await read_json(request, SMALL_BODY))
    connection_id = await run_in_threadpool(_heartbeat, request, db, body)
    settings = _settings(request)
    redis_client = request.app.state.redis
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(body.wait_seconds, settings.tally_poll_wait_seconds)
    while True:
        wanted = await run_in_threadpool(pairing.take_refresh, redis_client, connection_id)
        if wanted is not None:
            return PollOut(refresh=True, full=wanted == "full", settings=bridge_settings(settings))
        remaining = deadline - loop.time()
        if remaining <= 0:
            return PollOut(refresh=False, full=False, settings=bridge_settings(settings))
        await asyncio.sleep(min(1.0, remaining))


# --------------------------------------------------------------------------------------------- batches
def _batch(request: Request, db: DbSession, body: BatchIn) -> BatchOut:
    ctx = authenticate(request, db)
    accepted, duplicate = receive_batch(
        db,
        ctx.device,
        ctx.connection,
        body,
        now=request.app.state.clock(),
        max_rows=_settings(request).tally_max_batch_rows,
    )
    db.commit()
    return BatchOut(accepted=accepted, duplicate=duplicate)


@router.post(
    "/batches",
    response_model=BatchOut,
    responses=_ERRORS,
    summary="Rows of one Tally dataset (idempotent by sync, dataset and batch number)",
)
async def batches(request: Request, db: Db) -> BatchOut:
    body = validate(BatchIn, await read_json(request, _settings(request).tally_max_batch_bytes))
    return await run_in_threadpool(_batch, request, db, body)


def _complete(request: Request, db: DbSession, sync_id: uuid.UUID, body: CompleteIn) -> CompleteOut:
    ctx = authenticate(request, db)
    now = request.app.state.clock()
    outcome, removed = complete_sync(db, ctx.device, ctx.connection, sync_id, body, now=now)
    queue = outcome == "complete" and ctx.connection.status != "disabled"
    if queue:
        ctx.connection.sync_state = "queued"
        ctx.connection.sync_requested_at = now
    org_id, connection_id = ctx.connection.org_id, ctx.connection.id
    db.commit()
    if queue:
        jobs: JobQueue = request.app.state.job_queue
        jobs.sync_connection(org_id=org_id, connection_id=connection_id, force=True)
    return CompleteOut(status=outcome, removed=removed)


@router.post(
    "/syncs/{sync_id}/complete",
    response_model=CompleteOut,
    responses=_ERRORS,
    summary="All batches of a sync were sent: check, remove rows Tally no longer has, rebuild the tables",
)
async def complete(sync_id: uuid.UUID, request: Request, db: Db) -> CompleteOut:
    body = validate(CompleteIn, await read_json(request, SMALL_BODY))
    return await run_in_threadpool(_complete, request, db, sync_id, body)
