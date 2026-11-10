"""Connections and their data (BUILD_SPEC 5, 8; M6).

* ``connections``: one data source of a workspace (an uploaded file, a Google account's spreadsheets, ...).
  ``config`` holds non-secret settings only; credentials live in ``secrets`` (``secret_ref``).
* ``connection_grants``: which connections a person may see when their membership is set to "selected"
  connections (DECISIONS D50). Owners always see every connection.
* ``datasets``: one logical table per connection (a sheet, a tab, later an API endpoint or a database table).
* ``snapshots``: short-lived Parquet copies of a dataset in object storage (the reuse cache, BUILD_SPEC 6.6).
* ``file_versions``: every uploaded file. Re-uploading creates a new version. The version in use is recorded in
  ``connections.config["file_version_id"]``; a new version only replaces it when it has data and keeps the same
  sheets, columns and types, or when an Owner chooses it (DECISIONS D58).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

ConnectionType = Literal["file", "google_sheets"]
CONNECTION_TYPES: tuple[ConnectionType, ...] = ("file", "google_sheets")
ConnectionStatus = Literal["active", "error", "needs_reauth", "disabled"]
SyncState = Literal["idle", "queued", "running"]
# needs_confirmation: readable, but it differs from the version in use (sheets or columns gone, types changed),
# so it waits for an Owner to choose it (DECISIONS D58).
FileVersionStatus = Literal["processing", "ready", "failed", "needs_confirmation"]


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class Connection(IdMixin, TimestampMixin, Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("type IN ('file', 'google_sheets')", name="type"),
        CheckConstraint("status IN ('active', 'error', 'needs_reauth', 'disabled')", name="status"),
        CheckConstraint("sync_state IN ('idle', 'queued', 'running')", name="sync_state"),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # NO ACTION: a secret still in use cannot be deleted; deleting the workspace cascades into both tables.
    secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secrets.id")
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Plain language for people ("Google stopped sharing data. ..."); never raw provider payloads.
    last_error: Mapped[str | None] = mapped_column(Text)
    # Background reading of the source (DECISIONS D52). "queued"/"running" show progress in the web app.
    sync_state: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    sync_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ConnectionGrant(IdMixin, TimestampMixin, Base):
    __tablename__ = "connection_grants"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "user_id", name="uq_connection_grants_connection_id_user_id"
        ),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Dataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_ref", name="uq_datasets_connection_id_external_ref"
        ),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    # Where the data lives in the source: a sheet name, "<spreadsheet id>/<tab id>", a table, an endpoint.
    external_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Order in the source (sheet or tab order), for display.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Columns with types and units as detected on the last read (app.connectors.tabular.TableSchema).
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The data-quality findings of the last read (app.data.quality), shown before any snapshot is needed.
    quality_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    last_error: Mapped[str | None] = mapped_column(Text)


class Snapshot(IdMixin, TimestampMixin, Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_dataset_id_fetched_at", "dataset_id", "fetched_at"),
        Index("ix_snapshots_expires_at", "expires_at"),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    # When the rows were read from the source.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the source last confirmed these rows are still current (same version), so freshness is honest.
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Incremental fetches continue from here (e.g. the highest updated_at seen). NULL: full fetches only.
    cursor: Mapped[str | None] = mapped_column(String(200))
    # The source's own version of the data (a file version id, a Drive revision), when it has one.
    source_version: Mapped[str | None] = mapped_column(String(200))
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quality_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FileVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "file_versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "version", name="uq_file_versions_connection_id_version"),
        CheckConstraint(
            "status IN ('processing', 'ready', 'failed', 'needs_confirmation')", name="status"
        ),
    )

    org_id: Mapped[uuid.UUID] = _org_fk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    error: Mapped[str | None] = mapped_column(Text)
    # How this version differs from the one in use, when it waits for confirmation (kind + details).
    review_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
