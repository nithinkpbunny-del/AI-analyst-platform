"""Organisations (tenants) and the memberships that give users a role in them."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

Role = Literal["owner", "analyst", "viewer"]
ROLES: tuple[Role, ...] = ("owner", "analyst", "viewer")
MembershipStatus = Literal["active", "removed"]
# Which connections a non-Owner sees (DECISIONS D50): every one, or only those in ``connection_grants``.
ConnectionAccess = Literal["all", "selected"]


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("plan IN ('managed', 'byok')", name="plan"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    industry_template: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="managed")
    monthly_question_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    # R9: proactive ₹ impact only when >= 5% of the monthly metric or >= ₹1 lakh.
    impact_threshold_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5.00")
    )
    impact_threshold_inr: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("100000.00")
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(IdMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_memberships_org_id_user_id"),
        CheckConstraint("role IN ('owner', 'analyst', 'viewer')", name="role"),
        CheckConstraint("status IN ('active', 'removed')", name="status"),
        CheckConstraint("connection_access IN ('all', 'selected')", name="connection_access"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    connection_access: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all", server_default="all"
    )
