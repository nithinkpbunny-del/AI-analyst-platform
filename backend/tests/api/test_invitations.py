"""``/invitations``: accepting an emailed invitation (BUILD_SPEC R15: "invitee sets their own login via link").

Public routes get success, validation and bad-link cases; ``accept-signed-in`` also 401 and wrong-account 403.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import REFRESH_COOKIE
from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Consent, Invitation, Membership, User
from tests.authflow import PASSWORD, finish_with_email_code, link_token
from tests.conftest import FakeClock
from tests.tenancy import World, build_world

SUBJECT = "Asha Rao invited you to join UrbanCart on Analyst"
NEW = "dev@urbancart.in"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def invite(
    client: TestClient, world: World, outbox: MemoryEmailSender, email: str, role: str = "analyst"
) -> str:
    response = client.post(
        "/team/invitations", json={"email": email, "role": role}, headers=world.headers("owner_a")
    )
    assert response.status_code == 201, response.text
    return link_token(outbox, email, SUBJECT)


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def accept_new(client: TestClient, token: str, **overrides: Any) -> Any:
    body = {"token": token, "name": "Dev Patel", "password": PASSWORD, "accept_terms": True}
    return client.post("/invitations/accept", json={**body, **overrides})


class TestPreview:
    def test_new_person(self, client: TestClient, world: World, outbox: MemoryEmailSender) -> None:
        token = invite(client, world, outbox, NEW)
        response = client.post("/invitations/preview", json={"token": token})
        assert response.status_code == 200
        body = response.json()
        assert body["organization_name"] == "UrbanCart"
        assert body["invited_by_name"] == "Asha Rao"
        assert (body["email"], body["role"]) == (NEW, "analyst")
        assert (body["account_exists"], body["has_password"]) == (False, False)
        assert response.headers["cache-control"] == "no-store"

    def test_existing_account(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        body = client.post("/invitations/preview", json={"token": token}).json()
        assert (body["account_exists"], body["has_password"]) == (True, True)

    def test_bad_expired_and_short_tokens(
        self, client: TestClient, world: World, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        token = invite(client, world, outbox, NEW)
        assert client.post("/invitations/preview", json={"token": "x" * 40}).status_code == 400
        assert client.post("/invitations/preview", json={"token": "short"}).status_code == 422
        assert client.post("/invitations/preview", json={}).status_code == 422
        clock.advance(days=7, seconds=1)
        response = client.post("/invitations/preview", json={"token": token})
        assert response.status_code == 400 and code(response) == "invalid_link"


class TestAcceptNewAccount:
    def test_creates_the_account_and_signs_in(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, NEW, role="viewer")
        response = accept_new(client, token)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "signed_in"
        assert body["organization"]["name"] == "UrbanCart"
        assert body["role"] == "viewer"
        assert "members.manage" not in body["permissions"]
        assert client.cookies.get(REFRESH_COOKIE)
        user = db.scalars(select(User).where(User.email == NEW)).one()
        assert user.email_verified_at is not None  # the link proved the address
        assert user.name == "Dev Patel"
        consent = db.scalars(select(Consent).where(Consent.user_id == user.id)).one()
        assert consent.org_id == world.org_a.id
        invitation = db.scalars(select(Invitation)).one()
        assert invitation.accepted_by == user.id and invitation.accepted_at is not None
        acts = list(
            db.scalars(
                select(AuditLog.action).where(AuditLog.user_id == user.id).order_by(AuditLog.at)
            )
        )
        assert {"consent.accepted", "team.invite_accepted", "auth.login.success"} <= set(acts)
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["role"] == "viewer"

    def test_link_works_once(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, NEW)
        assert accept_new(client, token).status_code == 200
        again = accept_new(client, token)
        assert again.status_code == 400 and code(again) == "invalid_link"

    def test_weak_password_keeps_the_link_usable(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, NEW)
        weak = accept_new(client, token, password="password123")
        assert weak.status_code == 422 and code(weak) == "weak_password"
        assert accept_new(client, token).status_code == 200

    def test_validation(self, client: TestClient, world: World, outbox: MemoryEmailSender) -> None:
        token = invite(client, world, outbox, NEW)
        response = accept_new(client, token, accept_terms=False, name="  ")
        assert response.status_code == 422
        assert set(response.json()["error"]["fields"]) == {"accept_terms", "name"}

    def test_existing_account_must_use_its_password(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        response = accept_new(client, token)
        assert response.status_code == 409 and code(response) == "account_exists"


class TestAcceptWithPassword:
    def test_joins_and_opens_the_new_workspace(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in", role="viewer")
        response = client.post(
            "/invitations/accept-sign-in", json={"token": token, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization"]["name"] == "UrbanCart"
        assert body["role"] == "viewer"
        assert sorted(m["organization_name"] for m in body["memberships"]) == [
            "Chaiwala Foods",
            "UrbanCart",
        ]

    def test_wrong_password_is_refused_and_logged(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        response = client.post(
            "/invitations/accept-sign-in", json={"token": token, "password": "wrong-password-1"}
        )
        assert response.status_code == 401 and code(response) == "invalid_credentials"
        failure = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.failure")).one()
        assert failure.details["reason"] == "bad_password"
        # Still open: the right password works.
        ok = client.post("/invitations/accept-sign-in", json={"token": token, "password": PASSWORD})
        assert ok.status_code == 200

    def test_repeated_wrong_passwords_lock(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        for _ in range(5):
            client.post(
                "/invitations/accept-sign-in", json={"token": token, "password": "wrong-password-1"}
            )
        response = client.post(
            "/invitations/accept-sign-in", json={"token": token, "password": PASSWORD}
        )
        assert response.status_code == 429

    def test_owner_elsewhere_still_needs_their_second_step(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "kabir@chaiwala.in")
        challenge = client.post(
            "/invitations/accept-sign-in", json={"token": token, "password": PASSWORD}
        ).json()
        assert challenge["status"] == "two_factor_required"
        session = finish_with_email_code(client, "kabir@chaiwala.in", challenge)
        assert session["organization"]["name"] == "UrbanCart"
        assert session["role"] == "analyst"

    def test_removed_member_can_rejoin_with_the_new_role(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        member = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        client.delete(f"/team/members/{member.id}", headers=world.headers("owner_a"))
        token = invite(client, world, outbox, "ravi@urbancart.in", role="viewer")
        response = client.post(
            "/invitations/accept-sign-in", json={"token": token, "password": PASSWORD}
        )
        assert response.status_code == 200
        db.expire_all()
        assert (member.status, member.role) == ("active", "viewer")
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "team.invite_accepted")).one()
        assert entry.details["rejoined"] is True

    def test_validation(self, client: TestClient) -> None:
        response = client.post("/invitations/accept-sign-in", json={"token": "x" * 40})
        assert response.status_code == 422


class TestAcceptSignedIn:
    def test_joins_and_switches_the_session(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        old = world.headers("analyst_b")
        response = client.post("/invitations/accept-signed-in", json={"token": token}, headers=old)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization"]["name"] == "UrbanCart"
        assert body["role"] == "analyst"
        new = {"Authorization": f"Bearer {body['access_token']}"}
        assert client.get("/auth/me", headers=new).json()["organization"]["name"] == "UrbanCart"
        stale = client.get("/auth/me", headers=old)
        assert stale.status_code == 401 and code(stale) == "token_expired"

    def test_someone_else_cannot_use_the_link(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        response = client.post(
            "/invitations/accept-signed-in", json={"token": token}, headers=world.headers("owner_b")
        )
        assert response.status_code == 403 and code(response) == "wrong_account"
        assert "zoya@chaiwala.in" in response.json()["error"]["message"]

    def test_needs_a_session(
        self, client: TestClient, world: World, outbox: MemoryEmailSender
    ) -> None:
        token = invite(client, world, outbox, "zoya@chaiwala.in")
        assert (
            client.post("/invitations/accept-signed-in", json={"token": token}).status_code == 401
        )

    def test_validation_and_bad_link(self, client: TestClient, world: World) -> None:
        headers = world.headers("analyst_b")
        assert (
            client.post("/invitations/accept-signed-in", json={}, headers=headers).status_code
            == 422
        )
        response = client.post(
            "/invitations/accept-signed-in", json={"token": "x" * 40}, headers=headers
        )
        assert response.status_code == 400
