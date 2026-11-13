"""Who may see an answer or a report (BUILD_SPEC X3 "respecting the recipient's permissions", 6.2, P6).

An answer or report shows numbers from the connections its results read (``provenance[...].tables[].connection_id``).
Someone may see it only when they may see every one of those connections:

* Owners see every connection;
* Analysts and Viewers with "all connections" see every connection;
* with "only these" they need a grant for each one; a connection that has since been removed counts as not granted.

Paused connections still count as theirs: pausing stops new reads, it does not take away what was already shared.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import ConnectionGrant, Membership


@dataclass(frozen=True)
class Permit:
    user_id: uuid.UUID
    role: str
    all_connections: bool
    granted: frozenset[uuid.UUID]

    def covers(self, connection_ids: Iterable[uuid.UUID]) -> bool:
        if self.all_connections:
            return True
        return set(connection_ids) <= self.granted


def permit(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Permit | None:
    """``None`` when the person is not an active member of the workspace."""
    membership = db.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if membership is None:
        return None
    every = membership.role == "owner" or membership.connection_access == "all"
    granted = (
        frozenset()
        if every
        else frozenset(
            db.scalars(
                select(ConnectionGrant.connection_id).where(
                    ConnectionGrant.org_id == org_id, ConnectionGrant.user_id == user_id
                )
            )
        )
    )
    return Permit(user_id, membership.role, every, granted)


def connections_of(provenance: dict[str, Any]) -> set[uuid.UUID]:
    """The connections an answer's or report's results read."""
    found: set[uuid.UUID] = set()
    for item in provenance.values():
        for table in (item or {}).get("tables") or []:
            raw = table.get("connection_id")
            if raw:
                try:
                    found.add(uuid.UUID(str(raw)))
                except ValueError:
                    continue
    return found
