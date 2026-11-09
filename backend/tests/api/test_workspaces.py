"""Workspaces: the profile (``/org``), switching between workspaces, and ``permissions`` in ``/auth/me`` (M4)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Organization
from tests.authflow import PASSWORD, finish_with_email_code, login_start, refresh, registered_user
from tests.conftest import FakeClock
from tests.tenancy import World, add_member, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


class TestOrgProfile:
    def test_everyone_can_read_it(self, client: TestClient, world: World) -> None:
        for who in ("owner_a", "analyst_a", "viewer_a"):
            body = client.get("/org", headers=world.headers(who)).json()
            assert (body["name"], body["timezone"], body["currency"]) == (
                "UrbanCart",
                "Asia/Kolkata",
                "INR",
            )

    def test_owner_renames_and_changes_time_zone(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        response = client.patch(
            "/org",
            json={"name": "  UrbanCart   India ", "timezone": "Asia/Dubai"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 200
        assert (response.json()["name"], response.json()["timezone"]) == (
            "UrbanCart India",
            "Asia/Dubai",
        )
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "org.settings_changed")).one()
        assert entry.details == {
            "name": {"from": "UrbanCart", "to": "UrbanCart India"},
            "timezone": {"from": "Asia/Kolkata", "to": "Asia/Dubai"},
        }
        assert db.get(Organization, world.org_b.id).name == "Chaiwala Foods"  # type: ignore[union-attr]

    def test_no_change_is_not_logged(self, client: TestClient, db: DbSession, world: World) -> None:
        response = client.patch(
            "/org", json={"name": "UrbanCart"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        assert (
            db.scalars(select(AuditLog).where(AuditLog.action == "org.settings_changed")).all()
            == []
        )

    def test_validation(self, client: TestClient, world: World) -> None:
        headers = world.headers("owner_a")
        for body in ({"timezone": "IST"}, {"name": ""}, {"name": "   "}, {"plan": "byok"}):
            assert client.patch("/org", json=body, headers=headers).status_code == 422


class TestSwitching:
    def test_switch_to_another_workspace(
        self, client: TestClient, db: DbSession, world: World, settings: Settings, clock: FakeClock
    ) -> None:
        add_member(db, world.org_b.id, email="ravi@urbancart.in", role="viewer")
        old = world.headers("analyst_a")
        response = client.post(
            "/auth/switch-organization",
            json={"organization_id": str(world.org_b.id)},
            headers=old,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["organization"]["name"], body["role"]) == ("Chaiwala Foods", "viewer")
        assert "ask" not in body["permissions"]
        assert response.headers["cache-control"] == "no-store"
        new = {"Authorization": f"Bearer {body['access_token']}"}
        assert client.get("/org", headers=new).json()["name"] == "Chaiwala Foods"
        stale = client.get("/org", headers=old)
        assert stale.status_code == 401 and code(stale) == "token_expired"
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "auth.workspace_switched")
        ).one()
        assert entry.org_id == world.org_b.id

    def test_not_a_member_is_404(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/auth/switch-organization",
            json={"organization_id": str(world.org_b.id)},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}

    def test_needs_a_session_and_a_valid_body(self, client: TestClient, world: World) -> None:
        body = {"organization_id": str(world.org_b.id)}
        assert client.post("/auth/switch-organization", json=body).status_code == 401
        bad = client.post(
            "/auth/switch-organization",
            json={"organization_id": "x"},
            headers=world.headers("owner_a"),
        )
        assert bad.status_code == 422

    def test_refresh_follows_the_switch(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        """Switching in one tab: the refresh cookie (shared by all tabs) now opens the new workspace."""
        session = registered_user(client, outbox)
        second = Organization(name="Second Shop", monthly_question_limit=500)
        db.add(second)
        db.flush()
        add_member(db, second.id, email="asha@urbancart.in", role="analyst")
        response = client.post(
            "/auth/switch-organization",
            json={"organization_id": str(second.id)},
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )
        assert response.status_code == 200
        refreshed = refresh(client)
        assert refreshed.status_code == 200
        assert refreshed.json()["organization"]["name"] == "Second Shop"
        assert refreshed.json()["role"] == "analyst"

    def test_login_can_choose_the_workspace(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        add_member(db, world.org_b.id, email="ravi@urbancart.in", role="viewer")
        response = client.post(
            "/auth/login",
            json={
                "email": "ravi@urbancart.in",
                "password": PASSWORD,
                "organization_id": str(world.org_b.id),
            },
        )
        assert response.json()["organization"]["name"] == "Chaiwala Foods"


class TestPermissionsInMe:
    def test_each_role_gets_its_list(self, client: TestClient, world: World) -> None:
        owner = client.get("/auth/me", headers=world.headers("owner_a")).json()["permissions"]
        analyst = client.get("/auth/me", headers=world.headers("analyst_a")).json()["permissions"]
        viewer = client.get("/auth/me", headers=world.headers("viewer_a")).json()["permissions"]
        assert "members.manage" in owner and "audit.view_all" in owner
        assert "members.manage" not in analyst and "audit.view_own" in analyst
        assert "ask" in analyst
        assert "ask" not in viewer and "audit.view_own" not in viewer
        assert set(viewer) < set(analyst) < set(owner)

    def test_login_response_includes_them(
        self, client: TestClient, db: DbSession, world: World, settings: Settings, clock: FakeClock
    ) -> None:
        challenge = login_start(client, "kabir@chaiwala.in")
        body = finish_with_email_code(client, "kabir@chaiwala.in", challenge)
        assert "members.manage" in body["permissions"]
