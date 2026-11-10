"""Which connections a person may see (BUILD_SPEC R15 "per-connection grants", 6.2; DECISIONS D50).

* Owners see every connection of their workspace.
* Everyone else follows their membership's ``connection_access``: ``all`` (the default) or ``selected``, which
  means only the connections in ``connection_grants`` for them, possibly none.

The agent (M8), briefings and dashboard tiles will use ``visible_connection_ids`` with the asking person's
context, so nobody reaches data through the agent that they could not see on the Connections screen.
A connection that is not visible answers 404, exactly like one that does not exist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.auth.service import AuthContext
from app.models import Connection, ConnectionGrant, Dataset, Membership


def membership_of(auth: AuthContext) -> Membership | None:
    return next(
        (
            m
            for m, org in auth.memberships
            if org.id == auth.organization.id and m.status == "active"
        ),
        None,
    )


def sees_everything(auth: AuthContext) -> bool:
    if auth.role == "owner":
        return True
    membership = membership_of(auth)
    return membership is not None and membership.connection_access == "all"


def visible_connections(auth: AuthContext) -> Select[Connection]:
    query = select(Connection).where(Connection.org_id == auth.organization.id)
    if not sees_everything(auth):
        granted = select(ConnectionGrant.connection_id).where(
            ConnectionGrant.org_id == auth.organization.id,
            ConnectionGrant.user_id == auth.user.id,
        )
        query = query.where(Connection.id.in_(granted))
    return query


def visible_connection_ids(db: DbSession, auth: AuthContext) -> set[uuid.UUID]:
    return set(db.scalars(visible_connections(auth).with_only_columns(Connection.id)))


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def visible_connection(
    db: DbSession, auth: AuthContext, connection_id: uuid.UUID, *, lock: bool = False
) -> Connection:
    query = visible_connections(auth).where(Connection.id == connection_id)
    found = db.scalar(query.with_for_update() if lock else query)
    if found is None:
        raise not_found()
    return found


def visible_dataset(
    db: DbSession, auth: AuthContext, connection_id: uuid.UUID, dataset_id: uuid.UUID
) -> tuple[Connection, Dataset]:
    connection = visible_connection(db, auth, connection_id)
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.connection_id == connection.id,
            Dataset.org_id == auth.organization.id,
        )
    )
    if dataset is None:
        raise not_found()
    return connection, dataset
