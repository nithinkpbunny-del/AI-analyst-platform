"""Billing events (BUILD_SPEC 5, R7, M18; DECISIONS D117): the hooks a payment provider will use later.

No money moves here. A row records something billing will care about, once: a plan change, a usage warning (80% of
the Managed plan's monthly questions), the limit being reached, or a month's invoice-ready usage summary. ``provider``
names the billing provider that was offered the event (today only the ``stand-in``, which sends nothing) and
``delivery`` says what happened (``not_sent`` for the stand-in). Workspace requests can read and add rows, never
change or remove them (grants in migration 0017).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

BillingEventKind = Literal["plan_changed", "usage_warning", "usage_limit_reached", "usage_summary"]
BILLING_EVENT_KINDS: tuple[BillingEventKind, ...] = (
    "plan_changed",
    "usage_warning",
    "usage_limit_reached",
    "usage_summary",
)
Delivery = Literal["not_sent", "sent", "failed"]


class BillingEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "billing_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('plan_changed', 'usage_warning', 'usage_limit_reached', 'usage_summary')",
            name="kind",
        ),
        CheckConstraint("delivery IN ('not_sent', 'sent', 'failed')", name="delivery"),
        # Warnings and summaries happen once per workspace per month. Plan changes have no period (NULLs are
        # distinct in a unique index), so a workspace can change plan as often as it likes.
        UniqueConstraint("org_id", "kind", "period", name="uq_billing_events_org_id_kind_period"),
        Index("ix_billing_events_org_id_created_at", "org_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # The calendar month in the workspace's time zone ("2026-11"); None for plan changes.
    period: Mapped[str | None] = mapped_column(String(7))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery: Mapped[str] = mapped_column(String(12), nullable=False, default="not_sent")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
