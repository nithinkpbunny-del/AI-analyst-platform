"""Secrets, BYOK keys and AI usage (BUILD_SPEC 5, 6.4, 7.8; M5).

* ``secrets``: envelope-encrypted values (``app.security.secrets``). Never returned by any API.
* ``llm_keys``: a workspace's own AI key (BYOK). Only ``last4`` and the provider are ever shown.
* ``usage_events``: one row per LLM call. ``run_id`` groups the calls of one question, long task or briefing, so
  "questions this month" counts runs, not calls. Append-only for workspace requests (grants in migration 0005).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

Provider = Literal["gemini", "claude"]
PROVIDERS: tuple[Provider, ...] = ("gemini", "claude")
UsageKind = Literal["question", "task", "briefing", "check"]
KeySource = Literal["managed", "byok"]
# Runs of these kinds count towards the Managed plan's monthly question limit (DECISIONS D42).
LIMITED_KINDS: tuple[UsageKind, ...] = ("question", "task")


class Secret(IdMixin, TimestampMixin, Base):
    __tablename__ = "secrets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # What the value is for (e.g. "llm_key"); bound into the encryption context with org_id and id.
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class LlmKey(IdMixin, TimestampMixin, Base):
    __tablename__ = "llm_keys"
    __table_args__ = (CheckConstraint("provider IN ('gemini', 'claude')", name="provider"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one key per workspace
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # NO ACTION (checked at the end of the statement), so deleting a workspace may cascade into both tables in
    # any order, while deleting a secret that is still in use is refused.
    secret_ref: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secrets.id"), nullable=False
    )
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class UsageEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint("kind IN ('question', 'task', 'briefing', 'check')", name="kind"),
        CheckConstraint("key_source IN ('managed', 'byok')", name="key_source"),
        CheckConstraint("provider IN ('gemini', 'claude')", name="provider"),
        Index("ix_usage_events_org_id_created_at", "org_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL when the model's price is unknown (shown as "not known" rather than a made-up number).
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    key_source: Mapped[str] = mapped_column(String(16), nullable=False)
