"""Security of connections (BUILD_SPEC 6.3, 6.4, P2, P3): read-only at every layer, credentials never shown or
logged, uploaded files kept unchanged and stored under safe names, and tenant isolation of the new tables."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import openpyxl.xml
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.connectors.base import ReadOnlyViolation
from app.connectors.google_sheets import GoogleSheetsSetup
from app.connectors.registry import TYPE_LABELS, ConnectorRuntime, build_connector
from app.data.objects import MemoryObjectStore
from app.db.tenant import scope_to_org
from app.models import (
    AuditLog,
    Connection,
    ConnectionGrant,
    Dataset,
    FileVersion,
    Snapshot,
)
from app.security.redaction import Redactor
from tests.conftest import FakeClock
from tests.datasources import csv_bytes, make_file_connection, make_sheets_connection, upload
from tests.recorded_google import REFRESH_TOKEN, RecordedGoogle
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


class TestReadOnly:
    @pytest.mark.parametrize("kind", sorted(TYPE_LABELS))
    def test_no_connector_can_send_a_write(
        self, kind: str, db: DbSession, runtime: ConnectorRuntime, world: World
    ) -> None:
        """BUILD_SPEC 6.3 rule 6: try writes through every connector and see them blocked."""
        make = make_file_connection if kind == "file" else make_sheets_connection
        connection = make(db, runtime, world.org_a.id)
        scope_to_org(db, world.org_a.id)
        connector = build_connector(db, connection, runtime, timezone="Asia/Kolkata")
        http = getattr(connector, "_http", None)
        if http is None:
            # Files: the connector has no network client and no way to write. The stored original is unchanged
            # by reading (checked below).
            assert not any(
                name.startswith(("write", "put", "upload", "delete")) for name in dir(connector)
            )
            return
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with pytest.raises(ReadOnlyViolation):
                http.request(
                    method, "https://sheets.googleapis.com/v4/spreadsheets/x:batchUpdate", json={}
                )

    def test_the_uploaded_original_is_never_changed(
        self, client: TestClient, world: World, db: DbSession, object_store: MemoryObjectStore
    ) -> None:
        data = csv_bytes()
        created = upload(client, world.headers("owner_a"), data=data).json()
        version = db.scalars(select(FileVersion)).one()
        assert version.sha256 == hashlib.sha256(data).hexdigest()
        for _ in range(2):
            client.post(f"/connections/{created['id']}/refresh", headers=world.headers("owner_a"))
        assert object_store.get(version.storage_key) == data

    def test_openpyxl_parses_xml_with_defusedxml(self) -> None:
        assert openpyxl.xml.DEFUSEDXML is True


class TestStorage:
    @pytest.mark.parametrize(
        ("sent", "kept"),
        [
            ("../../etc/passwd.csv", "passwd.csv"),
            ("C:\\Users\\asha\\sales.csv", "sales.csv"),
        ],
    )
    def test_file_names_cannot_choose_where_files_are_stored(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        object_store: MemoryObjectStore,
        sent: str,
        kept: str,
    ) -> None:
        response = upload(client, world.headers("owner_a"), sent, csv_bytes())
        assert response.status_code == 201, response.text
        version = db.scalars(select(FileVersion)).one()
        assert version.original_name == kept
        assert version.storage_key == (
            f"orgs/{world.org_a.id}/files/{version.connection_id}/{version.id}"
        )
        assert all(key.startswith(f"orgs/{world.org_a.id}/") for key in object_store.stored_keys())

    def test_control_characters_are_removed_from_names(self) -> None:
        from app.api.connections import _clean_filename

        assert _clean_filename("sales\x00\x1b[31m\n.csv") == "sales[31m.csv"
        assert _clean_filename("") == "upload"
        assert len(_clean_filename("a" * 400 + ".csv")) == 255


class TestSecrets:
    def _connect(self, client: TestClient, world: World) -> Any:
        started = client.post(
            "/connections/google/start", headers=world.headers("owner_a"), json={}
        ).json()
        state = started["authorization_url"].split("state=")[1].split("&")[0]
        response = client.post(
            "/connections/google/finish",
            headers=world.headers("owner_a"),
            json={"state": state, "code": "4/test-code"},
        )
        assert response.status_code == 200
        return response

    def test_the_refresh_token_never_leaves_the_server(
        self, client: TestClient, world: World, db: DbSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        finished = self._connect(client, world)
        connection_id = finished.json()["connection"]["id"]
        texts = [
            finished.text,
            client.get("/connections", headers=world.headers("owner_a")).text,
            client.get(f"/connections/{connection_id}", headers=world.headers("owner_a")).text,
            client.get("/audit", headers=world.headers("owner_a")).text,
            caplog.text,
        ]
        for text in texts:
            assert REFRESH_TOKEN not in text
            assert "test-client-secret" not in text
            assert "4/test-code" not in text or text is finished.text
        for entry in db.scalars(select(AuditLog)):
            assert REFRESH_TOKEN not in str(entry.details)
        connection = db.get(Connection, connection_id)
        assert connection is not None and REFRESH_TOKEN not in str(connection.config)

    def test_only_owners_receive_the_picker_token(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_sheets_connection(db, runtime, world.org_a.id)
        for who in ("analyst_a", "viewer_a"):
            response = client.post(
                f"/connections/{connection.id}/google/picker-token", headers=world.headers(who)
            )
            assert response.status_code == 403
        detail = client.get(f"/connections/{connection.id}", headers=world.headers("analyst_a"))
        assert "ya29." not in detail.text

    @pytest.mark.parametrize(
        "secret",
        [
            "ya29.a0AfH6SMBx3FakeAccessTokenValue",
            "1//0gFakeRefreshTokenValue1234567890",
            "4/0AX4XfWhFakeAuthorizationCodeValue",
        ],
    )
    def test_google_tokens_are_redacted_from_logs(self, secret: str) -> None:
        assert secret not in Redactor([]).redact(f"Google said: {secret} (retrying)")


class TestIsolation:
    def test_a_dataset_must_belong_to_the_connection_in_the_path(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        mine = make_file_connection(db, runtime, world.org_a.id)
        theirs = make_file_connection(db, runtime, world.org_b.id)
        their_dataset = db.scalars(select(Dataset).where(Dataset.connection_id == theirs.id)).one()
        response = client.get(
            f"/connections/{mine.id}/datasets/{their_dataset.id}/preview",
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "model",
        [Connection, ConnectionGrant, Dataset, Snapshot, FileVersion],
        ids=lambda m: m.__tablename__,
    )
    def test_rls_hides_and_refuses_other_workspaces_rows(
        self,
        model: Any,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        session_factory: sessionmaker[DbSession],
    ) -> None:
        for org, user in ((world.org_a, world.analyst_a), (world.org_b, world.analyst_b)):
            connection = make_file_connection(db, runtime, org.id)
            db.add(ConnectionGrant(org_id=org.id, connection_id=connection.id, user_id=user.id))
        db.commit()
        with session_factory() as scoped:
            scope_to_org(scoped, world.org_a.id)
            rows = list(scoped.scalars(select(model)))  # no org filter at all
            assert rows and {r.org_id for r in rows} == {world.org_a.id}
            other = db.scalars(select(model).where(model.org_id == world.org_b.id)).first()
            assert other is not None
            row = scoped.get(model, other.id)
            assert row is None
        with session_factory() as scoped:
            scope_to_org(scoped, world.org_a.id)
            scoped.add(
                Connection(
                    org_id=world.org_b.id,
                    type="file",
                    name="sneaky",
                    status="active",
                    config={},
                    sync_state="idle",
                )
            )
            with pytest.raises((DBAPIError, ProgrammingError)):
                scoped.commit()


class TestStandInPages:
    def test_the_consent_stand_in_is_off_with_real_google(self, client: TestClient) -> None:
        assert client.get("/connections/google/stand-in").status_code == 404
        assert client.post("/connections/google/stand-in", content=b"").status_code == 404

    def test_the_stand_in_page_escapes_and_returns_a_code(self, client: TestClient) -> None:
        client.app.state.connector_runtime.google = GoogleSheetsSetup(  # type: ignore[attr-defined]
            mode="stand_in", client_id="stand-in-client", api_base_url="http://localhost:8000"
        )
        page = client.get(
            "/connections/google/stand-in",
            params={"state": '"><script>x</script>', "login_hint": "a@b.in"},
        )
        assert page.status_code == 200
        assert "<script>x</script>" not in page.text
        assert 'value="a@b.in"' in page.text
        allowed = client.post(
            "/connections/google/stand-in",
            data={"state": "s1", "email": "dev@urbancart.in", "decision": "allow"},
            follow_redirects=False,
        )
        assert allowed.status_code == 303
        assert allowed.headers["location"].startswith(
            "http://localhost:8000/connections/google/callback?state=s1&code=standin-code."
        )
        denied = client.post(
            "/connections/google/stand-in",
            data={"state": "s1", "decision": "deny"},
            follow_redirects=False,
        )
        assert denied.headers["location"].endswith("state=s1&error=access_denied")


def test_google_answers_in_tests_are_recorded_never_live(google_transport: RecordedGoogle) -> None:
    """No connector test can reach Google: the transport answers 404 for anything not recorded."""
    import httpx

    client = httpx.Client(transport=google_transport)
    assert client.get("https://sheets.googleapis.com/v4/spreadsheets/unknown").status_code == 404
