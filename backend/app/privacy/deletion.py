"""Deleting a workspace (BUILD_SPEC 6.6: Owner requests, 7-day grace, hard delete, anonymised audit record).

* **Request** (an Owner, typing the workspace's name): ``deletion_requested_at`` is set; every Owner gets an email
  with the date. The workspace keeps working during the grace period, with a banner; briefings stop.
* **Cancel**: any Owner, any time before the date.
* **Purge** (beat job, hourly): for each workspace past its date, every connection is removed the normal way (its
  files, cached snapshots and secrets deleted, Google tokens revoked), report files are deleted from storage, then
  the workspace row is deleted and the database cascades to every table that belongs to it. One anonymised entry
  ``org.deleted`` stays in the activity log: no names, only counts and dates.

People's accounts are not deleted with a workspace: an account is global and may belong to other workspaces
(D85, final in D124). The workspace's earlier activity-log entries are kept, anonymised (``anonymise_activity``):
what happened and when stays; who, from where and on what goes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.audit import writer as audit
from app.connectors import service as connections
from app.connectors.registry import ConnectorRuntime
from app.db.tenant import scope_to_org
from app.models import Connection, Conversation, Membership, Organization, Report

logger = logging.getLogger(__name__)

GRACE = timedelta(days=7)
NO_META = audit.RequestMeta(None, None)
ANONYMISED_DETAILS = '{"anonymised": true}'


def due_at(org: Organization) -> datetime | None:
    return org.deletion_requested_at + GRACE if org.deletion_requested_at else None


def purge_due(rt: ConnectorRuntime, now: datetime) -> int:
    """Delete every workspace whose grace period has ended. Returns how many were deleted."""
    with rt.session_factory() as db:
        due = list(
            db.scalars(
                select(Organization.id).where(
                    Organization.deletion_requested_at.is_not(None),
                    Organization.deletion_requested_at <= now - GRACE,
                )
            )
        )
        db.commit()
    for org_id in due:
        try:
            purge(rt, org_id, now)
        except Exception:
            logger.exception("deleting a workspace failed; it is tried again next hour")
    return len(due)


def purge(rt: ConnectorRuntime, org_id: uuid.UUID, now: datetime) -> None:
    db = rt.session_factory()
    scope_to_org(db, org_id)
    with db:
        org = db.get(Organization, org_id)
        if org is None or org.deletion_requested_at is None:
            db.commit()
            return
        requested = org.deletion_requested_at
        counts = {
            "members": _count(db, Membership, org_id),
            "connections": _count(db, Connection, org_id),
            "conversations": _count(db, Conversation, org_id),
            "reports": _count(db, Report, org_id),
        }
        for connection in list(db.scalars(select(Connection).where(Connection.org_id == org_id))):
            connections.remove_connection(db, rt, connection)
        for report in db.scalars(select(Report).where(Report.org_id == org_id)):
            for key in (report.pdf_key, report.xlsx_key):
                if key:
                    rt.objects.delete(key)
        db.commit()
    # The workspace row itself is deleted unscoped (the tenant role may not delete organisations); the database
    # cascades to every table with its org_id.
    with rt.session_factory() as unscoped:
        org = unscoped.get(Organization, org_id)
        if org is not None:
            unscoped.delete(org)
            unscoped.flush()
        anonymised = anonymise_activity(unscoped, org_id)
        audit.write(
            unscoped,
            action="org.deleted",
            at=now,
            meta=NO_META,
            org_id=org_id,
            target_type="organization",
            target_id=org_id,
            details={
                "requested_at": requested.isoformat(),
                **counts,
                "activity_entries_anonymised": anonymised,
            },
        )
        unscoped.commit()
    logger.info("deleted a workspace after its grace period")


def anonymise_activity(db: DbSession, org_id: uuid.UUID) -> int:
    """Keep a deleted workspace's earlier activity-log entries, without who, where or what (D124, the final D85):
    the action and its time stay, so the record of what happened survives; the person, IP, browser, target and
    details go. Only possible once the workspace row is gone (the database trigger checks, migration 0018)."""
    db.execute(text("SET LOCAL app.audit_anonymise = 'on'"))
    result = db.execute(
        text(
            "UPDATE audit_log SET user_id = NULL, ip = NULL, user_agent = NULL, target_id = NULL, "
            "details = CAST(:anonymised AS jsonb) "
            "WHERE org_id = :org_id AND details <> CAST(:anonymised AS jsonb)"
        ),
        {"org_id": org_id, "anonymised": ANONYMISED_DETAILS},
    )
    db.execute(text("SET LOCAL app.audit_anonymise = 'off'"))
    return int(getattr(result, "rowcount", 0) or 0)


def _count(db: DbSession, model: Any, org_id: uuid.UUID) -> int:
    found = db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))
    return int(found or 0)
