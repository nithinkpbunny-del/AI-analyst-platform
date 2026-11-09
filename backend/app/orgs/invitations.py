"""Accepting an invitation (BUILD_SPEC 5, R15: "invitee sets their own login via link").

Three ways, depending on who opens the link (DECISIONS D36):

* **New person:** chooses a name and password and accepts the terms. The link proves they own the address, so the
  email counts as confirmed. They are signed in straight away (Analysts and Viewers need no second step).
* **Existing account, signed out:** enters their password on the invitation page. The usual sign-in checks apply
  (lockouts, activity log, two-step verification), and the session opens in the new workspace.
* **Existing account, signed in as the invited address:** one click; the session moves to the new workspace.

The invitation does not belong to the caller's workspace yet, so this runs outside tenant scope (like sign-in),
always looking the invitation up by the hash of its single-use token.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth import passwords
from app.auth.service import AuthContext, AuthService, LoginOutcome, SwitchedSession
from app.auth.tokens import hash_token
from app.models import Consent, Invitation, Membership, Organization, User

INVALID_INVITATION = (
    "This invitation has expired, was replaced by a newer one or was already used. "
    "Ask the person who invited you to send a new one."
)


@dataclass(frozen=True)
class InvitationPreview:
    organization_name: str
    invited_by_name: str | None
    email: str
    role: str
    expires_at: datetime
    account_exists: bool
    has_password: bool


@dataclass
class InvitationAcceptance:
    service: AuthService

    def _now(self) -> datetime:
        return self.service.clock()

    def _load(self, token: str, *, lock: bool = True) -> tuple[Invitation, Organization]:
        query = (
            select(Invitation, Organization)
            .join(Organization, Organization.id == Invitation.org_id)
            .where(
                Invitation.token_hash == hash_token(token),
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
        row = self.service.db.execute(
            query.with_for_update(of=Invitation) if lock else query
        ).first()
        if row is None or row[0].expires_at <= self._now():
            self.service.db.rollback()
            raise ApiError(400, "invalid_link", INVALID_INVITATION)
        return row[0], row[1]

    def preview(self, token: str) -> InvitationPreview:
        invitation, org = self._load(token, lock=False)
        user = self.service.user_by_email(invitation.email)
        inviter = (
            self.service.db.get(User, invitation.invited_by) if invitation.invited_by else None
        )
        self.service.db.rollback()
        return InvitationPreview(
            organization_name=org.name,
            invited_by_name=inviter.name if inviter else None,
            email=invitation.email,
            role=invitation.role,
            expires_at=invitation.expires_at,
            account_exists=user is not None,
            has_password=bool(user and user.password_hash),
        )

    def _join(self, user: User, invitation: Invitation, *, method: str, new_account: bool) -> None:
        now = self._now()
        membership = self.service.db.scalar(
            select(Membership)
            .where(Membership.org_id == invitation.org_id, Membership.user_id == user.id)
            .with_for_update()
        )
        rejoined = membership is not None and membership.status != "active"
        if membership is None:
            self.service.db.add(
                Membership(
                    org_id=invitation.org_id, user_id=user.id, role=invitation.role, status="active"
                )
            )
        elif rejoined:  # removed earlier, invited again: the invitation's role applies
            membership.status = "active"
            membership.role = invitation.role
        invitation.accepted_at = now
        invitation.accepted_by = user.id
        audit.write(
            self.service.db,
            action="team.invite_accepted",
            at=now,
            meta=self.service.meta,
            org_id=invitation.org_id,
            user_id=user.id,
            target_type="invitation",
            target_id=invitation.id,
            details={
                "role": invitation.role,
                "method": method,
                "new_account": new_account,
                "rejoined": rejoined,
            },
        )
        self.service.db.flush()

    def accept_new_account(self, token: str, *, name: str, password: str) -> LoginOutcome:
        invitation, _ = self._load(token)
        if self.service.user_by_email(invitation.email) is not None:
            self.service.db.rollback()
            raise ApiError(
                409,
                "account_exists",
                "You already have an account. Enter your password to accept the invitation.",
            )
        problem = passwords.password_problem(password, email=invitation.email)
        if problem:
            self.service.db.rollback()
            raise ApiError(422, "weak_password", problem, fields={"password": problem})
        now = self._now()
        user = User(
            email=invitation.email,
            name=name,
            password_hash=passwords.hash_password(password),
            email_verified_at=now,  # the invitation link proves they own the address
        )
        self.service.db.add(user)
        try:
            self.service.db.flush()
        except IntegrityError as error:  # the same address signed up at this very moment
            self.service.db.rollback()
            raise ApiError(
                409,
                "account_exists",
                "You already have an account. Enter your password to accept the invitation.",
            ) from error
        self.service.db.add(
            Consent(
                user_id=user.id,
                org_id=invitation.org_id,
                policy_version=self.service.settings.policy_version,
                accepted_at=now,
            )
        )
        audit.write(
            self.service.db,
            action="consent.accepted",
            at=now,
            meta=self.service.meta,
            org_id=invitation.org_id,
            user_id=user.id,
            details={"policy_version": self.service.settings.policy_version},
        )
        self._join(user, invitation, method="password", new_account=True)
        return self.service.continue_sign_in(user, invitation.org_id, "password")

    def accept_with_password(self, token: str, *, password: str) -> LoginOutcome:
        invitation, _ = self._load(token)
        user = self.service.user_by_email(invitation.email)
        user = self.service.check_password_for(user, invitation.email, password)
        if user.email_verified_at is None:
            user.email_verified_at = self._now()  # the invitation link proves they own the address
        self._join(user, invitation, method="password", new_account=False)
        return self.service.continue_sign_in(user, invitation.org_id, "password")

    def accept_signed_in(self, auth: AuthContext, token: str) -> SwitchedSession:
        invitation, _ = self._load(token)
        if auth.user.email.lower() != invitation.email.lower():
            self.service.db.rollback()
            raise ApiError(
                403,
                "wrong_account",
                f"This invitation is for {invitation.email}. Sign out, then open the link again "
                "and sign in with that address.",
            )
        self._join(auth.user, invitation, method="signed_in", new_account=False)
        org_id = invitation.org_id
        self.service.db.commit()
        refreshed = dataclasses.replace(
            auth, memberships=self.service.active_memberships(auth.user.id)
        )
        return self.service.switch_organization(refreshed, org_id)
