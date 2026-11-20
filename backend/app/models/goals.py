"""Goals and the pinned dashboard (BUILD_SPEC 5, X7, X8; M15).

* A **goal** is a target for one metric of the business dictionary over a period ("revenue at least ₹50 L in
  November"), optionally for part of the business (``filters``: column -> value). Goals belong to the workspace:
  Owners and Analysts set them, everyone sees them, but each person only sees goals whose metric they may see
  (connection access, BUILD_SPEC 6.2). Progress is computed by code when it is looked at (``app.goals``).
* A **dashboard tile** is an answer someone pinned. ``spec`` keeps what to re-run: the answer's tool calls (the
  exact analytics, never the AI), which result holds the key figure and which charts to draw. A tile re-runs with
  the permissions of whoever is looking at it. Its owner may show it on everyone's dashboard (``shared``); people
  whose access does not cover its tables do not see it.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

REFRESH_CHOICES = (15, 60, 240, 1440)


class Goal(IdMixin, TimestampMixin, Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint("direction IN ('at_least', 'at_most')", name="direction"),
        CheckConstraint("period_end >= period_start", name="period"),
        Index("ix_goals_org_id_period", "org_id", "period_end"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(String(200))
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="at_least")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class DashboardTile(IdMixin, TimestampMixin, Base):
    __tablename__ = "dashboard_tiles"
    __table_args__ = (
        CheckConstraint("refresh_minutes BETWEEN 5 AND 1440", name="refresh_minutes"),
        Index("ix_dashboard_tiles_org_id_user_id_position", "org_id", "user_id", "position"),
        Index("ix_dashboard_tiles_org_id_shared", "org_id", "shared"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The answer it was pinned from (kept for "Open the answer"; the tile works without it).
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
