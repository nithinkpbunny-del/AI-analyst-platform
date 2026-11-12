"""The business dictionary's versions (BUILD_SPEC 9, R13; M7).

One row per version of a workspace's dictionary. ``content`` is the whole dictionary as JSON (see
``app.dictionary.model``). A workspace has at most one **draft** (the system's proposal or an Owner's unfinished
edits) and any number of **confirmed** versions; the newest confirmed one is the dictionary answers use. Older
confirmed versions become ``superseded`` and are kept as history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

DictionaryStatus = Literal["draft", "confirmed", "superseded"]
DictionaryOrigin = Literal["initial", "schema_change", "replacement", "redraft"]


class DictionaryVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "dictionary_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'confirmed', 'superseded')", name="status"),
        CheckConstraint(
            "origin IN ('initial', 'schema_change', 'replacement', 'redraft')", name="origin"
        ),
        Index("uq_dictionary_versions_org_id_version", "org_id", "version", unique=True),
        # At most one draft per workspace.
        Index(
            "uq_dictionary_versions_one_draft",
            "org_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # What changed against the version this one was drafted from (new/missing columns, new tables, ...).
    diff_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # Hash of the profiled structure the draft was made from: an unchanged structure needs no new draft.
    profile_hash: Mapped[str | None] = mapped_column(String(64))
    # Hash of what was sent to the model, and its answer: the same input is never paid for twice.
    ai_input_hash: Mapped[str | None] = mapped_column(String(64))
    ai_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # How the draft was made: AI status, suggestions that were dropped and why.
    notes_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
