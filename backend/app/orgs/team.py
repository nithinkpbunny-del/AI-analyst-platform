"""Team management for Owners (BUILD_SPEC 6.1, 6.2, R15): members, role changes, removal and invitations.

Runs inside a tenant-scoped session (``app.api.deps.requires``): Row-Level Security already hides other
workspaces, and every query also filters by ``org_id``. Something from another workspace therefore looks exactly
like something that does not exist (404).

Rules (DECISIONS D35):
* A workspace always keeps at least one active Owner (checked here under a lock, and by a database trigger).
* Nobody changes their own role or removes themselves here, so an Owner cannot lock themselves out by accident.
* Removal takes effect at once: the membership is marked ``removed`` and that person's sessions in this workspace
  end (with their refresh tokens). Every request re-reads the membership anyway (D14).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from app.api.deps import Tenant
from app.api.errors import ApiError
from app.audit import writer as audit
from app.auth.ratelimit import FixedWindowLimiter
from app.auth.tokens import hash_token, new_opaque_token
from app.email import templates
from app.models import Invitation, Membership, Organization, TwoFactor, User

INVITATION_DAYS = 7  # BUILD_SPEC 5
INVITES_PER_HOUR = 30  # per workspace: stops a stolen Owner session from spamming people

ROLE_LABELS = {"owner": "Owner", "analyst": "Analyst", "viewer": "Viewer"}
ROLE_SUMMARIES = {
    "owner": "Owners manage the workspace: people, connections, settings and the business dictionary.",
    "analyst": "Analysts ask questions, run what-ifs and long analyses, and share answers.",
    "viewer": "Viewers see answers, reports and dashboards that are shared with them.",
}


def role_words(role: str) -> dict[str, str]:
    label = ROLE_LABELS[role]
    return {
        "role_label": label,
        "role_article": "an" if label[0] in "AEIOU" else "a",
        "role_summary": ROLE_SUMMARIES[role],
    }


@dataclass(frozen=True)
class MemberRow:
    membership: Membership
    user: User
    two_factor_enabled: bool


@dataclass(frozen=True)
class InvitationRow:
    invitation: Invitation
    invited_by_name: str | None


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


@dataclass
class TeamService:
    tenant: Tenant
    invite_limiter: FixedWindowLimiter

    def _now(self) -> datetime:
        return self.tenant.service.clock()

    def _audit(
        self,
        action: str,
        *,
        target_type: str,
        target_id: uuid.UUID | None,
        details: dict[str, object],
    ) -> None:
        audit.write(
            self.tenant.db,
            action=action,
            at=self._now(),
            meta=self.tenant.service.meta,
            org_id=self.tenant.org_id,
            user_id=self.tenant.auth.user.id,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )

    def _link(self, path: str, token: str | None = None) -> str:
        base = self.tenant.service.settings.app_base_url.rstrip("/")
        return f"{base}{path}#token={token}" if token else f"{base}{path}"

    # ------------------------------------------------------------------------------------------------ members
    def members(self) -> list[MemberRow]:
        rows = self.tenant.db.execute(
            select(Membership, User, TwoFactor.enabled_at)
            .join(User, User.id == Membership.user_id)
            .outerjoin(TwoFactor, TwoFactor.user_id == User.id)
            .where(Membership.org_id == self.tenant.org_id, Membership.status == "active")
            .order_by(Membership.created_at, Membership.id)
        ).all()
        return [MemberRow(m, u, enabled is not None) for m, u, enabled in rows]

    def _member(self, membership_id: uuid.UUID) -> tuple[Membership, User]:
        row = self.tenant.db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.id == membership_id,
                Membership.org_id == self.tenant.org_id,
                Membership.status == "active",
            )
            .with_for_update(of=Membership)
        ).first()
        if row is None:
            raise _not_found()
        return row[0], row[1]

    def _lock_org(self) -> Organization:
        """Serialise member changes per workspace, so two Owners cannot demote each other at the same time."""
        org = self.tenant.db.scalar(
            select(Organization).where(Organization.id == self.tenant.org_id).with_for_update()
        )
        if org is None:  # pragma: no cover - the caller's own workspace always exists
            raise _not_found()
        return org

    def _active_owners(self) -> int:
        return int(
            self.tenant.db.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.org_id == self.tenant.org_id,
                    Membership.role == "owner",
                    Membership.status == "active",
                )
            )
            or 0
        )

    def _refuse_self(self, membership: Membership, code: str, message: str) -> None:
        if membership.user_id == self.tenant.auth.user.id:
            raise ApiError(409, code, message)

    def change_role(self, membership_id: uuid.UUID, role: str) -> MemberRow:
        org = self._lock_org()
        membership, user = self._member(membership_id)
        self._refuse_self(
            membership,
            "cannot_change_own_role",
            "You can't change your own role. Ask another Owner to do it.",
        )
        previous = membership.role
        if previous != role:
            if previous == "owner" and self._active_owners() <= 1:
                raise ApiError(409, "last_owner", "A workspace needs at least one Owner.")
            membership.role = role
            self._audit(
                "team.role_changed",
                target_type="user",
                target_id=user.id,
                details={"from": previous, "to": role, "email": user.email},
            )
            two_factor = self.tenant.db.get(TwoFactor, user.id)
            self.tenant.service.outbox.append(
                templates.render(
                    "role_changed",
                    to=user.email,
                    subject=f"Your role in {org.name} changed",
                    name=user.name,
                    org_name=org.name,
                    changed_by=self.tenant.auth.user.name,
                    needs_two_factor=role == "owner"
                    and (two_factor is None or two_factor.enabled_at is None),
                    link=self._link("/today"),
                    **role_words(role),
                )
            )
        self.tenant.db.commit()
        two_factor = self.tenant.db.get(TwoFactor, user.id)
        return MemberRow(membership, user, bool(two_factor and two_factor.enabled_at))

    def remove(self, membership_id: uuid.UUID) -> None:
        org = self._lock_org()
        membership, user = self._member(membership_id)
        self._refuse_self(
            membership,
            "cannot_remove_self",
            "You can't remove yourself. Ask another Owner to do it.",
        )
        if membership.role == "owner" and self._active_owners() <= 1:
            raise ApiError(409, "last_owner", "A workspace needs at least one Owner.")
        membership.status = "removed"
        ended = self.tenant.service.revoke_member_sessions(user.id, self.tenant.org_id)
        self._audit(
            "team.member_removed",
            target_type="user",
            target_id=user.id,
            details={"role": membership.role, "email": user.email, "sessions_revoked": ended},
        )
        self.tenant.service.outbox.append(
            templates.render(
                "access_removed",
                to=user.email,
                subject=f"Your access to {org.name} was removed",
                name=user.name,
                org_name=org.name,
                removed_by=self.tenant.auth.user.name,
            )
        )
        self.tenant.db.commit()

    # -------------------------------------------------------------------------------------------- invitations
    def invitations(self) -> list[InvitationRow]:
        rows = self.tenant.db.execute(
            select(Invitation, User.name)
            .outerjoin(User, User.id == Invitation.invited_by)
            .where(
                Invitation.org_id == self.tenant.org_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        ).all()
        return [InvitationRow(i, name) for i, name in rows]

    def _check_invite_limit(self) -> None:
        if not self.invite_limiter.hit(str(self.tenant.org_id), self._now()):
            raise ApiError(
                429,
                "too_many_requests",
                "Too many invitations were sent in the last hour. Try again later.",
            )

    def _send(self, org: Organization, invitation: Invitation) -> None:
        """Give the invitation a fresh token and expiry, and email the link."""
        token = new_opaque_token()
        invitation.token_hash = hash_token(token)
        invitation.expires_at = self._now() + timedelta(days=INVITATION_DAYS)
        inviter = self.tenant.auth.user.name
        self.tenant.service.outbox.append(
            templates.render(
                "invitation",
                to=invitation.email,
                subject=f"{inviter} invited you to join {org.name} on Analyst",
                inviter_name=inviter,
                org_name=org.name,
                email=invitation.email,
                days=INVITATION_DAYS,
                link=self._link("/accept-invite", token),
                **role_words(invitation.role),
            )
        )

    def invite(self, email: str, role: str) -> InvitationRow:
        email = email.strip()
        org = self._lock_org()
        already = self.tenant.db.scalar(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == self.tenant.org_id,
                Membership.status == "active",
                User.email == email,
            )
        )
        if already is not None:
            raise ApiError(
                409,
                "already_member",
                "This person is already a member of this workspace.",
                fields={"email": "Already a member."},
            )
        self._check_invite_limit()
        # One open invitation per address: a new invite replaces the old one (its link stops working).
        self.tenant.db.execute(
            update(Invitation)
            .where(
                Invitation.org_id == self.tenant.org_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=self._now())
        )
        invitation = Invitation(
            org_id=self.tenant.org_id,
            email=email,
            role=role,
            invited_by=self.tenant.auth.user.id,
            token_hash="",
            expires_at=self._now(),
        )
        self._send(org, invitation)
        self.tenant.db.add(invitation)
        self.tenant.db.flush()
        self._audit(
            "team.invite_sent",
            target_type="invitation",
            target_id=invitation.id,
            details={"email": email, "role": role},
        )
        self.tenant.db.commit()
        return InvitationRow(invitation, self.tenant.auth.user.name)

    def _open_invitation(self, invitation_id: uuid.UUID) -> Invitation:
        invitation = self.tenant.db.scalar(
            select(Invitation)
            .where(
                Invitation.id == invitation_id,
                Invitation.org_id == self.tenant.org_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if invitation is None:
            raise _not_found()
        return invitation

    def resend(self, invitation_id: uuid.UUID) -> InvitationRow:
        org = self._lock_org()
        invitation = self._open_invitation(invitation_id)
        self._check_invite_limit()
        self._send(org, invitation)
        self._audit(
            "team.invite_resent",
            target_type="invitation",
            target_id=invitation.id,
            details={"email": invitation.email, "role": invitation.role},
        )
        self.tenant.db.commit()
        inviter = self.tenant.db.get(User, invitation.invited_by) if invitation.invited_by else None
        return InvitationRow(invitation, inviter.name if inviter else None)

    def revoke(self, invitation_id: uuid.UUID) -> None:
        invitation = self._open_invitation(invitation_id)
        invitation.revoked_at = self._now()
        self._audit(
            "team.invite_revoked",
            target_type="invitation",
            target_id=invitation.id,
            details={"email": invitation.email, "role": invitation.role},
        )
        self.tenant.db.commit()
