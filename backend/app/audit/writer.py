"""Write activity-log entries.

Callers add entries to the current database session; they are committed with the rest of the change (or on
their own, e.g. a failed sign-in that otherwise changes nothing). Never put secrets, tokens, passwords or
customer data rows into ``details``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models import AuditLog


@dataclass(frozen=True)
class RequestMeta:
    """Who is calling from where. Built once per request."""

    ip: str | None
    user_agent: str | None


def write(
    db: DbSession,
    *,
    action: str,
    at: datetime,
    meta: RequestMeta,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        org_id=org_id,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip=meta.ip,
        user_agent=meta.user_agent[:512] if meta.user_agent else None,
        details=details or {},
        at=at,
    )
    db.add(entry)
    return entry


def write_for_orgs(
    db: DbSession,
    org_ids: list[uuid.UUID],
    *,
    action: str,
    at: datetime,
    meta: RequestMeta,
    user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """One entry per organisation the user belongs to, so each Owner sees it; one org-less entry otherwise."""
    targets: list[uuid.UUID | None] = [*org_ids] if org_ids else [None]
    for org_id in targets:
        write(
            db,
            action=action,
            at=at,
            meta=meta,
            org_id=org_id,
            user_id=user_id,
            target_type="user" if user_id else None,
            target_id=user_id,
            details=details,
        )
