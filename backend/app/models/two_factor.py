"""Two-step verification, sign-in challenges and known devices (BUILD_SPEC 5 and 6.1, M3).

None of these tables are tenant-owned: they belong to a person (users are global), so they carry no ``org_id``
and need no RLS policy. Secrets are never stored in clear: the TOTP secret is envelope-encrypted, codes and
tokens are stored as keyed hashes or SHA-256.
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
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

TwoFactorMethod = Literal["totp", "email"]
TWO_FACTOR_METHODS: tuple[TwoFactorMethod, ...] = ("totp", "email")
ChallengeKind = Literal["two_factor", "two_factor_setup", "google_handoff", "google_signup"]
CodePurpose = Literal["login", "setup", "step_up"]


class TwoFactor(TimestampMixin, Base):
    """One row per person. ``enabled_at`` is set once a method is confirmed.

    ``pending_*`` hold a method being set up (or changed) until the person proves it works with a code; the
    active method keeps working meanwhile.
    """

    __tablename__ = "two_factor"
    __table_args__ = (
        CheckConstraint("method IS NULL OR method IN ('totp', 'email')", name="method"),
        CheckConstraint(
            "pending_method IS NULL OR pending_method IN ('totp', 'email')", name="pending_method"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    method: Mapped[str | None] = mapped_column(String(8))
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # The last TOTP time step accepted: a code can be used once, and never an older one.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    recovery_codes_hash: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list
    )
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_method: Mapped[str | None] = mapped_column(String(8))
    pending_totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)


class TwoFactorCode(IdMixin, TimestampMixin, Base):
    """A 6-digit code sent by email: 10 minutes, single use, 5 attempts (BUILD_SPEC 6.1)."""

    __tablename__ = "two_factor_codes"
    __table_args__ = (CheckConstraint("purpose IN ('login', 'setup', 'step_up')", name="purpose"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginChallenge(IdMixin, TimestampMixin, Base):
    """A sign-in that is half done. The browser holds the opaque token; only its hash is stored.

    * ``two_factor``: the first factor (password or Google) passed; a code is needed.
    * ``two_factor_setup``: as above, but the person must set up 2FA first (Owners).
    * ``google_handoff``: Google confirmed the person; the web app exchanges the token for the next step.
    * ``google_signup``: Google confirmed a new person; the web app collects the company name and consent.
    """

    __tablename__ = "login_challenges"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('two_factor', 'two_factor_setup', 'google_handoff', 'google_signup')",
            name="kind",
        ),
        CheckConstraint("auth_method IN ('password', 'google')", name="auth_method"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # google_signup only: the verified Google identity (sub, email, name). No secrets.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    codes_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_code_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnownDevice(IdMixin, TimestampMixin, Base):
    """Browsers a person has signed in from. A sign-in from an unknown one sends a "New sign-in" email."""

    __tablename__ = "known_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
