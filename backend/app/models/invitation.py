"""Invitations to join a workspace (BUILD_SPEC 5, R15). Owners invite by email with a role.

The link token is single use and only its SHA-256 is stored. An invitation is *pending* while it is not
accepted, not revoked and not expired. Re-sending replaces the token (the old link stops working).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

InviteRole = Literal["analyst", "viewer"]
INVITE_ROLES: tuple[InviteRole, ...] = ("analyst", "viewer")


class Invitation(IdMixin, TimestampMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("role IN ('analyst', 'viewer')", name="role"),
        # At most one open invitation per address and workspace (expired ones are revoked when re-inviting).
        Index(
            "uq_invitations_open_email",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
