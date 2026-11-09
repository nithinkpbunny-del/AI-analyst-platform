"""Test helpers for workspaces: people with roles in organisations, and access tokens for them without going
through the sign-in screens (fast, and independent of 2FA details tested elsewhere)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth import passwords
from app.auth.tokens import issue_access_token
from app.config import Settings
from app.models import Membership, Organization, Session, TwoFactor, User
from tests.authflow import PASSWORD

_EARLIER = datetime(2026, 11, 1, tzinfo=UTC)


def first_org(db: DbSession) -> Organization:
    org = db.scalars(select(Organization).order_by(Organization.created_at)).first()
    assert org is not None
    return org


def add_member(
    db: DbSession,
    org_id: uuid.UUID | None = None,
    *,
    email: str = "ravi@urbancart.in",
    role: str = "analyst",
    name: str = "Ravi Kumar",
    two_factor: bool = False,
    now: datetime | None = None,
) -> User:
    """Someone with a confirmed email and a password, active in ``org_id`` (default: the first workspace)."""
    org_id = org_id or first_org(db).id
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            name=name,
            password_hash=passwords.hash_password(PASSWORD),
            email_verified_at=now or _EARLIER,
        )
        db.add(user)
        db.flush()
    db.add(Membership(org_id=org_id, user_id=user.id, role=role, status="active"))
    if two_factor and db.get(TwoFactor, user.id) is None:
        db.add(
            TwoFactor(
                user_id=user.id,
                method="email",
                recovery_codes_hash=[],
                enabled_at=now or _EARLIER,
            )
        )
    db.commit()
    return user


def add_second_owner(db: DbSession, org_id: uuid.UUID | None = None) -> User:
    """A second Owner, so tests can demote or remove the first (a workspace always keeps one, M4)."""
    return add_member(
        db, org_id, email="meera@urbancart.in", role="owner", name="Meera Shah", two_factor=True
    )


def make_org(db: DbSession, name: str, owner_email: str, owner_name: str = "Owner") -> Organization:
    org = Organization(name=name, monthly_question_limit=500)
    db.add(org)
    db.flush()
    add_member(db, org.id, email=owner_email, role="owner", name=owner_name, two_factor=True)
    return org


def token_for(
    db: DbSession, settings: Settings, now: datetime, user: User, org_id: uuid.UUID
) -> str:
    """An access token for a fresh session of ``user`` in ``org_id``."""
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id)
    )
    assert membership is not None
    session = Session(
        user_id=user.id,
        org_id=org_id,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_max_hours),
    )
    db.add(session)
    db.commit()
    token, _ = issue_access_token(
        secret=settings.jwt_secret.get_secret_value(),
        user_id=user.id,
        org_id=org_id,
        role=membership.role,
        session_id=session.id,
        now=now,
        lifetime=timedelta(minutes=settings.access_token_minutes),
    )
    return token


@dataclass
class World:
    """Two workspaces with people in every role (UrbanCart) and one Owner plus one Analyst (Chaiwala)."""

    org_a: Organization
    org_b: Organization
    owner_a: User
    analyst_a: User
    viewer_a: User
    owner_b: User
    analyst_b: User
    tokens: dict[str, str]

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}


def build_world(db: DbSession, settings: Settings, now: datetime) -> World:
    org_a = make_org(db, "UrbanCart", "asha@urbancart.in", "Asha Rao")
    org_b = make_org(db, "Chaiwala Foods", "kabir@chaiwala.in", "Kabir Das")
    owner_a = db.scalars(select(User).where(User.email == "asha@urbancart.in")).one()
    owner_b = db.scalars(select(User).where(User.email == "kabir@chaiwala.in")).one()
    analyst_a = add_member(db, org_a.id, email="ravi@urbancart.in", role="analyst", name="Ravi")
    viewer_a = add_member(db, org_a.id, email="lata@urbancart.in", role="viewer", name="Lata")
    analyst_b = add_member(db, org_b.id, email="zoya@chaiwala.in", role="analyst", name="Zoya")
    tokens = {
        "owner_a": token_for(db, settings, now, owner_a, org_a.id),
        "analyst_a": token_for(db, settings, now, analyst_a, org_a.id),
        "viewer_a": token_for(db, settings, now, viewer_a, org_a.id),
        "owner_b": token_for(db, settings, now, owner_b, org_b.id),
        "analyst_b": token_for(db, settings, now, analyst_b, org_b.id),
    }
    return World(org_a, org_b, owner_a, analyst_a, viewer_a, owner_b, analyst_b, tokens)
