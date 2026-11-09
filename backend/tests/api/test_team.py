"""``/team``: members, role changes, removal and invitations (BUILD_SPEC 6.1, 6.2, R15, M4 acceptance).

Role (403) and cross-tenant (404) cases for every route are in ``test_route_matrix.py``; this file covers the
behaviour: emails, rules about Owners, immediate removal, invitation links and the activity log.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Invitation, Membership, Session, User
from app.orgs.team import INVITES_PER_HOUR
from tests.authflow import link_token
from tests.conftest import FakeClock
from tests.tenancy import World, add_member, build_world, token_for

INVITE_SUBJECT = "Asha Rao invited you to join UrbanCart on Analyst"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def membership_id(db: DbSession, world: World, email: str) -> str:
    return str(
        db.scalars(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == world.org_a.id, User.email == email)
        ).one()
    )


def actions(db: DbSession, world: World) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.org_id == world.org_a.id)
            .order_by(AuditLog.at, AuditLog.id)
        )
    )


class TestMembers:
    def test_lists_active_members_with_roles_and_2fa(
        self, client: TestClient, world: World
    ) -> None:
        response = client.get("/team/members", headers=world.headers("owner_a"))
        assert response.status_code == 200
        members = response.json()["members"]
        assert [(m["email"], m["role"], m["two_factor_enabled"], m["is_you"]) for m in members] == [
            ("asha@urbancart.in", "owner", True, True),
            ("ravi@urbancart.in", "analyst", False, False),
            ("lata@urbancart.in", "viewer", False, False),
        ]

    def test_change_role_emails_and_logs(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        response = client.patch(
            f"/team/members/{target}", json={"role": "viewer"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"
        [email] = outbox.to("ravi@urbancart.in")
        assert email.subject == "Your role in UrbanCart changed"
        assert "Asha Rao made you a Viewer in UrbanCart" in email.text
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "team.role_changed")).one()
        assert entry.details == {"from": "analyst", "to": "viewer", "email": "ravi@urbancart.in"}
        assert entry.user_id == world.owner_a.id
        assert entry.target_id == str(world.analyst_a.id)

    def test_new_role_applies_to_the_next_request(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        assert client.get("/audit", headers=world.headers("analyst_a")).status_code == 200
        client.patch(
            f"/team/members/{target}", json={"role": "viewer"}, headers=world.headers("owner_a")
        )
        assert client.get("/audit", headers=world.headers("analyst_a")).status_code == 403

    def test_same_role_changes_nothing(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        response = client.patch(
            f"/team/members/{target}", json={"role": "analyst"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        assert outbox.to("ravi@urbancart.in") == []
        assert "team.role_changed" not in actions(db, world)

    def test_promoted_owner_must_turn_on_2fa_first(
        self,
        client: TestClient,
        db: DbSession,
        world: World,
        outbox: MemoryEmailSender,
    ) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        client.patch(
            f"/team/members/{target}", json={"role": "owner"}, headers=world.headers("owner_a")
        )
        assert "turn it on the next time" in outbox.to("ravi@urbancart.in")[0].text
        headers = world.headers("analyst_a")  # Ravi's existing session
        me = client.get("/auth/me", headers=headers).json()
        assert me["role"] == "owner"
        assert me["permissions"] == []
        assert me["two_factor"]["required"] is True
        response = client.get("/team/members", headers=headers)
        assert response.status_code == 403
        assert error_code(response) == "two_factor_required"

    def test_cannot_change_own_role(self, client: TestClient, db: DbSession, world: World) -> None:
        mine = membership_id(db, world, "asha@urbancart.in")
        response = client.patch(
            f"/team/members/{mine}", json={"role": "analyst"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 409
        assert error_code(response) == "cannot_change_own_role"

    def test_two_owners_can_demote_each_other_but_not_both(
        self, client: TestClient, db: DbSession, world: World, settings: Settings, clock: FakeClock
    ) -> None:
        meera = add_member(
            db, world.org_a.id, email="meera@urbancart.in", role="owner", two_factor=True
        )
        meera_headers = {
            "Authorization": f"Bearer {token_for(db, settings, clock.now, meera, world.org_a.id)}"
        }
        asha = membership_id(db, world, "asha@urbancart.in")
        response = client.patch(
            f"/team/members/{asha}", json={"role": "analyst"}, headers=meera_headers
        )
        assert response.status_code == 200
        # Asha is now an Analyst and can no longer manage the team.
        meera_id = membership_id(db, world, "meera@urbancart.in")
        response = client.patch(
            f"/team/members/{meera_id}", json={"role": "analyst"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 403


class TestRemove:
    def test_removal_ends_access_at_once(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        assert client.get("/auth/me", headers=world.headers("analyst_a")).status_code == 200
        response = client.delete(f"/team/members/{target}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        response = client.get("/auth/me", headers=world.headers("analyst_a"))
        assert response.status_code == 401
        db.expire_all()
        session = db.scalars(select(Session).where(Session.user_id == world.analyst_a.id)).one()
        assert session.revoked_at is not None
        assert session.revoke_reason == "access_removed"
        assert outbox.to("ravi@urbancart.in")[0].subject == "Your access to UrbanCart was removed"
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "team.member_removed")).one()
        assert entry.details["sessions_revoked"] == 1
        members = client.get("/team/members", headers=world.headers("owner_a")).json()["members"]
        assert "ravi@urbancart.in" not in [m["email"] for m in members]

    def test_removal_keeps_sessions_in_other_workspaces(
        self, client: TestClient, db: DbSession, world: World, settings: Settings, clock: FakeClock
    ) -> None:
        add_member(db, world.org_b.id, email="ravi@urbancart.in", role="viewer")
        other = token_for(db, settings, clock.now, world.analyst_a, world.org_b.id)
        target = membership_id(db, world, "ravi@urbancart.in")
        client.delete(f"/team/members/{target}", headers=world.headers("owner_a"))
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {other}"})
        assert me.status_code == 200
        assert me.json()["organization"]["name"] == "Chaiwala Foods"

    def test_cannot_remove_yourself(self, client: TestClient, db: DbSession, world: World) -> None:
        mine = membership_id(db, world, "asha@urbancart.in")
        response = client.delete(f"/team/members/{mine}", headers=world.headers("owner_a"))
        assert response.status_code == 409
        assert error_code(response) == "cannot_remove_self"

    def test_removed_twice_is_404(self, client: TestClient, db: DbSession, world: World) -> None:
        target = membership_id(db, world, "ravi@urbancart.in")
        client.delete(f"/team/members/{target}", headers=world.headers("owner_a"))
        response = client.delete(f"/team/members/{target}", headers=world.headers("owner_a"))
        assert response.status_code == 404


class TestInvite:
    def test_invite_emails_a_link_and_lists_it(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        response = client.post(
            "/team/invitations",
            json={"email": "dev@urbancart.in", "role": "analyst"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "dev@urbancart.in"
        assert body["invited_by_name"] == "Asha Rao"
        assert body["expired"] is False
        [email] = outbox.to("dev@urbancart.in")
        assert email.subject == INVITE_SUBJECT
        assert "as an Analyst" in email.text and "works for 7 days" in email.text
        token = link_token(outbox, "dev@urbancart.in", INVITE_SUBJECT)
        assert "/accept-invite#token=" in email.text
        stored = db.scalars(select(Invitation)).one()
        assert stored.token_hash != token  # only the hash is kept
        assert stored.expires_at == world_now(db, stored) + timedelta(days=7)
        listed = client.get("/team/invitations", headers=world.headers("owner_a")).json()
        assert [i["email"] for i in listed["invitations"]] == ["dev@urbancart.in"]
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "team.invite_sent")).one()
        assert entry.details == {"email": "dev@urbancart.in", "role": "analyst"}

    def test_cannot_invite_as_owner(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/team/invitations",
            json={"email": "dev@urbancart.in", "role": "owner"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 422

    def test_existing_member_cannot_be_invited(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/team/invitations",
            json={"email": "RAVI@urbancart.in", "role": "viewer"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 409
        assert error_code(response) == "already_member"

    def test_a_new_invite_replaces_the_old_link(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        for role in ("analyst", "viewer"):
            client.post(
                "/team/invitations",
                json={"email": "dev@urbancart.in", "role": role},
                headers=world.headers("owner_a"),
            )
        first, second = (
            link_token_n(outbox, "dev@urbancart.in", 0),
            link_token_n(outbox, "dev@urbancart.in", 1),
        )
        assert client.post("/invitations/preview", json={"token": first}).status_code == 400
        preview = client.post("/invitations/preview", json={"token": second})
        assert preview.json()["role"] == "viewer"
        listed = client.get("/team/invitations", headers=world.headers("owner_a")).json()
        assert len(listed["invitations"]) == 1

    def test_resend_gives_a_new_link(
        self, client: TestClient, world: World, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        created = client.post(
            "/team/invitations",
            json={"email": "dev@urbancart.in", "role": "analyst"},
            headers=world.headers("owner_a"),
        ).json()
        clock.advance(days=8)  # expired
        world_tokens_refresh(client, world)
        listed = client.get("/team/invitations", headers=world.headers("owner_a")).json()
        assert listed["invitations"][0]["expired"] is True
        response = client.post(
            f"/team/invitations/{created['id']}/resend", headers=world.headers("owner_a")
        )
        assert response.status_code == 200 and response.json()["expired"] is False
        old, new = (
            link_token_n(outbox, "dev@urbancart.in", 0),
            link_token_n(outbox, "dev@urbancart.in", 1),
        )
        assert client.post("/invitations/preview", json={"token": old}).status_code == 400
        assert client.post("/invitations/preview", json={"token": new}).status_code == 200

    def test_revoke_kills_the_link(
        self, client: TestClient, db: DbSession, world: World, outbox: MemoryEmailSender
    ) -> None:
        created = client.post(
            "/team/invitations",
            json={"email": "dev@urbancart.in", "role": "analyst"},
            headers=world.headers("owner_a"),
        ).json()
        token = link_token(outbox, "dev@urbancart.in", INVITE_SUBJECT)
        response = client.delete(
            f"/team/invitations/{created['id']}", headers=world.headers("owner_a")
        )
        assert response.status_code == 204
        assert client.post("/invitations/preview", json={"token": token}).status_code == 400
        assert "team.invite_revoked" in actions(db, world)
        again = client.delete(
            f"/team/invitations/{created['id']}", headers=world.headers("owner_a")
        )
        assert again.status_code == 404

    def test_invitations_are_rate_limited_per_workspace(
        self, client: TestClient, world: World
    ) -> None:
        for n in range(INVITES_PER_HOUR):
            response = client.post(
                "/team/invitations",
                json={"email": f"p{n}@urbancart.in", "role": "viewer"},
                headers=world.headers("owner_a"),
            )
            assert response.status_code == 201
        response = client.post(
            "/team/invitations",
            json={"email": "one-more@urbancart.in", "role": "viewer"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 429
        # Another workspace is not affected.
        other = client.post(
            "/team/invitations",
            json={"email": "dev@chaiwala.in", "role": "viewer"},
            headers=world.headers("owner_b"),
        )
        assert other.status_code == 201


# ----------------------------------------------------------------------------------------------- helpers
def link_token_n(outbox: MemoryEmailSender, email: str, index: int) -> str:
    messages = [m for m in outbox.to(email) if m.subject == INVITE_SUBJECT]
    match = re.search(r"#token=([A-Za-z0-9_\-]+)", messages[index].text)
    assert match
    return match.group(1)


def world_now(db: DbSession, invitation: Invitation) -> Any:
    entry = db.scalars(select(AuditLog).where(AuditLog.target_id == str(invitation.id))).first()
    assert entry is not None
    return entry.at


def world_tokens_refresh(client: TestClient, world: World) -> None:
    """After moving the clock a long way, give the Owner a fresh session (the old one timed out)."""
    db: DbSession = client.app.state.session_factory()  # type: ignore[attr-defined]
    try:
        owner = db.scalars(select(User).where(User.id == world.owner_a.id)).one()
        settings: Settings = client.app.state.settings  # type: ignore[attr-defined]
        now = client.app.state.clock()  # type: ignore[attr-defined]
        world.tokens["owner_a"] = token_for(db, settings, now, owner, world.org_a.id)
    finally:
        db.close()
