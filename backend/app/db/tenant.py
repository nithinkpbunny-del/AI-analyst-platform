"""Tenant scoping for database sessions (BUILD_SPEC 6.2, P3; DECISIONS D34).

``scope_to_org(db, org_id)`` marks a session as belonging to one organisation. From then on, **every** transaction
it begins runs as the restricted ``app_tenant`` role with ``app.org_id`` set (both ``SET LOCAL``, so they end with
the transaction and never leak to the next user of a pooled connection). Postgres Row-Level Security then hides
and refuses rows of every other organisation, even if a query forgets its ``org_id`` filter.

Services commit more than once per request, so the settings are applied on each ``after_begin``, not once.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction

TENANT_ROLE = "app_tenant"
_ORG_KEY = "tenant_org_id"


def scope_to_org(db: Session, org_id: uuid.UUID) -> None:
    """Scope all later transactions of ``db`` to ``org_id``.

    A transaction already open (e.g. a read after sign-in checks) is committed first so the next one starts
    scoped. Commit, not rollback: rollback would expire loaded objects and reload them under the new scope.
    """
    if db.in_transaction():
        db.commit()
    db.info[_ORG_KEY] = org_id


def scoped_org(db: Session) -> uuid.UUID | None:
    value = db.info.get(_ORG_KEY)
    return value if isinstance(value, uuid.UUID) else None


@event.listens_for(Session, "after_begin")
def _apply_tenant_scope(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    org_id = scoped_org(session)
    if org_id is None:
        return
    connection.exec_driver_sql(f"SET LOCAL ROLE {TENANT_ROLE}")
    connection.execute(
        text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_id)}
    )
