"""What the agent may see for the person asking (BUILD_SPEC 6.2 "Agent permissions", P6; DECISIONS D50).

``AccessContext`` is built from the database for a person in a workspace (the background job has no request), with
the same rules as the Connections screen (``app.connectors.access``): Owners see every connection; others see
all connections or only the ones granted to them. Paused connections are left out ("questions do not use it").

``visible_dictionary`` cuts the business dictionary down to that: tables of connections the person may not see,
tables whose data no longer exists, and every metric and join that touches them are removed, and what was removed
is reported so the answer can say so (D64). Every tool works on this dictionary only, so the agent cannot reach a
table it may not see.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.dictionary import validate
from app.dictionary.model import Dictionary
from app.models import Connection, ConnectionGrant, Dataset, Membership, Organization


class NoAccess(Exception):
    """The person is not an active member of the workspace (removed meanwhile)."""


@dataclass(frozen=True)
class DatasetRef:
    dataset_id: uuid.UUID
    connection_id: uuid.UUID
    connection_name: str
    connection_type: str
    name: str
    row_count: int | None
    profile: dict[str, object]
    # The source's own name for the table ("public.orders") and, for databases, "postgresql" or "mysql" (M14).
    external_ref: str = ""
    engine: str | None = None


@dataclass(frozen=True)
class AccessContext:
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    org_name: str
    industry: str | None
    timezone: str
    impact_threshold_pct: float
    impact_threshold_inr: float
    connection_ids: frozenset[uuid.UUID]
    datasets: dict[uuid.UUID, DatasetRef] = field(default_factory=dict)
    # Ids of every dataset of the workspace (visible or not): tells "removed" apart from "not yours to see".
    existing_dataset_ids: frozenset[uuid.UUID] = frozenset()


def access_context(db: DbSession, org_id: uuid.UUID, user_id: uuid.UUID) -> AccessContext:
    org = db.get(Organization, org_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if org is None or membership is None:
        raise NoAccess(str(user_id))
    query = select(Connection).where(Connection.org_id == org_id, Connection.status != "disabled")
    if membership.role != "owner" and membership.connection_access != "all":
        granted = select(ConnectionGrant.connection_id).where(
            ConnectionGrant.org_id == org_id, ConnectionGrant.user_id == user_id
        )
        query = query.where(Connection.id.in_(granted))
    connections = {c.id: c for c in db.scalars(query)}
    datasets: dict[uuid.UUID, DatasetRef] = {}
    if connections:
        for dataset in db.scalars(
            select(Dataset).where(
                Dataset.org_id == org_id, Dataset.connection_id.in_(list(connections))
            )
        ):
            connection = connections[dataset.connection_id]
            datasets[dataset.id] = DatasetRef(
                dataset_id=dataset.id,
                connection_id=connection.id,
                connection_name=connection.name,
                connection_type=connection.type,
                name=dataset.name,
                row_count=dataset.row_count_estimate,
                profile=dict(dataset.profile_json or {}),
                external_ref=dataset.external_ref,
                engine=str(connection.config.get("engine"))
                if connection.type == "sql_database"
                else None,
            )
    existing = frozenset(db.scalars(select(Dataset.id).where(Dataset.org_id == org_id)))
    return AccessContext(
        org_id=org_id,
        user_id=user_id,
        role=membership.role,
        org_name=org.name,
        industry=org.industry_template,
        timezone=org.timezone,
        impact_threshold_pct=float(org.impact_threshold_pct),
        impact_threshold_inr=float(org.impact_threshold_inr),
        connection_ids=frozenset(connections),
        datasets=datasets,
        existing_dataset_ids=existing,
    )


@dataclass
class VisibleDictionary:
    dictionary: Dictionary
    # Table key -> the dataset it reads.
    tables: dict[str, DatasetRef]
    # Metrics whose table no longer exists (removed source, D64). Metrics of tables the person may not see are
    # dropped without a word: their names are not theirs to know.
    unavailable_metrics: list[str] = field(default_factory=list)
    gone_tables: list[str] = field(default_factory=list)


def visible_dictionary(dictionary: Dictionary, access: AccessContext) -> VisibleDictionary:
    out = dictionary.model_copy(deep=True)
    tables: dict[str, DatasetRef] = {}
    gone: set[str] = set()
    for key, entry in list(out.datasets.items()):
        dataset_id = _uuid(entry.dataset_id)
        ref = access.datasets.get(dataset_id) if dataset_id else None
        if ref is None:
            if dataset_id is None or dataset_id not in access.existing_dataset_ids:
                gone.add(key)
            del out.datasets[key]
            continue
        tables[key] = ref
    unavailable: list[str] = []
    for name in list(out.metrics):
        table = dictionary.metrics[name].dataset
        with contextlib.suppress(validate.MetricError):
            _, table = validate.expand(name, dictionary)
        if table is not None and table in out.datasets:
            continue
        if table in gone or table is None:
            unavailable.append(name)
        del out.metrics[name]
    out.relationships = [
        r
        for r in out.relationships
        if r.left.partition(".")[0] in out.datasets and r.right.partition(".")[0] in out.datasets
    ]
    for entry in out.datasets.values():
        for column in entry.columns.values():
            column.joins = [j for j in column.joins if j.partition(".")[0] in out.datasets]
    return VisibleDictionary(out, tables, sorted(unavailable), sorted(gone))


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None
