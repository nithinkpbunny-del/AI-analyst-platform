"""Reading the activity log (BUILD_SPEC 6.7): filters, keyset pages and CSV export.

Owners see every entry of their workspace; Analysts only their own (BUILD_SPEC 6.2); Viewers none. The caller
decides the scope with ``require()``/``allowed()`` and passes ``only_user_id`` here. Runs in a tenant-scoped
session, so Row-Level Security limits it to the workspace as well as the ``org_id`` filter below.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.auth.devices import describe_user_agent
from app.models import AuditLog, User

PAGE_SIZE = 50
EXPORT_MAX_ROWS = 20_000
# Spreadsheet apps run cells that start with these as formulas ("CSV injection").
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class AuditFilters:
    user_id: uuid.UUID | None = None
    action: str | None = None  # exact action or a prefix such as "auth.login" or "team"
    date_from: date | None = None  # inclusive, in the workspace's time zone
    date_to: date | None = None  # inclusive


@dataclass(frozen=True)
class AuditRow:
    entry: AuditLog
    user_name: str | None
    user_email: str | None


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def encode_cursor(entry: AuditLog) -> str:
    raw = f"{entry.at.astimezone(UTC).isoformat()}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, _, entry_id = base64.urlsafe_b64decode(padded.encode()).decode().partition("|")
        return datetime.fromisoformat(at), uuid.UUID(entry_id)
    except ValueError as error:
        raise ApiError(
            422, "validation_error", "Check the highlighted fields.", fields={"cursor": "Invalid."}
        ) from error


def _query(
    org_id: uuid.UUID, timezone: str, filters: AuditFilters, only_user_id: uuid.UUID | None
) -> Any:
    query = (
        select(AuditLog, User.name, User.email)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.org_id == org_id)
    )
    user_id = only_user_id or filters.user_id
    if only_user_id and filters.user_id and filters.user_id != only_user_id:
        # Someone else's entries are simply not there for this caller.
        query = query.where(AuditLog.id.is_(None))
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if filters.action:
        action = filters.action.strip().rstrip(".")
        query = query.where(
            (AuditLog.action == action) | AuditLog.action.startswith(f"{action}.", autoescape=True)
        )
    zone = _zone(timezone)
    if filters.date_from:
        query = query.where(AuditLog.at >= datetime.combine(filters.date_from, time.min, zone))
    if filters.date_to:
        end = datetime.combine(filters.date_to + timedelta(days=1), time.min, zone)
        query = query.where(AuditLog.at < end)
    return query.order_by(AuditLog.at.desc(), AuditLog.id.desc())


def list_entries(
    db: DbSession,
    *,
    org_id: uuid.UUID,
    timezone: str,
    filters: AuditFilters,
    only_user_id: uuid.UUID | None,
    cursor: str | None = None,
    limit: int = PAGE_SIZE,
) -> tuple[list[AuditRow], str | None]:
    query = _query(org_id, timezone, filters, only_user_id)
    if cursor:
        at, entry_id = decode_cursor(cursor)
        query = query.where(tuple_(AuditLog.at, AuditLog.id) < tuple_(at, entry_id))
    rows = [AuditRow(e, n, m) for e, n, m in db.execute(query.limit(limit + 1)).all()]
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1].entry) if more and rows else None


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


CSV_HEADER = ["Time", "Person", "Email", "Action", "Target", "IP address", "Browser", "Details"]


def export_csv(
    db: DbSession,
    *,
    org_id: uuid.UUID,
    timezone: str,
    filters: AuditFilters,
    only_user_id: uuid.UUID | None,
) -> tuple[str, int, bool]:
    """CSV text, the number of rows and whether it was cut at ``EXPORT_MAX_ROWS``."""
    zone = _zone(timezone)
    query = _query(org_id, timezone, filters, only_user_id).limit(EXPORT_MAX_ROWS + 1)
    rows = db.execute(query).all()
    truncated = len(rows) > EXPORT_MAX_ROWS
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for entry, name, email in rows[:EXPORT_MAX_ROWS]:
        target = f"{entry.target_type}:{entry.target_id}" if entry.target_type else ""
        writer.writerow(
            [
                _cell(entry.at.astimezone(zone).isoformat(timespec="seconds")),
                _cell(name),
                _cell(email),
                _cell(entry.action),
                _cell(target),
                _cell(entry.ip),
                _cell(describe_user_agent(entry.user_agent) if entry.user_agent else ""),
                _cell(json.dumps(entry.details, sort_keys=True) if entry.details else ""),
            ]
        )
    return buffer.getvalue(), min(len(rows), EXPORT_MAX_ROWS), truncated
