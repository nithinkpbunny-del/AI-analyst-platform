"""Security of team management (M4): invitation links stay secret, a stolen link alone gives nothing to an
existing account, a removed member's refresh token is dead, and nothing crosses workspaces."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Invitation, Membership
from tests.authflow import PASSWORD, link_token, refresh, registered_user
from tests.conftest import FakeClock
from tests.tenancy import World, add_member, build_world

SUBJECT = "Asha Rao invited you to join UrbanCart on Analyst"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def test_invitation_tokens_are_hashed_and_never_logged(
    client: TestClient,
    db: DbSession,
    world: World,
    outbox: MemoryEmailSender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client.post(
        "/team/invitations",
        json={"email": "dev@urbancart.in", "role": "analyst"},
        headers=world.headers("owner_a"),
    )
    token = link_token(outbox, "dev@urbancart.in", SUBJECT)
    client.post("/invitations/preview", json={"token": token})
    client.post(
        "/invitations/accept",
        json={"token": token, "name": "Dev", "password": PASSWORD, "accept_terms": True},
    )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in logged and PASSWORD not in logged
    stored = db.scalars(select(Invitation.token_hash)).one()
    assert token not in stored
    for entry in db.scalars(select(AuditLog)):
        assert token not in str(entry.details)


def test_a_link_alone_does_not_open_an_existing_account(
    client: TestClient, world: World, outbox: MemoryEmailSender
) -> None:
    """Whoever intercepts the email still needs the account's password (or its session)."""
    client.post(
        "/team/invitations",
        json={"email": "zoya@chaiwala.in", "role": "analyst"},
        headers=world.headers("owner_a"),
    )
    token = link_token(outbox, "zoya@chaiwala.in", SUBJECT)
    new_account = client.post(
        "/invitations/accept",
        json={
            "token": token,
            "name": "Mallory",
            "password": "mallory-pass-9876",
            "accept_terms": True,
        },
    )
    assert new_account.status_code == 409
    wrong = client.post(
        "/invitations/accept-sign-in", json={"token": token, "password": "mallory-pass-9876"}
    )
    assert wrong.status_code == 401


def test_removed_members_refresh_token_is_dead(
    client: TestClient, db: DbSession, outbox: MemoryEmailSender
) -> None:
    owner = registered_user(client, outbox)  # Asha, Owner, signed in through the real flow
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    analyst = add_member(db, email="ravi@urbancart.in")
    client.cookies.clear()
    login = client.post("/auth/login", json={"email": "ravi@urbancart.in", "password": PASSWORD})
    assert login.json()["status"] == "signed_in"
    ravi_cookies = dict(client.cookies)
    membership = db.scalars(select(Membership).where(Membership.user_id == analyst.id)).one()
    assert client.delete(f"/team/members/{membership.id}", headers=headers).status_code == 204
    client.cookies.clear()
    for name, value in ravi_cookies.items():
        client.cookies.set(name, value)
    assert refresh(client).status_code == 401


def test_an_owner_cannot_reach_into_another_workspace_by_id(
    client: TestClient, db: DbSession, world: World
) -> None:
    """Belt and braces for the matrix: even after guessing a real id, nothing in org B changes."""
    target = db.scalars(select(Membership).where(Membership.user_id == world.analyst_b.id)).one()
    for method, body in (("PATCH", {"role": "owner"}), ("DELETE", None)):
        response = client.request(
            method, f"/team/members/{target.id}", json=body, headers=world.headers("owner_a")
        )
        assert response.status_code == 404
    db.expire_all()
    assert (target.role, target.status) == ("analyst", "active")
    assert db.scalars(select(AuditLog).where(AuditLog.org_id == world.org_b.id)).all() == []


def test_removed_member_can_be_added_to_another_workspace_independently(
    client: TestClient, db: DbSession, world: World
) -> None:
    add_member(db, world.org_b.id, email="ravi@urbancart.in", role="viewer")
    member = db.scalars(
        select(Membership).where(
            Membership.user_id == world.analyst_a.id, Membership.org_id == world.org_a.id
        )
    ).one()
    client.delete(f"/team/members/{member.id}", headers=world.headers("owner_a"))
    other = db.scalars(
        select(Membership).where(
            Membership.user_id == world.analyst_a.id, Membership.org_id == world.org_b.id
        )
    ).one()
    db.expire_all()
    assert other.status == "active"
