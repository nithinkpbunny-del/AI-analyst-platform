"""An Owner's Tally routes (M16; DECISIONS D108): add a Tally connection with a pairing code, get a new code,
unpair the PC. "Refresh now", "Test", rename, pause and remove are the shared connection routes."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.connections import _ERRORS, _audit, _one, _runtime
from app.api.deps import Tenant, requires
from app.api.errors import ApiError
from app.auth.permissions import Permission
from app.auth.ratelimit import FixedWindowLimiter
from app.connectors import access
from app.models import Connection, TallyDevice
from app.schemas.connections import ConnectionOut
from app.schemas.tally import PairingCodeOut, TallyAddRequest
from app.tally import pairing
from app.tally.ingest import set_tally_config

router = APIRouter(prefix="/connections", tags=["connections"])
ManageConnections = Annotated[Tenant, requires(Permission.CONNECTIONS_MANAGE)]
CODES_PER_HOUR = 20
UNPAIRED = "The Tally PC was unpaired. Pair a PC to send Tally data again."


class TallyAddOut(BaseModel):
    connection: ConnectionOut
    pairing: PairingCodeOut


def _code(request: Request, tenant: Tenant, connection: Connection) -> PairingCodeOut:
    limiter = FixedWindowLimiter(
        request.app.state.redis,
        prefix="tally-codes",
        limit=CODES_PER_HOUR,
        window=timedelta(hours=1),
    )
    if not limiter.hit(str(tenant.org_id), tenant.service.clock()):
        raise ApiError(
            429,
            "too_many_tally_codes",
            f"{CODES_PER_HOUR} pairing codes were made in the last hour. Try again later.",
        )
    settings = tenant.service.settings
    code = pairing.issue_code(
        _runtime(request).redis,
        org_id=tenant.org_id,
        connection_id=connection.id,
        user_id=tenant.auth.user.id,
        now=tenant.service.clock(),
        minutes=settings.tally_pairing_code_minutes,
    )
    return PairingCodeOut(
        code=code.code,
        expires_at=code.expires_at,
        server_url=settings.api_base_url.rstrip("/"),
        download_url=settings.tally_bridge_download_url or None,
    )


def _tally(tenant: Tenant, connection_id: uuid.UUID) -> Connection:
    connection = access.visible_connection(tenant.db, tenant.auth, connection_id, lock=True)
    if connection.type != "tally":
        raise ApiError(409, "not_tally", "This is not a Tally connection.")
    return connection


@router.post(
    "/tally",
    response_model=TallyAddOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Add a Tally connection and get a one-time code to pair the Tally PC's bridge",
)
def add_tally(body: TallyAddRequest, request: Request, tenant: ManageConnections) -> TallyAddOut:
    connection = Connection(
        org_id=tenant.org_id,
        type="tally",
        name=(body.name or "Tally")[:120],
        status="active",
        config={"tally": {}},
        sync_state="idle",
        created_by=tenant.auth.user.id,
    )
    tenant.db.add(connection)
    tenant.db.flush()
    code = _code(request, tenant, connection)
    _audit(tenant, "connection.added", connection, {"type": "tally", "name": connection.name})
    tenant.db.commit()
    return TallyAddOut(connection=_one(tenant, connection), pairing=code)


@router.post(
    "/{connection_id}/tally/pairing-code",
    response_model=PairingCodeOut,
    responses=_ERRORS,
    summary="A new one-time pairing code for the Tally PC (the previous code stops working)",
)
def new_pairing_code(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> PairingCodeOut:
    connection = _tally(tenant, connection_id)
    code = _code(request, tenant, connection)
    _audit(tenant, "connection.tally_code_created", connection, {"name": connection.name})
    tenant.db.commit()
    return code


@router.post(
    "/{connection_id}/tally/unpair",
    response_model=ConnectionOut,
    responses=_ERRORS,
    summary="Unpair the Tally PC: its token stops working at once (the data already sent stays)",
)
def unpair_tally(
    connection_id: uuid.UUID, request: Request, tenant: ManageConnections
) -> ConnectionOut:
    connection = _tally(tenant, connection_id)
    now = tenant.service.clock()
    devices = list(
        tenant.db.scalars(
            select(TallyDevice).where(
                TallyDevice.connection_id == connection.id, TallyDevice.status == "active"
            )
        )
    )
    for device in devices:
        device.status = "revoked"
        device.revoked_at = now
    redis_client = _runtime(request).redis
    pairing.cancel_code(redis_client, connection.id)
    pairing.clear_refresh(redis_client, connection.id)
    set_tally_config(connection, unpaired_at=now.isoformat(), refresh_requested_at=None)
    if connection.status != "disabled":
        connection.status = "needs_reauth"
        connection.last_error = UNPAIRED
    _audit(
        tenant,
        "connection.tally_unpaired",
        connection,
        {"name": connection.name, "device": devices[0].name if devices else None},
    )
    tenant.db.commit()
    return _one(tenant, connection)
