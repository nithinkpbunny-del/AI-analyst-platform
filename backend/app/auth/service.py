"""Authentication use cases (BUILD_SPEC 6.1, R15): sign-up, email verification, login, refresh rotation with
reuse detection, logout, idle/absolute timeouts, password reset, consent and activity-log entries; since M3 also
two-step verification during sign-in, Google sign-in, new-device alerts and managing your own sessions.

Sign-in is two steps when a second factor is needed. The first factor (password or Google) ends in either a
session (``IssuedSession``) or a ``PendingSignIn``: an opaque challenge token the web app keeps in memory and
sends back with the code (or, for Owners without 2FA, while setting it up). Only then is a session created.

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
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.api.errors import ApiError
from app.audit import writer as audit
from app.audit.writer import RequestMeta
from app.auth import passwords
from app.auth.devices import (
    describe_user_agent,
    device_hash,
    mask_email,
    new_device_id,
    sign_device_id,
)
from app.auth.google_oidc import GoogleIdentity, GoogleSignInError
from app.auth.ratelimit import FixedWindowLimiter, LoginThrottle
from app.auth.tokens import (
    ExpiredAccessToken,
    InvalidAccessToken,
    decode_access_token,
    hash_token,
    issue_access_token,
    new_opaque_token,
)
from app.auth.two_factor import SetupStarted, TwoFactorManager, UsedFactor, wrong_code
from app.config import Settings
from app.email import templates
from app.email.sender import OutgoingEmail
from app.models import (
    Consent,
    KnownDevice,
    LoginChallenge,
    Membership,
    Organization,
    RefreshToken,
    Session,
    User,
    UserToken,
)
from app.models.two_factor import ChallengeKind, TwoFactorMethod

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
TokenPurpose = Literal["verify_email", "reset_password", "secure_account"]
AuthMethod = Literal["password", "google"]

INVALID_CREDENTIALS = "Invalid email or password."
# Updating last_seen_at on every request would mean a write per call; within this interval one write is enough.
_LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=30)
EMAIL_LIMIT_PER_ADDRESS = 5  # per hour: stops someone flooding a person's inbox
EMAIL_LIMIT_PER_IP = 30  # per hour
CHALLENGE_MINUTES = 15  # time to enter a code, or to scan a QR code and confirm it
CHALLENGE_ATTEMPTS = 5  # wrong codes per sign-in attempt; then sign in again
CODE_RESEND_SECONDS = 30
CODE_SENDS_PER_CHALLENGE = 5
SECURE_ACCOUNT_LINK_DAYS = 7
CHALLENGE_EXPIRED = "This sign-in has expired. Sign in again."


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
    # A freshly signed ``analyst_device`` cookie value when this browser had none (or an invalid one).
    device_cookie: str | None = None


@dataclass(frozen=True)
class PendingSignIn:
    """The first factor passed; the browser must now enter a code (or set up 2FA) with this token."""

    challenge_token: str
    kind: Literal["two_factor", "two_factor_setup"]
    method: str | None
    email_hint: str | None
    expires_at: datetime
    code_sent: bool


LoginOutcome = IssuedSession | PendingSignIn


@dataclass(frozen=True)
class AuthContext:
    """The authenticated caller of a request."""

    user: User
    organization: Organization
    role: str
    session: Session
    memberships: list[tuple[Membership, Organization]]
    # BUILD_SPEC 6.1: Owners may not perform owner actions until this is true (``app.auth.permissions.require``).
    two_factor_enabled: bool = False


@dataclass(frozen=True)
class SwitchedSession:
    """A session moved to another workspace: a new access token; the refresh cookie is unchanged."""

    access_token: str
    access_expires_at: datetime
    session: Session
    user: User
    organization: Organization
    role: str
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
    code_failures: LoginThrottle  # wrong 2FA codes per person
    code_email_limiter: FixedWindowLimiter  # 2FA code emails per person
    device_id: str | None = None  # verified id from the browser's device cookie
    outbox: list[OutgoingEmail] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.factors = TwoFactorManager(
            db=self.db,
            settings=self.settings,
            clock=self.clock,
            failures=self.code_failures,
            email_limiter=self.code_email_limiter,
            outbox=self.outbox,
        )

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

    def _new_user_token(
        self, user: User, purpose: TokenPurpose, lifetime: timedelta, *, keep_older: bool = False
    ) -> str:
        now = self._now()
        if not keep_older:  # only the newest link of each kind works
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
        device_cookie: str | None = None,
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
            device_cookie=device_cookie,
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
    ) -> LoginOutcome:
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
        return self._after_first_factor(user, memberships, chosen.id, "password")

    # ------------------------------------------------------------------ second factor during sign-in
    def _after_first_factor(
        self,
        user: User,
        memberships: list[tuple[Membership, Organization]],
        org_id: uuid.UUID,
        auth_method: AuthMethod,
    ) -> LoginOutcome:
        """Password or Google passed: ask for a code, ask an Owner to set up 2FA, or sign in."""
        row = self.factors.row(user.id)
        if row is not None and row.enabled_at is not None:
            challenge, token = self._new_challenge(user, org_id, "two_factor", auth_method)
            sent = False
            if row.method == "email":
                sent = self.factors.send_email_code(user, "login")
                challenge.codes_sent = 1 if sent else 0
                challenge.last_code_sent_at = self._now() if sent else None
            self.db.commit()
            return PendingSignIn(
                challenge_token=token,
                kind="two_factor",
                method=row.method,
                email_hint=mask_email(user.email) if row.method == "email" else None,
                expires_at=challenge.expires_at,
                code_sent=sent,
            )
        if self.factors.is_required(user.id):
            challenge, token = self._new_challenge(user, org_id, "two_factor_setup", auth_method)
            self.db.commit()
            return PendingSignIn(
                challenge_token=token,
                kind="two_factor_setup",
                method=None,
                email_hint=mask_email(user.email),
                expires_at=challenge.expires_at,
                code_sent=False,
            )
        return self._start_session(
            user, memberships, org_id, auth_method=auth_method, second_factor=None
        )

    def _new_challenge(
        self,
        user: User | None,
        org_id: uuid.UUID | None,
        kind: ChallengeKind,
        auth_method: AuthMethod,
        data: dict[str, object] | None = None,
    ) -> tuple[LoginChallenge, str]:
        token = new_opaque_token()
        challenge = LoginChallenge(
            user_id=user.id if user else None,
            org_id=org_id,
            kind=kind,
            auth_method=auth_method,
            token_hash=hash_token(token),
            data=data or {},
            expires_at=self._now() + timedelta(minutes=CHALLENGE_MINUTES),
        )
        self.db.add(challenge)
        return challenge, token

    def _load_challenge(
        self, token: str, kind: ChallengeKind, *, lock: bool = True
    ) -> LoginChallenge:
        query = select(LoginChallenge).where(
            LoginChallenge.token_hash == hash_token(token), LoginChallenge.kind == kind
        )
        challenge = self.db.scalar(query.with_for_update() if lock else query)
        if (
            challenge is None
            or challenge.used_at is not None
            or challenge.expires_at <= self._now()
        ):
            self.db.rollback()
            raise ApiError(401, "challenge_expired", CHALLENGE_EXPIRED)
        return challenge

    def _challenge_people(
        self, challenge: LoginChallenge
    ) -> tuple[User, list[tuple[Membership, Organization]], uuid.UUID]:
        """The challenge's user, still active and still a member somewhere; otherwise the sign-in ends."""
        user = self.db.get(User, challenge.user_id) if challenge.user_id else None
        memberships = self._active_memberships(user.id) if user else []
        if user is None or not user.is_active or not memberships:
            challenge.used_at = self._now()
            self.db.commit()
            raise ApiError(401, "challenge_expired", CHALLENGE_EXPIRED)
        org_ids = [o.id for _, o in memberships]
        org_id = challenge.org_id if challenge.org_id in org_ids else org_ids[0]
        return user, memberships, org_id

    def _wrong_challenge_code(self, challenge: LoginChallenge, user: User, reason: str) -> ApiError:
        """Count a wrong code against this sign-in; after ``CHALLENGE_ATTEMPTS`` it ends."""
        challenge.attempts += 1
        exhausted = challenge.attempts >= CHALLENGE_ATTEMPTS
        if exhausted:
            challenge.used_at = self._now()
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.login.failure",
            at=self._now(),
            meta=self.meta,
            user_id=user.id,
            details={"reason": reason, "method": challenge.auth_method},
        )
        self.db.commit()
        if exhausted:
            return ApiError(401, "challenge_expired", "Too many wrong codes. Sign in again.")
        return wrong_code()

    def _check_codes_not_locked(self, challenge: LoginChallenge, user: User) -> None:
        try:
            self.factors.check_not_locked(user.id)
        except ApiError:
            audit.write_for_orgs(
                self.db,
                self._org_ids(user),
                action="auth.login.failure",
                at=self._now(),
                meta=self.meta,
                user_id=user.id,
                details={"reason": "two_factor_locked", "method": challenge.auth_method},
            )
            self.db.commit()
            raise

    def verify_login_code(self, token: str, code: str) -> IssuedSession:
        challenge = self._load_challenge(token, "two_factor")
        user, memberships, org_id = self._challenge_people(challenge)
        self._check_codes_not_locked(challenge, user)
        used = self.factors.verify(user, code, "login")
        if used is None:
            self.factors.record_failure(user.id)
            raise self._wrong_challenge_code(challenge, user, "bad_2fa_code")
        self.factors.failures.record_success(str(user.id), "-")
        challenge.used_at = self._now()
        if used == "recovery":
            self._recovery_code_used(user)
        return self._start_session(
            user,
            memberships,
            org_id,
            auth_method="google" if challenge.auth_method == "google" else "password",
            second_factor=used,
        )

    def _recovery_code_used(self, user: User) -> None:
        row = self.factors.row(user.id)
        remaining = len(row.recovery_codes_hash) if row else 0
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.2fa.recovery_code_used",
            at=self._now(),
            meta=self.meta,
            user_id=user.id,
            details={"remaining": remaining},
        )
        self._notify_two_factor_change(
            user,
            f"a recovery code was just used to sign in. You have {remaining} left. "
            "Create new ones in Settings if you are running low.",
        )

    def resend_login_code(self, token: str) -> PendingSignIn:
        challenge = self._load_challenge(token, "two_factor")
        user, _, _ = self._challenge_people(challenge)
        row = self.factors.row(user.id)
        if row is None or row.method != "email":
            self.db.rollback()
            raise ApiError(400, "no_email_code", "Use the code from your authenticator app.")
        now = self._now()
        if challenge.codes_sent >= CODE_SENDS_PER_CHALLENGE:
            self.db.rollback()
            raise ApiError(429, "too_many_requests", "Too many codes were sent. Sign in again.")
        if challenge.last_code_sent_at and now - challenge.last_code_sent_at < timedelta(
            seconds=CODE_RESEND_SECONDS
        ):
            wait = CODE_RESEND_SECONDS - int((now - challenge.last_code_sent_at).total_seconds())
            self.db.rollback()
            raise ApiError(
                429,
                "too_many_requests",
                f"Wait {wait} seconds before asking for another code.",
                headers={"Retry-After": str(wait)},
            )
        if not self.factors.send_email_code(user, "login"):
            self.db.rollback()
            raise ApiError(
                429,
                "too_many_requests",
                "Too many codes were sent. Wait a few minutes and try again.",
            )
        challenge.codes_sent += 1
        challenge.last_code_sent_at = now
        self.db.commit()
        return PendingSignIn(
            challenge_token=token,
            kind="two_factor",
            method="email",
            email_hint=mask_email(user.email),
            expires_at=challenge.expires_at,
            code_sent=True,
        )

    def begin_login_setup(self, token: str, method: TwoFactorMethod) -> SetupStarted:
        challenge = self._load_challenge(token, "two_factor_setup")
        user, _, _ = self._challenge_people(challenge)
        try:
            started = self.factors.begin_setup(user, method)
        except ApiError:
            self.db.rollback()
            raise
        self.db.commit()
        return started

    def resend_login_setup_code(self, token: str) -> None:
        challenge = self._load_challenge(token, "two_factor_setup")
        user, _, _ = self._challenge_people(challenge)
        sent = self.factors.resend_setup_code(user)
        self.db.commit()
        if not sent:
            raise ApiError(
                429,
                "too_many_requests",
                "Too many codes were sent. Wait a few minutes and try again.",
            )

    def confirm_login_setup(self, token: str, code: str) -> tuple[IssuedSession, list[str]]:
        challenge = self._load_challenge(token, "two_factor_setup")
        user, memberships, org_id = self._challenge_people(challenge)
        self._check_codes_not_locked(challenge, user)
        try:
            method, recovery, _ = self.factors.confirm_setup(user, code)
        except ApiError as error:
            if error.code == "invalid_code":
                raise self._wrong_challenge_code(challenge, user, "bad_2fa_setup_code") from error
            self.db.rollback()
            raise
        challenge.used_at = self._now()
        self._two_factor_enabled(user, method, was_change=False)
        issued = self._start_session(
            user,
            memberships,
            org_id,
            auth_method="google" if challenge.auth_method == "google" else "password",
            second_factor=method,
        )
        return issued, recovery

    # ------------------------------------------------------------------ sessions and devices
    def _start_session(
        self,
        user: User,
        memberships: list[tuple[Membership, Organization]],
        org_id: uuid.UUID,
        *,
        auth_method: AuthMethod,
        second_factor: UsedFactor | None,
    ) -> IssuedSession:
        now = self._now()
        device_cookie = None
        device_id = self.device_id
        if device_id is None:
            device_id = new_device_id()
            device_cookie = sign_device_id(device_id, self.settings.jwt_secret.get_secret_value())
        hashed_device = device_hash(device_id)
        session = Session(
            user_id=user.id,
            org_id=org_id,
            device_id=hashed_device,
            ip=self.meta.ip,
            user_agent=self.meta.user_agent[:512] if self.meta.user_agent else None,
            last_seen_at=now,
            expires_at=now + timedelta(hours=self.settings.session_max_hours),
        )
        self.db.add(session)
        self.db.flush()
        new_device = self._remember_device(user, hashed_device, org_id, memberships)
        issued = self._issue(session, user, memberships, device_cookie)
        audit.write(
            self.db,
            action="auth.login.success",
            at=now,
            meta=self.meta,
            org_id=org_id,
            user_id=user.id,
            target_type="session",
            target_id=session.id,
            details={"method": auth_method, "two_factor": second_factor, "new_device": new_device},
        )
        self.db.commit()
        return issued

    def _remember_device(
        self,
        user: User,
        hashed_device: str,
        org_id: uuid.UUID,
        memberships: list[tuple[Membership, Organization]],
    ) -> bool:
        """Record the device; True (and a "New sign-in" email) if it is new for someone with earlier devices.

        The very first device is not announced: that is the person's own sign-up or first sign-in.
        """
        now = self._now()
        known = self.db.scalar(
            select(KnownDevice)
            .where(KnownDevice.user_id == user.id, KnownDevice.device_id_hash == hashed_device)
            .with_for_update()
        )
        if known is not None:
            known.last_seen_at = now
            return False
        has_earlier = bool(self.db.scalar(select(exists().where(KnownDevice.user_id == user.id))))
        self.db.add(
            KnownDevice(
                user_id=user.id, device_id_hash=hashed_device, first_seen_at=now, last_seen_at=now
            )
        )
        if not has_earlier:
            return False
        organization = next(o for _, o in memberships if o.id == org_id)
        token = self._new_user_token(
            user,
            "secure_account",
            timedelta(days=SECURE_ACCOUNT_LINK_DAYS),
            keep_older=True,
        )
        browser = describe_user_agent(self.meta.user_agent)
        self.outbox.append(
            templates.render(
                "new_sign_in",
                to=user.email,
                name=user.name,
                org_name=organization.name,
                when=_local_time(now, organization.timezone),
                browser=browser,
                ip=self.meta.ip or "Unknown",
                link=self._link("/secure-account", token),
            )
        )
        audit.write(
            self.db,
            action="auth.device.new",
            at=now,
            meta=self.meta,
            org_id=org_id,
            user_id=user.id,
            details={"browser": browser},
        )
        return True

    def secure_account(self, token: str) -> int:
        """ "This wasn't me": sign out everywhere and forget every device. Returns the sessions ended."""
        consumed = self._consume_user_token(token, "secure_account")
        if consumed is None:
            self.db.rollback()
            raise ApiError(400, "invalid_link", "This link has expired or was already used.")
        _, user = consumed
        now = self._now()
        revoked = self._revoke_all_sessions(user.id, "reported")
        self.db.execute(delete(KnownDevice).where(KnownDevice.user_id == user.id))
        self.db.execute(
            update(LoginChallenge)
            .where(LoginChallenge.user_id == user.id, LoginChallenge.used_at.is_(None))
            .values(used_at=now)
        )
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.device.reported",
            at=now,
            meta=self.meta,
            user_id=user.id,
            details={"sessions_revoked": revoked},
        )
        self.db.commit()
        return revoked

    def list_sessions(self, auth: AuthContext) -> list[Session]:
        now = self._now()
        idle_cutoff = now - timedelta(minutes=self.settings.session_idle_minutes)
        return list(
            self.db.scalars(
                select(Session)
                .where(
                    Session.user_id == auth.user.id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                    Session.last_seen_at > idle_cutoff,
                )
                .order_by(Session.last_seen_at.desc())
            )
        )

    def revoke_own_session(self, auth: AuthContext, session_id: uuid.UUID) -> None:
        session = self.db.scalar(
            select(Session).where(
                Session.id == session_id,
                Session.user_id == auth.user.id,
                Session.revoked_at.is_(None),
            )
        )
        if session is None:  # someone else's session looks exactly like a missing one
            raise ApiError(404, "not_found", "Not found.")
        self._revoke_session(session, "signed_out_remotely")
        audit.write(
            self.db,
            action="auth.session.revoked",
            at=self._now(),
            meta=self.meta,
            org_id=auth.organization.id,
            user_id=auth.user.id,
            target_type="session",
            target_id=session.id,
            details={"current": session.id == auth.session.id},
        )
        self.db.commit()

    def revoke_other_sessions(self, auth: AuthContext) -> int:
        others = self.db.scalars(
            select(Session).where(
                Session.user_id == auth.user.id,
                Session.revoked_at.is_(None),
                Session.id != auth.session.id,
            )
        ).all()
        for session in others:
            self._revoke_session(session, "signed_out_remotely")
        audit.write(
            self.db,
            action="auth.session.revoked_others",
            at=self._now(),
            meta=self.meta,
            org_id=auth.organization.id,
            user_id=auth.user.id,
            details={"sessions_revoked": len(others)},
        )
        self.db.commit()
        return len(others)

    # ------------------------------------------------------------------ two-step verification settings
    def _notify_two_factor_change(self, user: User, change: str) -> None:
        self.outbox.append(
            templates.render(
                "two_factor_changed",
                to=user.email,
                name=user.name,
                change=change,
                link=self._link("/forgot-password"),
            )
        )

    def _two_factor_enabled(self, user: User, method: str, *, was_change: bool) -> None:
        label = "an authenticator app" if method == "totp" else "codes sent by email"
        audit.write_for_orgs(
            self.db,
            self._org_ids(user),
            action="auth.2fa.method_changed" if was_change else "auth.2fa.enabled",
            at=self._now(),
            meta=self.meta,
            user_id=user.id,
            details={"method": method},
        )
        self._notify_two_factor_change(
            user,
            f"two-step verification now uses {label}. New recovery codes were created and the old "
            "ones stopped working."
            if was_change
            else f"two-step verification is now on, using {label}.",
        )

    def _step_up(self, auth: AuthContext, code: str | None, action: str) -> UsedFactor:
        """Re-check the person before a security change: a code from their current method."""
        if not code:
            raise ApiError(
                400,
                "code_required",
                "Enter a code from your current method to make this change.",
                fields={"code": "Enter the code."},
            )
        try:
            used = self.factors.verify_or_fail(auth.user, code, "step_up")
        except ApiError as error:
            if error.code == "invalid_code":
                audit.write(
                    self.db,
                    action="auth.2fa.code_failed",
                    at=self._now(),
                    meta=self.meta,
                    org_id=auth.organization.id,
                    user_id=auth.user.id,
                    details={"for": action},
                )
            self.db.commit()  # keep attempt counters and the log entry
            raise
        if used == "recovery":
            self._recovery_code_used(auth.user)
        return used

    def begin_two_factor_setup(
        self, auth: AuthContext, method: TwoFactorMethod, code: str | None
    ) -> SetupStarted:
        if self.factors.summary(auth.user.id).enabled:
            self._step_up(auth, code, "change_method")
        try:
            started = self.factors.begin_setup(auth.user, method)
        except ApiError:
            self.db.commit()
            raise
        self.db.commit()
        return started

    def resend_two_factor_setup_code(self, auth: AuthContext) -> None:
        sent = self.factors.resend_setup_code(auth.user)
        self.db.commit()
        if not sent:
            raise ApiError(
                429,
                "too_many_requests",
                "Too many codes were sent. Wait a few minutes and try again.",
            )

    def confirm_two_factor_setup(self, auth: AuthContext, code: str) -> list[str]:
        try:
            method, recovery, was_change = self.factors.confirm_setup(auth.user, code)
        except ApiError:
            self.db.commit()
            raise
        self._two_factor_enabled(auth.user, method, was_change=was_change)
        self.db.commit()
        return recovery

    def send_step_up_code(self, auth: AuthContext) -> None:
        row = self.factors.row(auth.user.id)
        if row is None or row.enabled_at is None or row.method != "email":
            raise ApiError(400, "no_email_code", "Use the code from your authenticator app.")
        sent = self.factors.send_email_code(auth.user, "step_up")
        self.db.commit()
        if not sent:
            raise ApiError(
                429,
                "too_many_requests",
                "Too many codes were sent. Wait a few minutes and try again.",
            )

    def regenerate_recovery_codes(self, auth: AuthContext, code: str | None) -> list[str]:
        self._step_up(auth, code, "recovery_codes")
        recovery = self.factors.regenerate_recovery_codes(auth.user)
        audit.write_for_orgs(
            self.db,
            self._org_ids(auth.user),
            action="auth.2fa.recovery_codes_regenerated",
            at=self._now(),
            meta=self.meta,
            user_id=auth.user.id,
        )
        self._notify_two_factor_change(
            auth.user, "new recovery codes were created. The old ones no longer work."
        )
        self.db.commit()
        return recovery

    def disable_two_factor(self, auth: AuthContext, code: str | None) -> None:
        if self.factors.is_required(auth.user.id):
            self.factors.disable(auth.user)  # raises the Owner explanation
        self._step_up(auth, code, "disable")
        self.factors.disable(auth.user)
        audit.write_for_orgs(
            self.db,
            self._org_ids(auth.user),
            action="auth.2fa.disabled",
            at=self._now(),
            meta=self.meta,
            user_id=auth.user.id,
        )
        self._notify_two_factor_change(auth.user, "two-step verification was turned off.")
        self.db.commit()

    # ------------------------------------------------------------------ Google sign-in
    def google_identity(self, identity: GoogleIdentity) -> tuple[Literal["handoff", "signup"], str]:
        """Google confirmed who this is. Returns the next step and a one-time token for the web app.

        Linking (BUILD_SPEC 6.1): an existing account is linked only when Google says the email address is
        verified. If the existing account never confirmed its email, its password is removed: it may have
        been set by someone who does not own the address.
        """
        now = self._now()
        if not identity.email_verified:
            raise GoogleSignInError("google_email_unverified", "Google email not verified")
        user = self.db.scalar(select(User).where(User.google_sub == identity.sub))
        if user is None:
            user = self._user_by_email(identity.email)
            if user is not None:
                if user.google_sub and user.google_sub != identity.sub:
                    raise GoogleSignInError("google_other_account", "email linked to another sub")
                if user.email_verified_at is None:
                    user.password_hash = None
                    user.email_verified_at = now
                user.google_sub = identity.sub
                audit.write_for_orgs(
                    self.db,
                    self._org_ids(user),
                    action="auth.google.linked",
                    at=now,
                    meta=self.meta,
                    user_id=user.id,
                )
        if user is None:
            _, token = self._new_challenge(
                None,
                None,
                "google_signup",
                "google",
                data={"sub": identity.sub, "email": identity.email, "name": identity.name},
            )
            self.db.commit()
            return "signup", token
        memberships = self._active_memberships(user.id)
        if not user.is_active or not memberships:
            reason = "inactive" if not user.is_active else "no_membership"
            audit.write_for_orgs(
                self.db,
                self._org_ids(user),
                action="auth.login.failure",
                at=now,
                meta=self.meta,
                user_id=user.id,
                details={"reason": reason, "method": "google"},
            )
            self.db.commit()
            raise GoogleSignInError(
                "no_organization" if user.is_active else "google_failed", reason
            )
        _, token = self._new_challenge(user, memberships[0][1].id, "google_handoff", "google")
        self.db.commit()
        return "handoff", token

    def google_finish(self, token: str) -> LoginOutcome:
        challenge = self._load_challenge(token, "google_handoff")
        user, memberships, org_id = self._challenge_people(challenge)
        challenge.used_at = self._now()
        return self._after_first_factor(user, memberships, org_id, "google")

    def google_signup_details(self, token: str) -> tuple[str, str]:
        challenge = self._load_challenge(token, "google_signup", lock=False)
        return str(challenge.data["email"]), str(challenge.data["name"])

    def google_signup(self, token: str, *, name: str, organization_name: str) -> LoginOutcome:
        challenge = self._load_challenge(token, "google_signup")
        now = self._now()
        email, sub = str(challenge.data["email"]), str(challenge.data["sub"])
        challenge.used_at = now
        taken = self.db.scalar(
            select(exists().where((User.email == email) | (User.google_sub == sub)))
        )
        if taken:
            self.db.commit()
            raise ApiError(
                409,
                "account_exists",
                "An account with this email already exists. Sign in with Google instead.",
            )
        org = Organization(
            name=organization_name,
            monthly_question_limit=self.settings.managed_monthly_question_limit,
        )
        user = User(
            email=email, name=name, password_hash=None, google_sub=sub, email_verified_at=now
        )
        self.db.add_all([org, user])
        self.db.flush()
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
            details={"method": "google"},
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
        self.db.flush()
        return self._after_first_factor(user, self._active_memberships(user.id), org.id, "google")

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

    # ------------------------------------------------------------------ helpers for invitations (M4)
    def user_by_email(self, email: str) -> User | None:
        return self._user_by_email(email)

    def active_memberships(self, user_id: uuid.UUID) -> list[tuple[Membership, Organization]]:
        return self._active_memberships(user_id)

    def continue_sign_in(
        self, user: User, org_id: uuid.UUID, auth_method: AuthMethod
    ) -> LoginOutcome:
        """The first factor passed (or an invitation was accepted): second step, or a session in ``org_id``."""
        return self._after_first_factor(
            user, self._active_memberships(user.id), org_id, auth_method
        )

    def check_password_for(self, user: User | None, email: str, password: str) -> User:
        """The password checks of ``login`` (throttle, failure log, same answers) for an invitation sign-in."""
        ip = self.meta.ip or "-"
        lockout = self.login_throttle.check(email, ip, self._now())
        if lockout:
            self._login_failed(email, user, "rate_limited", count=False)
            raise ApiError(
                429,
                "too_many_attempts",
                "Too many sign-in attempts. Wait a few minutes, or reset your password.",
                headers={"Retry-After": str(lockout.retry_after_seconds)},
            )
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
        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)
        self.login_throttle.record_success(email, ip)
        return user

    def revoke_member_sessions(self, user_id: uuid.UUID, org_id: uuid.UUID) -> int:
        """End someone's sessions in one workspace (they were removed from it). Returns how many ended."""
        sessions = self.db.scalars(
            select(Session).where(
                Session.user_id == user_id,
                Session.org_id == org_id,
                Session.revoked_at.is_(None),
            )
        ).all()
        for session in sessions:
            self._revoke_session(session, "access_removed")
        return len(sessions)

    # ------------------------------------------------------------------ switching workspace (M4)
    def switch_organization(self, auth: AuthContext, org_id: uuid.UUID) -> SwitchedSession:
        """Move this session to another workspace the person belongs to. Not a member: 404, like a missing one.

        The refresh token stays the same; later refreshes issue tokens for the new workspace. Access tokens for
        the old one stop working (``authenticate`` answers ``token_expired``, so other tabs refresh and follow).
        """
        chosen = next(((m, o) for m, o in auth.memberships if o.id == org_id), None)
        if chosen is None:
            raise ApiError(404, "not_found", "Not found.")
        membership, organization = chosen
        now = self._now()
        session = self.db.get(Session, auth.session.id, with_for_update=True)
        if session is None or session.revoked_at is not None:
            self.db.rollback()
            raise ApiError(401, "session_revoked", "You were signed out. Sign in again.")
        previous = session.org_id
        session.org_id = organization.id
        session.last_seen_at = now
        if previous != organization.id:
            audit.write(
                self.db,
                action="auth.workspace_switched",
                at=now,
                meta=self.meta,
                org_id=organization.id,
                user_id=auth.user.id,
                target_type="session",
                target_id=session.id,
            )
        access, access_expires = issue_access_token(
            secret=self.settings.jwt_secret.get_secret_value(),
            user_id=auth.user.id,
            org_id=organization.id,
            role=membership.role,
            session_id=session.id,
            now=now,
            lifetime=timedelta(minutes=self.settings.access_token_minutes),
        )
        self.db.commit()
        return SwitchedSession(
            access_token=access,
            access_expires_at=access_expires,
            session=session,
            user=auth.user,
            organization=organization,
            role=membership.role,
            memberships=auth.memberships,
        )

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
        if session is None or session.user_id != claims.user_id:
            raise ApiError(401, "invalid_token", "Sign in to continue.")
        if session.org_id != claims.org_id:
            # The session switched workspace (maybe in another tab): a refresh gives a token for the new one.
            raise ApiError(401, "token_expired", "Your sign-in needs refreshing.")
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
            two_factor_enabled=self.factors.summary(user.id).enabled,
        )


def _local_time(moment: datetime, timezone: str) -> str:
    """``Mon 9 Nov 2026, 2:30 pm IST`` in the workspace's time zone."""
    try:
        local = moment.astimezone(ZoneInfo(timezone))
    except (KeyError, ValueError):
        local = moment
    hour = local.hour % 12 or 12
    suffix = "am" if local.hour < 12 else "pm"
    return f"{local:%a} {local.day} {local:%b %Y}, {hour}:{local:%M} {suffix} {local.tzname()}"
