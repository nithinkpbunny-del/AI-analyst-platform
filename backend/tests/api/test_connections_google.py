"""Connecting Google Sheets through the API (M6), against recorded Google answers (no live calls)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.models import AuditLog, Connection, Dataset, Secret
from tests.conftest import FakeClock
from tests.datasources import make_sheets_connection
from tests.recorded_google import (
    ACCESS_TOKEN,
    ACCOUNT,
    CLIENT_ID,
    DELETED_ID,
    NO_ACCESS_ID,
    REFRESH_TOKEN,
    SHEET_ID,
    RecordedGoogle,
)
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def start(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> dict[str, str]:
    response = client.post("/connections/google/start", headers=world.headers(who), json=body)
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorization_url"])
    return {k: v[0] for k, v in parse_qs(url.query).items()} | {
        "_base": f"{url.scheme}://{url.netloc}{url.path}"
    }


def finish(
    client: TestClient, world: World, state: str, code: str = "4/test-code", who: str = "owner_a"
) -> Any:
    return client.post(
        "/connections/google/finish",
        headers=world.headers(who),
        json={"state": state, "code": code},
    )


def connect(client: TestClient, world: World) -> dict[str, Any]:
    params = start(client, world)
    response = finish(client, world, params["state"])
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestConnect:
    def test_the_consent_request_is_read_only_with_pkce_and_offline_access(
        self, client: TestClient, world: World
    ) -> None:
        params = start(client, world)
        assert params["_base"] == "https://accounts.google.com/o/oauth2/v2/auth"
        assert params["client_id"] == CLIENT_ID
        assert params["redirect_uri"] == "http://localhost:8000/connections/google/callback"
        assert params["scope"].split() == [
            "openid",
            "email",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ]
        assert params["code_challenge_method"] == "S256"
        assert len(params["code_challenge"]) == 43
        assert params["access_type"] == "offline"
        assert len(params["state"]) >= 32

    def test_the_callback_hands_the_code_to_the_web_app_in_the_fragment(
        self, client: TestClient, world: World
    ) -> None:
        params = start(client, world)
        response = client.get(
            "/connections/google/callback",
            params={"state": params["state"], "code": "4/test-code"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith("http://localhost:5173/connections/google/return#")
        assert parse_qs(urlsplit(location).fragment) == {
            "state": [params["state"]],
            "code": ["4/test-code"],
        }
        assert response.headers["referrer-policy"] == "no-referrer"

    @pytest.mark.parametrize(
        ("query", "error"),
        [
            ({"state": "unknown-state-0000000000", "code": "x"}, "expired"),
            ({"error": "access_denied"}, "access_denied"),
        ],
    )
    def test_the_callback_reports_problems(
        self, client: TestClient, world: World, query: dict[str, str], error: str
    ) -> None:
        if "state" not in query:
            query = {**query, "state": start(client, world)["state"]}
        response = client.get("/connections/google/callback", params=query, follow_redirects=False)
        assert parse_qs(urlsplit(response.headers["location"]).fragment) == {"error": [error]}

    def test_finishing_creates_the_connection_and_returns_a_picker_token(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        body = connect(client, world)
        assert body["reconnected"] is False
        connection = body["connection"]
        assert connection["type"] == "google_sheets"
        assert connection["name"] == f"Google Sheets ({ACCOUNT})"
        assert connection["google"] == {"account_email": ACCOUNT, "spreadsheets": []}
        assert body["picker"] == {
            "access_token": ACCESS_TOKEN,
            "expires_in": 3599,
            "developer_key": "test-picker-key",
            "app_id": "123456789012",
            "stand_in": False,
            "stand_in_files": [],
        }
        row = db.get(Connection, connection["id"])
        assert row is not None and row.secret_ref is not None
        secret = db.get(Secret, row.secret_ref)
        assert secret is not None and REFRESH_TOKEN.encode() not in secret.ciphertext
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "connection.added")).one()
        assert entry.details == {"type": "google_sheets", "name": connection["name"]}

    def test_choosing_spreadsheets_makes_one_dataset_per_visible_tab(
        self, client: TestClient, world: World, db: DbSession, google_transport: RecordedGoogle
    ) -> None:
        connection = connect(client, world)["connection"]
        response = client.put(
            f"/connections/{connection['id']}/google/spreadsheets",
            headers=world.headers("owner_a"),
            json={"spreadsheet_ids": [SHEET_ID]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["google"]["spreadsheets"] == [
            {"id": SHEET_ID, "name": "UrbanCart targets 2026"}
        ]
        detail = client.get(
            f"/connections/{connection['id']}", headers=world.headers("owner_a")
        ).json()
        assert [d["name"] for d in detail["datasets"]] == [
            "UrbanCart targets 2026 · Targets",
            "UrbanCart targets 2026 · Notes",
        ]  # the hidden tab and the chart sheet are left out
        targets = detail["datasets"][0]
        assert targets["row_count"] == 12  # title row and "Total" row are not data
        columns = {c["name"]: c for c in targets["columns"]}
        assert columns["month"]["type"] == "date"
        assert columns["revenue_target_inr"]["unit"] == "INR"
        # Only reads went to Google (plus the token endpoint).
        assert {m for m, _ in google_transport.methods()} <= {"GET", "POST"}
        assert {u for m, u in google_transport.methods() if m == "POST"} == {
            "https://oauth2.googleapis.com/token"
        }
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.spreadsheets_changed")
        ).one()
        assert entry.details["added"] == ["UrbanCart targets 2026"]

    def test_removing_a_spreadsheet_removes_its_tabs(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_sheets_connection(db, runtime, world.org_a.id)
        assert db.scalars(select(Dataset)).all()
        response = client.put(
            f"/connections/{connection.id}/google/spreadsheets",
            headers=world.headers("owner_a"),
            json={"spreadsheet_ids": []},
        )
        assert response.status_code == 200
        db.expire_all()
        assert db.scalars(select(Dataset)).all() == []

    @pytest.mark.parametrize("spreadsheet_id", [NO_ACCESS_ID, DELETED_ID])
    def test_a_spreadsheet_the_app_cannot_read_is_refused(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        spreadsheet_id: str,
    ) -> None:
        connection = make_sheets_connection(db, runtime, world.org_a.id)
        response = client.put(
            f"/connections/{connection.id}/google/spreadsheets",
            headers=world.headers("owner_a"),
            json={"spreadsheet_ids": [spreadsheet_id]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "spreadsheet_unreadable"

    def test_a_picker_token_for_choosing_more(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_sheets_connection(db, runtime, world.org_a.id)
        response = client.post(
            f"/connections/{connection.id}/google/picker-token", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        assert response.json()["access_token"] == ACCESS_TOKEN

    def test_a_revoked_google_token_asks_to_sign_in_again(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_sheets_connection(
            db, runtime, world.org_a.id, refresh_token="1//revoked-refresh-token"
        )
        db.refresh(connection)
        assert connection.status == "needs_reauth"
        assert (
            connection.last_error
            == "Google stopped sharing data. Sign in to Google again to reconnect."
        )
        response = client.post(
            f"/connections/{connection.id}/google/picker-token", headers=world.headers("owner_a")
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "needs_reauth"
        tested = client.post(f"/connections/{connection.id}/test", headers=world.headers("owner_a"))
        assert tested.json()["ok"] is False

    def test_signing_in_again_replaces_the_token(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_sheets_connection(
            db, runtime, world.org_a.id, refresh_token="1//revoked-refresh-token"
        )
        old_secret = connection.secret_ref
        params = start(client, world, connection_id=str(connection.id))
        assert params["login_hint"] == ACCOUNT
        response = finish(client, world, params["state"])
        assert response.status_code == 200, response.text
        assert response.json()["reconnected"] is True
        db.expire_all()
        current = db.get(Connection, connection.id)
        assert current is not None
        assert current.status == "active"
        assert current.secret_ref != old_secret
        assert db.get(Secret, old_secret) is None
        assert len(db.scalars(select(Dataset)).all()) == 2  # read again after signing in

    def test_signing_in_again_with_another_account_is_refused(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        google_transport: RecordedGoogle,
    ) -> None:
        connection = make_sheets_connection(db, runtime, world.org_a.id)
        params = start(client, world, connection_id=str(connection.id))
        response = finish(client, world, params["state"], code="4/other-account-code")
        assert response.status_code == 409
        assert response.json()["error"]["message"] == (
            f"Sign in with {ACCOUNT}, the Google account this connection uses."
        )
        revoked = [c for c in google_transport.calls if c.url.endswith("/revoke")]
        assert revoked and revoked[0].form == {"token": "1//other-refresh-token"}


class TestRefused:
    def test_a_token_that_could_write_is_refused(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        params = start(client, world)
        response = finish(client, world, params["state"], code="4/write-scope-code")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "google_refused"
        assert "more access than it asks for" in response.json()["error"]["message"]
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []

    def test_without_the_spreadsheets_scope_nothing_is_connected(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        params = start(client, world)
        response = finish(client, world, params["state"], code="4/no-sheets-scope-code")
        assert response.status_code == 422
        assert "tick both boxes" in response.json()["error"]["message"]

    def test_a_used_or_unknown_state_is_refused(self, client: TestClient, world: World) -> None:
        params = start(client, world)
        assert finish(client, world, params["state"]).status_code == 200
        again = finish(client, world, params["state"])
        assert again.status_code == 400
        assert again.json()["error"]["code"] == "google_link_expired"

    def test_someone_else_cannot_finish_your_sign_in(
        self, client: TestClient, world: World, db: DbSession, settings: Settings, clock: FakeClock
    ) -> None:
        from tests.tenancy import add_member, token_for

        params = start(client, world)
        meera = add_member(
            db,
            world.org_a.id,
            email="meera@urbancart.in",
            role="owner",
            name="Meera",
            two_factor=True,
        )
        world.tokens["meera"] = token_for(db, settings, clock.now, meera, world.org_a.id)
        assert finish(client, world, params["state"], who="meera").status_code == 400
        # Another workspace's Owner neither.
        params = start(client, world)
        assert finish(client, world, params["state"], who="owner_b").status_code == 400

    def test_a_bad_code_is_refused(self, client: TestClient, world: World) -> None:
        params = start(client, world)
        response = finish(client, world, params["state"], code="4/made-up")
        assert response.status_code == 422

    def test_google_is_unavailable_without_credentials(
        self, client: TestClient, world: World
    ) -> None:
        from app.connectors.google_sheets import GoogleSheetsSetup

        client.app.state.connector_runtime.google = GoogleSheetsSetup(mode="off")  # type: ignore[attr-defined]
        response = client.post(
            "/connections/google/start", headers=world.headers("owner_a"), json={}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "google_unavailable"
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["google_sheets"] == {"available": False, "stand_in": False}
