"""Two-step verification for one person (BUILD_SPEC 6.1, R15): set up, verify, recovery codes, turn off.

Used by the sign-in flow (``AuthService``) and by the signed-in settings routes. It changes rows in the caller's
database session and queues emails in the caller's outbox; the caller commits.

Limits: a code entered wrongly 10 times within 15 minutes (any challenge, any purpose) locks code entry for that
person, doubling like sign-in lockouts. Each email code allows 5 attempts and lasts 10 minutes. At most
``CODE_EMAILS_PER_HOUR`` code emails go to one person per hour.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.auth import codes
from app.auth.code_delivery import CodeDelivery, EmailCodeDelivery
from app.auth.devices import mask_email
from app.auth.ratelimit import FixedWindowLimiter, LoginThrottle
from app.config import Settings
from app.email.sender import OutgoingEmail
from app.models import Membership, TwoFactor, TwoFactorCode, User
from app.models.two_factor import CodePurpose, TwoFactorMethod
from app.security import crypto

logger = logging.getLogger(__name__)

EMAIL_CODE_MINUTES = 10
EMAIL_CODE_ATTEMPTS = 5
CODE_EMAILS_PER_HOUR = 10
CODE_FAILURES_BEFORE_LOCK = 10
CODE_FAILURE_WINDOW = timedelta(minutes=15)

UsedFactor = Literal["totp", "email", "recovery"]

_WRONG_CODE = "That code is not right. Check it and try again."


@dataclass(frozen=True)
class TwoFactorSummary:
    enabled: bool
    method: str | None
    required: bool
    recovery_codes_remaining: int


@dataclass(frozen=True)
class SetupStarted:
    method: TwoFactorMethod
    totp: codes.TotpProvisioning | None = None
    email_hint: str | None = None


def wrong_code(fields_key: str = "code") -> ApiError:
    return ApiError(400, "invalid_code", _WRONG_CODE, fields={fields_key: _WRONG_CODE})


@dataclass
class TwoFactorManager:
    db: DbSession
    settings: Settings
    clock: Callable[[], datetime]
    failures: LoginThrottle
    email_limiter: FixedWindowLimiter
    outbox: list[OutgoingEmail] = field(default_factory=list)
    # How codes reach the person. Email for now; an SMS channel is a later phase (see code_delivery.py).
    delivery: CodeDelivery = field(init=False)

    def __post_init__(self) -> None:
        self.delivery = EmailCodeDelivery(self.outbox)

    # ------------------------------------------------------------------ state
    def row(self, user_id: uuid.UUID, *, lock: bool = False) -> TwoFactor | None:
        return self.db.get(TwoFactor, user_id, with_for_update=lock)

    def is_required(self, user_id: uuid.UUID) -> bool:
        """Mandatory for anyone who is an Owner of at least one workspace."""
        return bool(
            self.db.scalar(
                select(
                    exists().where(
                        Membership.user_id == user_id,
                        Membership.role == "owner",
                        Membership.status == "active",
                    )
                )
            )
        )

    def summary(self, user_id: uuid.UUID) -> TwoFactorSummary:
        row = self.row(user_id)
        enabled = bool(row and row.enabled_at)
        return TwoFactorSummary(
            enabled=enabled,
            method=row.method if row and enabled else None,
            required=self.is_required(user_id),
            recovery_codes_remaining=len(row.recovery_codes_hash) if row and enabled else 0,
        )

    def _master_key(self) -> str:
        return self.settings.master_key.get_secret_value()

    def _context(self, user_id: uuid.UUID) -> str:
        return f"two_factor.totp:{user_id}"

    def _secret(self, blob: bytes, user_id: uuid.UUID) -> str | None:
        """The TOTP secret, or ``None`` (fail closed) if it cannot be decrypted: wrong key or a copied row."""
        try:
            return crypto.unseal(
                blob, master_key=self._master_key(), context=self._context(user_id)
            ).decode()
        except crypto.CryptoError:
            logger.error("a TOTP secret could not be decrypted; the code is refused")
            return None

    def _hash(self, user_id: uuid.UUID, value: str, purpose: str) -> str:
        return crypto.keyed_hash(
            f"{user_id}:{value}", master_key=self._master_key(), purpose=purpose
        )

    # ------------------------------------------------------------------ throttling
    def check_not_locked(self, user_id: uuid.UUID) -> None:
        lockout = self.failures.check(str(user_id), "-", self.clock())
        if lockout:
            minutes = max(1, round(lockout.retry_after_seconds / 60))
            raise ApiError(
                429,
                "too_many_attempts",
                f"Too many wrong codes. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                headers={"Retry-After": str(lockout.retry_after_seconds)},
            )

    def record_failure(self, user_id: uuid.UUID) -> None:
        self.failures.record_failure(str(user_id), "-", self.clock())

    # ------------------------------------------------------------------ email codes
    def send_email_code(self, user: User, purpose: CodePurpose) -> bool:
        """Send a fresh code (older unused codes for the same purpose stop working). False when rate-limited."""
        now = self.clock()
        if not self.email_limiter.hit(str(user.id), now):
            return False
        self.db.execute(
            update(TwoFactorCode)
            .where(
                TwoFactorCode.user_id == user.id,
                TwoFactorCode.purpose == purpose,
                TwoFactorCode.used_at.is_(None),
            )
            .values(used_at=now)
        )
        code = codes.new_email_code()
        self.db.add(
            TwoFactorCode(
                user_id=user.id,
                purpose=purpose,
                code_hash=self._hash(user.id, f"{purpose}:{code}", "email-code"),
                expires_at=now + timedelta(minutes=EMAIL_CODE_MINUTES),
            )
        )
        self.delivery.deliver(user, code, purpose=purpose, minutes=EMAIL_CODE_MINUTES)
        return True

    def _check_email_code(self, user_id: uuid.UUID, purpose: CodePurpose, code: str) -> bool:
        now = self.clock()
        row = self.db.scalar(
            select(TwoFactorCode)
            .where(
                TwoFactorCode.user_id == user_id,
                TwoFactorCode.purpose == purpose,
                TwoFactorCode.used_at.is_(None),
            )
            .order_by(TwoFactorCode.created_at.desc(), TwoFactorCode.id.desc())
            .limit(1)
            .with_for_update()
        )
        if row is None or row.expires_at <= now or row.attempts >= EMAIL_CODE_ATTEMPTS:
            return False
        expected = self._hash(user_id, f"{purpose}:{code}", "email-code")
        if crypto.hmac_equal(row.code_hash, expected):
            row.used_at = now
            return True
        row.attempts += 1
        if row.attempts >= EMAIL_CODE_ATTEMPTS:
            row.used_at = now  # spent: ask for a new one
        return False

    # ------------------------------------------------------------------ verification of the active method
    def verify(self, user: User, code: str, purpose: CodePurpose) -> UsedFactor | None:
        """Check a code against the person's active method or their recovery codes. Consumes what it uses."""
        row = self.row(user.id, lock=True)
        if row is None or row.enabled_at is None:
            return None
        parsed = codes.classify(code)
        if parsed is None:
            return None
        kind, value = parsed
        if kind == "recovery":
            hashed = self._hash(user.id, value, "recovery-code")
            remaining = [h for h in row.recovery_codes_hash if not crypto.hmac_equal(h, hashed)]
            if len(remaining) == len(row.recovery_codes_hash):
                return None
            row.recovery_codes_hash = remaining
            return "recovery"
        if row.method == "totp" and row.totp_secret_enc:
            secret = self._secret(row.totp_secret_enc, user.id)
            step = (
                codes.totp_step(secret, value, self.clock(), row.totp_last_step) if secret else None
            )
            if step is None:
                return None
            row.totp_last_step = step
            return "totp"
        if row.method == "email" and self._check_email_code(user.id, purpose, value):
            return "email"
        return None

    def verify_or_fail(self, user: User, code: str, purpose: CodePurpose) -> UsedFactor:
        """``verify`` with the per-person lockout applied. Raises 400 on a wrong code, 429 when locked."""
        self.check_not_locked(user.id)
        used = self.verify(user, code, purpose)
        if used is None:
            self.record_failure(user.id)
            raise wrong_code()
        self.failures.record_success(str(user.id), "-")
        return used

    # ------------------------------------------------------------------ set up / change / turn off
    def begin_setup(self, user: User, method: TwoFactorMethod) -> SetupStarted:
        """Start setting up ``method``. The active method (if any) keeps working until the new one is confirmed."""
        if not self._master_key():
            raise ApiError(
                503, "not_configured", "Two-step verification is not configured on this server."
            )
        row = self.row(user.id, lock=True)
        if row is None:
            row = TwoFactor(user_id=user.id, recovery_codes_hash=[])
            self.db.add(row)
        row.pending_method = method
        if method == "totp":
            secret = codes.new_totp_secret()
            row.pending_totp_secret_enc = crypto.seal(
                secret.encode(), master_key=self._master_key(), context=self._context(user.id)
            )
            return SetupStarted(method=method, totp=codes.totp_provisioning(secret, user.email))
        row.pending_totp_secret_enc = None
        if not self.send_email_code(user, "setup"):
            raise ApiError(
                429,
                "too_many_requests",
                "Too many codes were sent. Wait a few minutes and try again.",
            )
        return SetupStarted(method=method, email_hint=mask_email(user.email))

    def resend_setup_code(self, user: User) -> bool:
        row = self.row(user.id)
        if row is None or row.pending_method != "email":
            raise ApiError(400, "no_setup", "Start setting up two-step verification first.")
        return self.send_email_code(user, "setup")

    def confirm_setup(self, user: User, code: str) -> tuple[TwoFactorMethod, list[str], bool]:
        """Prove the pending method works; make it active. Returns (method, new recovery codes, was_change)."""
        self.check_not_locked(user.id)
        row = self.row(user.id, lock=True)
        if row is None or row.pending_method is None:
            raise ApiError(400, "no_setup", "Start setting up two-step verification first.")
        parsed = codes.classify(code)
        value = parsed[1] if parsed and parsed[0] == "otp" else None
        ok = False
        step: int | None = None
        if value and row.pending_method == "totp" and row.pending_totp_secret_enc:
            pending = self._secret(row.pending_totp_secret_enc, user.id)
            step = codes.totp_step(pending, value, self.clock(), None) if pending else None
            ok = step is not None
        elif value and row.pending_method == "email":
            ok = self._check_email_code(user.id, "setup", value)
        if not ok:
            self.record_failure(user.id)
            raise wrong_code()
        self.failures.record_success(str(user.id), "-")

        was_change = row.enabled_at is not None
        method: TwoFactorMethod = "totp" if row.pending_method == "totp" else "email"
        row.method = method
        row.totp_secret_enc = row.pending_totp_secret_enc if method == "totp" else None
        row.totp_last_step = step if method == "totp" else None
        row.pending_method = None
        row.pending_totp_secret_enc = None
        row.enabled_at = self.clock()
        recovery = self._new_recovery_codes(row)
        return method, recovery, was_change

    def _new_recovery_codes(self, row: TwoFactor) -> list[str]:
        fresh = codes.new_recovery_codes()
        row.recovery_codes_hash = [
            self._hash(row.user_id, code.replace("-", ""), "recovery-code") for code in fresh
        ]
        return fresh

    def regenerate_recovery_codes(self, user: User) -> list[str]:
        row = self.row(user.id, lock=True)
        if row is None or row.enabled_at is None:
            raise ApiError(400, "not_enabled", "Two-step verification is not on.")
        return self._new_recovery_codes(row)

    def disable(self, user: User) -> None:
        if self.is_required(user.id):
            raise ApiError(
                403,
                "two_factor_required",
                "Owners must keep two-step verification on. You can switch to another method instead.",
            )
        row = self.row(user.id, lock=True)
        if row is not None:
            self.db.delete(row)
