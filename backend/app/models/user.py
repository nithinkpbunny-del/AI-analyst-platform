"""Users are global: one person, one login, possibly several organisations (via memberships)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, true
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    # Nullable: Google-only accounts (M3) have no password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
