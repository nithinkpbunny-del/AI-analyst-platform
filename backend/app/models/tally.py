"""The Tally bridge (BUILD_SPEC 8.8, M16; DECISIONS D108-D113).

* ``tally_devices``: a Tally PC paired with a Tally connection. Only a SHA-256 hash of its token is kept. One active
  device per connection; unpairing (or pairing another PC) revokes the old one at once.
* ``tally_syncs``: one sync the bridge sends, with the batch numbers received so far (so a re-sent batch is
  ignored and "complete" can check that nothing is missing).
* ``tally_records``: the rows the bridge sent, as Tally's own text, keyed by Tally's GUID. A row sent again
  replaces its earlier copy, so nothing is ever duplicated. The Tally connector types them into tables
  (``app.connectors.tally``). They are the source copy of Tally data on the platform (Tally cannot be read from
  here), kept until the connection is removed (D110).

Each table has ``org_id`` and the ``tenant_isolation`` RLS policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _connection_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )


class TallyDevice(IdMixin, TimestampMixin, Base):
    __tablename__ = "tally_devices"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
        Index(
            "uq_tally_devices_one_active",
            "connection_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = _connection_fk()
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    bridge_version: Mapped[str | None] = mapped_column(String(32))
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paired_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the bridge said in its last heartbeat (Tally reachable, company, its last error code ...).
    status_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TallySync(IdMixin, TimestampMixin, Base):
    __tablename__ = "tally_syncs"
    __table_args__ = (
        UniqueConstraint("device_id", "sync_ref", name="uq_tally_syncs_device_id_sync_ref"),
        CheckConstraint("mode IN ('full', 'incremental')", name="mode"),
        CheckConstraint("status IN ('receiving', 'complete')", name="status"),
        Index("ix_tally_syncs_connection_id_created_at", "connection_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = _connection_fk()
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_devices.id", ondelete="CASCADE"), nullable=False
    )
    # The bridge's own id for the sync (it chooses it, so it can resume after a restart).
    sync_ref: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="receiving")
    company_guid: Mapped[str] = mapped_column(String(64), nullable=False)
    # {dataset: [batch numbers received]} and {dataset: rows received}
    batches_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    records_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TallyRecord(Base):
    __tablename__ = "tally_records"
    __table_args__ = (
        Index("ix_tally_records_parent", "connection_id", "dataset", "parent_key"),
        Index("ix_tally_records_sync", "connection_id", "dataset", "sync_id"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    dataset: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(220), primary_key=True)
    org_id: Mapped[uuid.UUID] = _org_fk()
    # A voucher line's voucher (its GUID); empty for vouchers and masters.
    parent_key: Mapped[str | None] = mapped_column(String(200))
    # Tally's text for each field, e.g. {"DATE": "20260701", "AMOUNT": "-1281.72"}.
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The ``tally_syncs.id`` that last sent this row (a full read removes rows it did not send).
    sync_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
