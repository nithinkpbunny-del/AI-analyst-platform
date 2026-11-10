"""New file versions never silently replace a working one (DECISIONS D58), and Owners can switch versions.

The human's live check found it: a one-line text file uploaded as a new version of a 28,540-row file became
"In use". Now a version without data rows is refused, and one whose structure differs waits for an Owner.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.connectors.service import NO_ROWS
from app.data.objects import MemoryObjectStore
from app.models import AuditLog, FileVersion
from tests.conftest import FakeClock
from tests.datasources import csv_bytes, make_sheets_connection, upload
from tests.tenancy import World, build_world

SAME_COLUMNS = (
    "Date,Outlet,City,Bills,Sales (₹),Pincode\n"
    '01/02/2026,Indiranagar,Bengaluru,130,"₹1,30,000",560038\n'
)
OTHER_COLUMNS = "Month,Region,Target\nJul 2026,South,720000\nAug 2026,North,650000\n"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def first(client: TestClient, world: World) -> dict[str, Any]:
    """A working file connection: version 1 with 3 rows."""
    created: dict[str, Any] = upload(client, world.headers("owner_a")).json()
    return created


def detail(client: TestClient, world: World, connection_id: str) -> dict[str, Any]:
    response = client.get(f"/connections/{connection_id}", headers=world.headers("owner_a"))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def new_version(client: TestClient, world: World, connection_id: str, name: str, text: str) -> None:
    response = upload(
        client, world.headers("owner_a"), name, csv_bytes(text), connection_id=connection_id
    )
    assert response.status_code == 200, response.text


def versions(body: dict[str, Any]) -> list[tuple[int, str, bool]]:
    return [(v["version"], v["status"], v["in_use"]) for v in body["versions"]]


def use(client: TestClient, world: World, connection_id: str, version_id: str) -> Any:
    return client.post(
        f"/connections/{connection_id}/versions/{version_id}/use", headers=world.headers("owner_a")
    )


class TestRefused:
    @pytest.mark.parametrize(
        "text",
        ["this is not a real csv\n", "Date,Outlet,City,Bills,Sales (₹),Pincode\n", "\n\n"],
        ids=["one line of text", "header only", "blank lines"],
    )
    def test_a_version_without_data_rows_never_replaces_the_working_one(
        self, client: TestClient, world: World, first: dict[str, Any], text: str
    ) -> None:
        new_version(client, world, first["id"], "broken.csv", text)
        body = detail(client, world, first["id"])
        assert versions(body) == [(2, "failed", False), (1, "ready", True)]
        assert body["versions"][0]["error"] == NO_ROWS
        assert body["file"]["version"] == 1
        assert body["file"]["latest_upload_failed"] is True
        assert body["file"]["latest_upload_version"] == 2
        assert body["status"] == "active"
        [dataset] = body["datasets"]
        assert dataset["row_count"] == 3  # the data did not change
        preview = client.get(
            f"/connections/{first['id']}/datasets/{dataset['id']}/preview",
            headers=world.headers("owner_a"),
        ).json()
        assert len(preview["rows"]) == 3

    def test_a_first_upload_without_data_rows_says_why(
        self, client: TestClient, world: World
    ) -> None:
        created = upload(
            client, world.headers("owner_a"), "broken.csv", csv_bytes("this is not a real csv\n")
        ).json()
        body = detail(client, world, created["id"])
        assert (body["status"], body["last_error"]) == ("error", NO_ROWS)
        assert versions(body) == [(1, "failed", False)]


class TestReview:
    def test_different_columns_wait_for_an_owner(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        new_version(client, world, first["id"], "targets.csv", OTHER_COLUMNS)
        body = detail(client, world, first["id"])
        assert versions(body) == [(2, "needs_confirmation", False), (1, "ready", True)]
        assert body["versions"][0]["changes"] == [
            {
                "kind": "columns_missing",
                "sheet": "outlet_sales",
                "columns": ["Date", "Outlet", "City", "Bills", "Sales (₹)", "Pincode"],
                "count": 6,
            }
        ]
        assert body["file"]["latest_upload_needs_review"] is True
        assert body["file"]["latest_upload_version"] == 2
        assert body["file"]["version"] == 1
        assert body["datasets"][0]["row_count"] == 3

    def test_a_changed_column_type_waits_for_an_owner(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        text = SAME_COLUMNS.replace('"₹1,30,000"', "about one lakh")
        new_version(client, world, first["id"], "sales_text.csv", text)
        body = detail(client, world, first["id"])
        assert body["versions"][0]["status"] == "needs_confirmation"
        assert {
            "kind": "type_changed",
            "sheet": "outlet_sales",
            "column": "Sales (₹)",
            "from": "number",
            "to": "text",
        } in body["versions"][0]["changes"]

    def test_the_same_columns_replace_the_data_at_once(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        new_version(client, world, first["id"], "feb.csv", SAME_COLUMNS)
        body = detail(client, world, first["id"])
        assert versions(body) == [(2, "ready", True), (1, "ready", False)]
        assert body["datasets"][0]["row_count"] == 1

    def test_an_owner_can_use_the_reviewed_version(
        self, client: TestClient, world: World, first: dict[str, Any], db: DbSession
    ) -> None:
        new_version(client, world, first["id"], "targets.csv", OTHER_COLUMNS)
        waiting = detail(client, world, first["id"])["versions"][0]
        response = use(client, world, first["id"], waiting["id"])
        assert response.status_code == 200, response.text
        body = detail(client, world, first["id"])
        assert versions(body) == [(2, "ready", True), (1, "ready", False)]
        assert body["versions"][0]["changes"] == []
        assert [c["name"] for c in body["datasets"][0]["columns"]] == ["month", "region", "target"]
        assert body["datasets"][0]["row_count"] == 2
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.version_used")
        ).one()
        assert entry.details == {"name": "outlet_sales", "version": 2, "file": "targets.csv"}


class TestSwitchBack:
    def test_an_owner_can_go_back_to_an_earlier_version_and_it_stays(
        self, client: TestClient, world: World, first: dict[str, Any], clock: FakeClock
    ) -> None:
        new_version(client, world, first["id"], "feb.csv", SAME_COLUMNS)
        one = detail(client, world, first["id"])["versions"][1]
        assert use(client, world, first["id"], one["id"]).status_code == 200
        body = detail(client, world, first["id"])
        assert versions(body) == [(2, "ready", False), (1, "ready", True)]
        assert body["file"]["version"] == 1
        assert body["datasets"][0]["row_count"] == 3
        # "Refresh now" reads the version in use, not the newest one.
        clock.advance(minutes=1)
        refresh = client.post(
            f"/connections/{first['id']}/refresh", headers=world.headers("owner_a")
        )
        assert refresh.status_code == 200
        assert detail(client, world, first["id"])["datasets"][0]["row_count"] == 3

    def test_the_version_in_use_cannot_be_used_again(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        current = detail(client, world, first["id"])["versions"][0]
        response = use(client, world, first["id"], current["id"])
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_in_use"

    def test_a_version_that_could_not_be_read_cannot_be_used(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        new_version(client, world, first["id"], "broken.csv", "this is not a real csv\n")
        failed = detail(client, world, first["id"])["versions"][0]
        response = use(client, world, first["id"], failed["id"])
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "version_unusable"


class TestDiscard:
    def test_discarding_deletes_the_version_and_its_file(
        self,
        client: TestClient,
        world: World,
        first: dict[str, Any],
        db: DbSession,
        object_store: MemoryObjectStore,
    ) -> None:
        new_version(client, world, first["id"], "targets.csv", OTHER_COLUMNS)
        waiting = detail(client, world, first["id"])["versions"][0]
        key = db.get(FileVersion, waiting["id"]).storage_key  # type: ignore[union-attr]
        response = client.delete(
            f"/connections/{first['id']}/versions/{waiting['id']}",
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 204
        assert not object_store.exists(key)
        body = detail(client, world, first["id"])
        assert versions(body) == [(1, "ready", True)]
        assert body["file"]["latest_upload_needs_review"] is False
        assert db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.version_discarded")
        ).one()

    def test_the_version_in_use_cannot_be_discarded(
        self, client: TestClient, world: World, first: dict[str, Any]
    ) -> None:
        current = detail(client, world, first["id"])["versions"][0]
        response = client.delete(
            f"/connections/{first['id']}/versions/{current['id']}",
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "version_in_use"


def test_google_sheets_connections_have_no_versions(
    client: TestClient,
    world: World,
    db: DbSession,
    runtime: ConnectorRuntime,
    first: dict[str, Any],
) -> None:
    sheets = make_sheets_connection(db, runtime, world.org_a.id)
    version_id = detail(client, world, first["id"])["versions"][0]["id"]
    response = use(client, world, str(sheets.id), version_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_a_file"
