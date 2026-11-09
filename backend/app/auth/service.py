"""Authentication use cases (BUILD_SPEC 6.1, R15): sign-up, email verification, login, refresh rotation with
reuse detection, logout, idle/absolute timeouts, password reset, consent and activity-log entries.

Routes stay thin: they build an ``AuthService`` per request, call one method, then send the queued emails
from a background task. Every method commits its own transaction, so activity-log entries for failures are
kept even though the request then fails.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.audit import writer as audit
from app.audit.writer import RequestMeta
from app.auth import passwords
from app.auth.ratelimit import FixedWindowLimiter, LoginThrottle
from app.auth.tokens import (
    ExpiredAccessToken,
    InvalidAccessToken,
    decode_access_token,
    hash_token,
    issue_access_token,
    new_opaque_token,
)
from app.config import Settings
from app.email import templates
from app.email.sender import OutgoingEmail
from app.models import (
    Consent,
    Membership,
    Organization,
    RefreshToken,
    Session,
    User,
    UserToken,
)

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
TokenPurpose = Literal["verify_email", "reset_password"]

INVALID_CREDENTIALS = "Invalid email or password."
# Updating last_seen_at on every request would mean a write per call; within this interval one write is enough.
_LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=30)
EMAIL_LIMIT_PER_ADDRESS = 5  # per hour: stops someone flooding a person's inbox
EMAIL_LIMIT_PER_IP = 30  # per hour


@dataclass(frozen=True)
class IssuedSession:
    """What a successful login or refresh hands to the route: tokens plus who is signed in."""

    access_token: str
    access_expires_at: datetime
    refresh_token: str
    session: Session
    user: User
    organization: Organization
    role: str
    memberships: list[tuple[Membership, Organization]]


@dataclass(frozen=True)
class AuthContext:
    """The authenticated caller of a request."""

    user: User
    organization: Organization
    role: str
    session: Session
    memberships: list[tuple[Membership, Organization]]


@dataclass
class AuthService:
    db: DbSession
    settings: Settings
    clock: Clock
    login_throttle: LoginThrottle
    email_limiter: FixedWindowLimiter  # per recipient address
    ip_limiter: FixedWindowLimiter  # per caller IP, for every email-sending request
    meta: RequestMeta
    outbox: list[OutgoingEmail] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def _now(self) -> datetime:
        return self.clock()

    def _link(self, path: str, token: str | None = None) -> str:
        base = self.settings.app_base_url.rstrip("/")
        # The token goes in the URL fragment: browsers never send it to any server or put it in logs.
        return f"{base}{path}#token={token}" if token else f"{base}{path}"

    def _user_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email.strip()))

    def _active_memberships(self, user_id: uuid.UUID) -> list[tuple[Membership, Organization]]:
        rows = self.db.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user_id, Membership.status == "active")
            .order_by(Membership.created_at, Membership.id)
        ).all()
        return [(m, o) for m, o in rows]

    def _org_ids(self, user: User | None) -> list[uuid.UUID]:
        return [m.org_id for m, _ in self._active_memberships(user.id)] if user else []

    def _may_email(self, address: str) -> bool:
        now = self._now()
        if self.meta.ip and not self.ip_limiter.hit(self.meta.ip, now):
            raise ApiError(
                429, "too_many_requests", "Too many requests. Wait a few minutes and try again."
            )
        # Per address: silently skip, so the response stays identical (no account-existence signal).
        return self.email_limiter.hit(address.lower(), now)

    def _new_user_token(self, user: User, purpose: TokenPurpose, lifetime: timedelta) -> str:
        now = self._now()
        # Only the newest link of each kind works.
        self.db.execute(
            update(UserToken)
            .where(
                UserToken.user_id == user.id,
                UserToken.purpose == purpose,
                UserToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        token = new_opaque_token()
        self.db.add(
            UserToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_token(token),
                expires_at=now + lifetime,
            )
        )
        return token

    def _consume_user_token(
        self, token: str, purpose: TokenPurpose
    ) -> tuple[UserToken, User] | None:
        row = self.db.execute(
            select(UserToken, User)
            .join(User, User.id == UserToken.user_id)
            .where(UserToken.token_hash == hash_token(token), UserToken.purpose == purpose)
            .with_for_update(of=UserToken)
        ).first()
        if row is None:
            return None
        user_token, user = row
        if user_token.used_at is not None or user_token.expires_at <= self._now():
            return None
        user_token.used_at = self._now()
        return user_token, user

    def _queue_verification(self, user: User, org_name: str) -> None:
        token = self._new_user_token(
            user, "verify_email", timedelta(hours=self.settings.email_verification_hours)
        )
        self.outbox.append(
            templates.render(
                "verify_email",
                to=user.email,
                name=user.name,
                org_name=org_name,
                hours=self.settings.email_verification_hours,
                link=self._link("/verify-email", token),
            )
        )

    def _revoke_session(self, session: Session, reason: str) -> None:
        now = self._now()
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoke_reason = reason
        self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == session.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def _revoke_all_sessions(self, user_id: uuid.UUID, reason: str) -> int:
        sessions = self.db.scalars(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        ).all()
        for session in sessions:
            self._revoke_session(session, reason)
        return len(sessions)

    def _issue(
        self,
        session: Session,
        user: User,
        memberships: list[tuple[Membership, Organization]],
    ) -> IssuedSession:
        now = self._now()
        membership, organization = next((m, o) for m, o in memberships if o.id == session.org_id)
        refresh = new_opaque_token()
        self.db.add(
            RefreshToken(
                session_id=session.id,
                token_hash=hash_token(refresh),
                expires_at=session.expires_at,
            )
        )
        access, access_expires = issue_access_token(
            secret=self.settings.jwt_secret.get_secret_value(),
            user_id=user.id,
            org_id=organization.id,
            role=membership.role,
            session_id=session.id,
            now=now,
            lifetime=timedelta(minutes=self.settings.access_token_minutes),
        )
        return IssuedSession(
            access_token=access,
            access_expires_at=access_expires,
            refresh_token=refresh,
            session=session,
            user=user,
            organization=organization,
            role=membership.role,
            memberships=memberships,
        )

    # ------------------------------------------------------------------ sign-up and email verification
    def signup(self, *, name: str, email: str, password: str, organization_name: str) -> None:
        problem = passwords.password_problem(password, email=email)
        if problem:
            raise ApiError(422, "weak_password", problem, fields={"password": problem})
        now = self._now()
        # Hash before the existence check so both paths cost the same time.
        password_hash = passwords.hash_password(password)
        existing = self._user_by_email(email)
        if existing is not None:
            self._notify_existing_account(existing)
            return

        org = Organization(
            name=organization_name,
            monthly_question_limit=self.settings.managed_monthly_question_limit,
        )
        user = User(email=email.strip(), name=name, password_hash=password_hash)
        self.db.add_all([org, user])
        try:
            self.db.flush()
        except IntegrityError:  # someone signed up with the same email at the same moment
            self.db.rollback()
            existing = self._user_by_email(email)
            if existing is not None:
                self._notify_existing_account(existing)
            return
        self.db.add(Membership(org_id=org.id, user_id=user.id, role="owner", status="active"))
        self.db.add(
            Consent(
                user_id=user.id,
                org_id=org.id,
                policy_version=self.settings.policy_version,
                accepted_at=now,
            )
        )
        audit.write(
            self.db,
            action="auth.signup",
            at=now,
            meta=self.meta,
            org_id=org.id,
            user_id=user.id,
            target_type="organization",
            target_id=org.id,
        )
        audit.write(
            self.db,
            action="consent.accepted",
            at=now,
            meta=self.meta,
            org_id=org.id,
            user_id=user.id,
            details={"policy_version": self.settings.policy_version},
        )
        if self._may_email(user.email):
            self._queue_verification(user, org.name)
        self.db.commit()

    def _notify_existing_account(self, user: User) -> None:
        if self._may_email(user.email):
            self.outbox.append(
                templates.render(
                    "account_exists",
                    to=user.email,
                    link=self._link("/signin"),
                    reset_link=self._link("/forgot-password"),
                )
            )
        self.db.commit()

    def verify_email(self, token: str) -> None:
        consumed = self._consume_user_token(token, "verify_email")
        if consumed is None:
            self.db.rollback()
            raise ApiError(
                400,
                "invalid_link",
                "This link has expired or was already used. Ask for a new one.",
            )
        _, user = consumed
        now = self._now()
        if user.email_verified_at is None:
            user.email_verified_at = now
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.email_verified",
            at=now,
            meta=self.meta,
            user_id=user.id,
        )
        self.db.commit()

    def resend_verification(self, email: str) -> None:
        user = self._user_by_email(email)
        if user is None or user.email_verified_at is not None or not user.is_active:
            return
        if self._may_email(user.email):
            memberships = self._active_memberships(user.id)
            org_name = memberships[0][1].name if memberships else "your workspace"
            self._queue_verification(user, org_name)
        self.db.commit()

    # ------------------------------------------------------------------ login, refresh, logout
    def _login_failed(
        self, email: str, user: User | None, reason: str, *, count: bool = True
    ) -> None:
        now = self._now()
        lockout = (
            self.login_throttle.record_failure(email, self.meta.ip or "-", now) if count else None
        )
        details: dict[str, object] = {"reason": reason}
        if user is None:
            # Data minimisation: no clear-text email for unknown accounts.
            details["email_sha256"] = hash_token(email.strip().lower())
        if lockout:
            details["locked_for_seconds"] = lockout.retry_after_seconds
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.login.failure",
            at=now,
            meta=self.meta,
            user_id=user.id if user else None,
            details=details,
        )
        self.db.commit()

    def login(
        self, *, email: str, password: str, organization_id: uuid.UUID | None = None
    ) -> IssuedSession:
        now = self._now()
        ip = self.meta.ip or "-"
        lockout = self.login_throttle.check(email, ip, now)
        if lockout:
            self._login_failed(email, self._user_by_email(email), "rate_limited", count=False)
            minutes = max(1, round(lockout.retry_after_seconds / 60))
            raise ApiError(
                429,
                "too_many_attempts",
                f"Too many sign-in attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}, "
                "or reset your password.",
                headers={"Retry-After": str(lockout.retry_after_seconds)},
            )

        user = self._user_by_email(email)
        if user is None or user.password_hash is None:
            passwords.burn_verification_time(password)
            self._login_failed(email, user, "unknown_email" if user is None else "no_password")
            raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)
        if not passwords.verify_password(user.password_hash, password):
            self._login_failed(email, user, "bad_password")
            raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)
        if not user.is_active:
            self._login_failed(email, user, "inactive")
            raise ApiError(401, "invalid_credentials", INVALID_CREDENTIALS)
        if user.email_verified_at is None:
            self._login_failed(email, user, "email_not_verified", count=False)
            raise ApiError(
                403,
                "email_not_verified",
                "Confirm your email address first. We can send the link again.",
            )
        memberships = self._active_memberships(user.id)
        if not memberships:
            self._login_failed(email, user, "no_membership", count=False)
            raise ApiError(
                403,
                "no_organization",
                "You are not a member of any workspace. Ask an Owner to invite you.",
            )
        chosen = next((o for _, o in memberships if o.id == organization_id), memberships[0][1])

        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)
        self.login_throttle.record_success(email, ip)
        session = Session(
            user_id=user.id,
            org_id=chosen.id,
            ip=self.meta.ip,
            user_agent=self.meta.user_agent[:512] if self.meta.user_agent else None,
            last_seen_at=now,
            expires_at=now + timedelta(hours=self.settings.session_max_hours),
        )
        self.db.add(session)
        self.db.flush()
        issued = self._issue(session, user, memberships)
        audit.write(
            self.db,
            action="auth.login.success",
            at=now,
            meta=self.meta,
            org_id=chosen.id,
            user_id=user.id,
            target_type="session",
            target_id=session.id,
            details={"method": "password"},
        )
        self.db.commit()
        return issued

    def _end_session(self, session: Session, reason: str, code: str, message: str) -> ApiError:
        self._revoke_session(session, reason)
        audit.write(
            self.db,
            action=f"auth.session.{reason}",
            at=self._now(),
            meta=self.meta,
            org_id=session.org_id,
            user_id=session.user_id,
            target_type="session",
            target_id=session.id,
        )
        self.db.commit()
        return ApiError(401, code, message)

    def _check_session_alive(self, session: Session) -> None:
        """Raise (and revoke) when the session is past its absolute or idle limit."""
        now = self._now()
        if session.revoked_at is not None:
            raise ApiError(401, "session_revoked", "You were signed out. Sign in again.")
        if now >= session.expires_at:
            raise self._end_session(
                session,
                "expired",
                "session_expired",
                f"For your security you are signed out after {self.settings.session_max_hours} hours. "
                "Sign in again.",
            )
        if now - session.last_seen_at >= timedelta(minutes=self.settings.session_idle_minutes):
            raise self._end_session(
                session,
                "idle",
                "session_idle",
                f"You were signed out after {self.settings.session_idle_minutes} minutes without "
                "activity. Sign in again.",
            )

    def _session_people(
        self, session: Session
    ) -> tuple[User, list[tuple[Membership, Organization]]]:
        """The session's user and memberships; ends the session if the user or membership is gone."""
        user = self.db.get(User, session.user_id)
        memberships = self._active_memberships(session.user_id) if user else []
        if (
            user is None
            or not user.is_active
            or all(o.id != session.org_id for _, o in memberships)
        ):
            raise self._end_session(
                session,
                "access_removed",
                "session_revoked",
                "You no longer have access. Sign in again.",
            )
        return user, memberships

    def refresh(self, refresh_token: str | None) -> IssuedSession:
        if not refresh_token:
            raise ApiError(401, "not_signed_in", "Sign in to continue.")
        row = self.db.execute(
            select(RefreshToken, Session)
            .join(Session, Session.id == RefreshToken.session_id)
            .where(RefreshToken.token_hash == hash_token(refresh_token))
            .with_for_update()
        ).first()
        if row is None:
            self.db.rollback()
            raise ApiError(401, "not_signed_in", "Sign in to continue.")
        token, session = row
        now = self._now()
        if session.revoked_at is not None:
            self.db.rollback()
            raise ApiError(401, "session_revoked", "You were signed out. Sign in again.")
        if token.rotated_at is not None or token.revoked_at is not None:
            # Reuse of an old refresh token: it was copied. End the whole session (BUILD_SPEC 5, refresh_tokens).
            logger.warning("refresh token reuse detected; session revoked")
            self._revoke_session(session, "refresh_reuse")
            audit.write(
                self.db,
                action="auth.refresh_token_reused",
                at=now,
                meta=self.meta,
                org_id=session.org_id,
                user_id=session.user_id,
                target_type="session",
                target_id=session.id,
                details={"session_revoked": True},
            )
            self.db.commit()
            raise ApiError(
                401,
                "session_revoked",
                "For your safety you were signed out, because your sign-in was used from somewhere "
                "else. Sign in again.",
            )
        self._check_session_alive(session)
        user, memberships = self._session_people(session)
        token.rotated_at = now
        issued = self._issue(session, user, memberships)
        self.db.commit()
        return issued

    def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        row = self.db.execute(
            select(Session)
            .join(RefreshToken, RefreshToken.session_id == Session.id)
            .where(RefreshToken.token_hash == hash_token(refresh_token))
        ).first()
        if row is None:
            return
        session = row[0]
        if session.revoked_at is None:
            self._revoke_session(session, "logout")
            audit.write(
                self.db,
                action="auth.logout",
                at=self._now(),
                meta=self.meta,
                org_id=session.org_id,
                user_id=session.user_id,
                target_type="session",
                target_id=session.id,
            )
        self.db.commit()

    # ------------------------------------------------------------------ password reset
    def forgot_password(self, email: str) -> None:
        user = self._user_by_email(email)
        if user is None or not user.is_active:
            return
        if self._may_email(user.email):
            token = self._new_user_token(
                user, "reset_password", timedelta(minutes=self.settings.password_reset_minutes)
            )
            self.outbox.append(
                templates.render(
                    "reset_password",
                    to=user.email,
                    name=user.name,
                    minutes=self.settings.password_reset_minutes,
                    link=self._link("/reset-password", token),
                )
            )
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.password_reset.requested",
            at=self._now(),
            meta=self.meta,
            user_id=user.id,
        )
        self.db.commit()

    def reset_password(self, token: str, password: str) -> None:
        consumed = self._consume_user_token(token, "reset_password")
        if consumed is None:
            self.db.rollback()
            raise ApiError(
                400,
                "invalid_link",
                "This link has expired or was already used. Ask for a new one.",
            )
        _, user = consumed
        problem = passwords.password_problem(password, email=user.email)
        if problem:
            self.db.rollback()  # keep the link usable so the person can try a better password
            raise ApiError(422, "weak_password", problem, fields={"password": problem})
        now = self._now()
        user.password_hash = passwords.hash_password(password)
        if user.email_verified_at is None:
            user.email_verified_at = now  # the link proves they own the address
        revoked = self._revoke_all_sessions(user.id, "password_reset")
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.password_reset.completed",
            at=now,
            meta=self.meta,
            user_id=user.id,
            details={"sessions_revoked": revoked},
        )
        self.outbox.append(
            templates.render(
                "password_changed",
                to=user.email,
                name=user.name,
                link=self._link("/forgot-password"),
            )
        )
        self.db.commit()

    # ------------------------------------------------------------------ per-request authentication
    def authenticate(self, access_token: str) -> AuthContext:
        now = self._now()
        try:
            claims = decode_access_token(
                access_token, secret=self.settings.jwt_secret.get_secret_value(), now=now
            )
        except ExpiredAccessToken as error:
            raise ApiError(401, "token_expired", "Your sign-in needs refreshing.") from error
        except InvalidAccessToken as error:
            raise ApiError(401, "invalid_token", "Sign in to continue.") from error

        session = self.db.get(Session, claims.session_id, with_for_update=False)
        if session is None or session.user_id != claims.user_id or session.org_id != claims.org_id:
            raise ApiError(401, "invalid_token", "Sign in to continue.")
        self._check_session_alive(session)
        user, memberships = self._session_people(session)
        membership, organization = next((m, o) for m, o in memberships if o.id == session.org_id)
        if now - session.last_seen_at >= _LAST_SEEN_WRITE_INTERVAL:
            session.last_seen_at = now
        self.db.commit()
        # The role comes from the database, not the token, so role changes apply immediately.
        return AuthContext(
            user=user,
            organization=organization,
            role=membership.role,
            session=session,
            memberships=memberships,
        )
