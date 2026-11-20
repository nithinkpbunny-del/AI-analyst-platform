"""What the Connections screen shows about a Tally connection: is the PC paired, online, is Tally open, when did
data last arrive (DECISIONS D112). Computed from the active device's heartbeat; nothing is sent to the PC."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Connection, TallyDevice

State = Literal[
    "not_paired", "offline", "tally_closed", "no_company", "tally_error", "first_sync", "connected"
]


def active_devices(db: DbSession, connection_ids: list[uuid.UUID]) -> dict[uuid.UUID, TallyDevice]:
    if not connection_ids:
        return {}
    devices = db.scalars(
        select(TallyDevice).where(
            TallyDevice.connection_id.in_(connection_ids), TallyDevice.status == "active"
        )
    )
    return {d.connection_id: d for d in devices}


def state(device: TallyDevice | None, now: datetime, offline_after_minutes: int) -> State:
    if device is None:
        return "not_paired"
    seen = device.last_seen_at
    if seen is None or now - seen > timedelta(minutes=offline_after_minutes):
        return "offline"
    reported = device.status_json or {}
    tally = reported.get("tally")
    if tally == "unreachable":
        return "tally_closed"
    if tally == "no_company":
        return "no_company"
    if tally == "error":
        return "tally_error"
    if device.last_sync_at is None:
        return "first_sync"
    return "connected"


def view(
    connection: Connection,
    device: TallyDevice | None,
    *,
    now: datetime,
    offline_after_minutes: int,
    refresh_pending: bool,
) -> dict[str, Any]:
    config = dict(connection.config.get("tally") or {})
    reported = (device.status_json or {}) if device is not None else {}
    return {
        "state": state(device, now, offline_after_minutes),
        "device_name": device.name if device else None,
        "bridge_version": device.bridge_version if device else None,
        "paired_at": device.paired_at if device else None,
        "last_seen_at": device.last_seen_at if device else None,
        "last_sync_at": config.get("last_sync_at"),
        "last_full_sync_at": config.get("last_full_sync_at"),
        "company": config.get("company") or reported.get("company"),
        "problem": reported.get("last_error"),
        "syncing": bool(reported.get("syncing")) if device else False,
        "next_sync_at": reported.get("next_sync_at"),
        "refresh_pending": refresh_pending,
        "rows": {str(k): int(v) for k, v in (config.get("rows") or {}).items()},
        "unpaired_at": config.get("unpaired_at"),
    }
