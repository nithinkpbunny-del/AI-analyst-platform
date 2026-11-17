"""Connections API (M6): uploads, versions, datasets, preview, rename/pause, refresh, test, removal and who sees
what. The route matrix (test_route_matrix.py) covers 401/403/404/422 for every route; this file covers behaviour.
Background jobs run inline (``create_app(inline_jobs=True)``)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors import file_formats
from app.connectors.registry import ConnectorRuntime
from app.data.objects import MemoryObjectStore
from app.models import AuditLog, Connection, FileVersion, Membership, Secret, Snapshot
from tests.conftest import FakeClock
from tests.datasources import XLSX_MIME, csv_bytes, make_file_connection, upload, xlsx_bytes
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def _detail(client: TestClient, world: World, connection_id: str, who: str = "owner_a") -> Any:
    response = client.get(f"/connections/{connection_id}", headers=world.headers(who))
    assert response.status_code == 200, response.text
    return response.json()


def _actions(db: DbSession) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.at, AuditLog.id))]


class TestUpload:
    def test_a_csv_becomes_a_connection_with_one_dataset(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        response = upload(client, world.headers("owner_a"))
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["type"] == "file"
        assert created["name"] == "outlet_sales"
        detail = _detail(client, world, created["id"])
        assert detail["sync_state"] == "idle"
        assert detail["status"] == "active"
        assert detail["file"]["version"] == 1
        assert detail["file"]["original_name"] == "outlet_sales.csv"
        [dataset] = detail["datasets"]
        assert dataset["name"] == "outlet_sales"
        assert dataset["row_count"] == 3  # title, blank line and the Total row are not data
        columns = {c["name"]: c for c in dataset["columns"]}
        assert columns["date"]["type"] == "date"
        assert columns["date"]["date_format"] == "dd/mm/yyyy"
        assert columns["sales_inr"]["type"] == "number"
        assert columns["sales_inr"]["unit"] == "INR"
        assert columns["pincode"]["type"] == "text"
        kinds = {q["kind"] for q in dataset["quality"]}
        assert "total_row_dropped" in kinds
        assert detail["datasets_count"] == 1
        assert detail["rows_total"] == 3
        assert "connection.added" in _actions(db)

    def test_an_excel_workbook_gives_one_dataset_per_sheet(
        self, client: TestClient, world: World
    ) -> None:
        response = upload(
            client, world.headers("owner_a"), "plan.xlsx", xlsx_bytes(), name="Plan 2026"
        )
        assert response.status_code == 201, response.text
        detail = _detail(client, world, response.json()["id"])
        assert detail["name"] == "Plan 2026"
        assert [d["name"] for d in detail["datasets"]] == ["Targets", "Budget"]
        budget = detail["datasets"][1]
        assert {c["name"]: c["type"] for c in budget["columns"]} == {
            "department": "text",
            "budget_inr": "integer",
        }

    def test_a_preview_shows_the_first_rows_typed(self, client: TestClient, world: World) -> None:
        created = upload(client, world.headers("owner_a")).json()
        dataset = _detail(client, world, created["id"])["datasets"][0]
        response = client.get(
            f"/connections/{created['id']}/datasets/{dataset['id']}/preview",
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["row_count"] == 3
        assert body["source"] == "cache"  # read by the upload's job moments ago
        assert body["rows"][0] == [
            "2026-01-05",
            "Indiranagar",
            "Bengaluru",
            120,
            120500.0,
            "560038",
        ]
        assert body["rows"][2][4] is None  # "N/A" is empty, not zero

    def test_a_new_version_replaces_the_data(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        # Same columns as the first upload, new rows: it replaces the data at once.
        newer = csv_bytes(
            "Date,Outlet,City,Bills,Sales (₹),Pincode\n"
            '01/02/2026,Indiranagar,Bengaluru,130,"₹1,30,000",560038\n'
            '02/02/2026,Andheri,Mumbai,90,"₹90,000",400053\n'
        )
        response = upload(
            client, world.headers("owner_a"), "sales_feb.csv", newer, connection_id=created["id"]
        )
        assert response.status_code == 200, response.text
        detail = _detail(client, world, created["id"])
        assert detail["file"]["version"] == 2
        assert detail["file"]["original_name"] == "sales_feb.csv"
        assert [v["version"] for v in detail["versions"]] == [2, 1]
        assert detail["versions"][0]["in_use"] is True
        [dataset] = detail["datasets"]
        assert dataset["row_count"] == 2
        assert "connection.file_uploaded" in _actions(db)

    def test_an_unreadable_new_version_keeps_the_old_one(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()

        def unreadable(*_: object, **__: object) -> object:
            raise file_formats.FileFormatError("This workbook is damaged and cannot be opened.")

        # Passes the quick check at upload time, then cannot be read by the background job.
        monkeypatch.setattr(file_formats, "rows", unreadable)
        response = upload(
            client, world.headers("owner_a"), "march.csv", csv_bytes(), connection_id=created["id"]
        )
        assert response.status_code == 200
        monkeypatch.undo()
        detail = _detail(client, world, created["id"])
        assert detail["status"] == "active"
        assert detail["file"]["version"] == 1  # still in use
        assert detail["file"]["latest_upload_failed"] is True
        assert "damaged" in detail["file"]["latest_upload_error"]
        assert [(v["version"], v["status"]) for v in detail["versions"]] == [
            (2, "failed"),
            (1, "ready"),
        ]
        assert detail["datasets"][0]["row_count"] == 3

    @pytest.mark.parametrize(
        ("filename", "data", "message"),
        [
            ("notes.pdf", b"%PDF-1.7", "Upload an Excel workbook (.xlsx) or a CSV file (.csv)."),
            ("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 64, "old Excel (.xls)"),
            ("fake.xlsx", b"just,some,text\n", "not a real .xlsx"),
            ("empty.csv", b"", "The file is empty."),
        ],
    )
    def test_a_file_that_cannot_be_read_is_refused_with_a_reason(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        filename: str,
        data: bytes,
        message: str,
    ) -> None:
        response = upload(client, world.headers("owner_a"), filename, data)
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "file_unreadable"
        assert message in body["fields"]["file"]
        assert db.scalars(select(Connection)).all() == []

    def test_a_file_over_the_limit_is_refused(
        self, client: TestClient, world: World, settings: Settings
    ) -> None:
        settings.upload_max_mb = 1
        response = upload(
            client, world.headers("owner_a"), "big.csv", b"a,b\n" + b"1,2\n" * 300_000
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"

    def test_no_file_is_a_validation_error(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/connections/files", headers=world.headers("owner_a"), data={"name": "x"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["fields"] == {"file": "Choose a file to upload."}

    def test_a_new_version_only_fits_a_file_connection(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        from tests.datasources import make_sheets_connection

        sheets = make_sheets_connection(db, runtime, world.org_a.id)
        response = upload(client, world.headers("owner_a"), connection_id=str(sheets.id))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_a_file"

    def test_xlsx_content_type_is_not_trusted(self, client: TestClient, world: World) -> None:
        response = client.post(
            "/connections/files",
            headers=world.headers("owner_a"),
            files={"file": ("sales.xlsx", b"PK\x03\x04not really a zip", XLSX_MIME)},
        )
        assert response.status_code == 422


class TestManage:
    def test_rename_pause_and_resume(self, client: TestClient, world: World, db: DbSession) -> None:
        created = upload(client, world.headers("owner_a")).json()
        url = f"/connections/{created['id']}"
        renamed = client.patch(
            url, headers=world.headers("owner_a"), json={"name": "  Outlet   sales "}
        )
        assert renamed.json()["name"] == "Outlet sales"
        paused = client.patch(url, headers=world.headers("owner_a"), json={"paused": True})
        assert paused.json()["status"] == "disabled"
        refresh = client.post(f"{url}/refresh", headers=world.headers("owner_a"))
        assert refresh.status_code == 409
        assert refresh.json()["error"]["code"] == "connection_paused"
        resumed = client.patch(url, headers=world.headers("owner_a"), json={"paused": False})
        assert resumed.json()["status"] == "active"
        actions = _actions(db)
        assert {"connection.renamed", "connection.paused", "connection.resumed"} <= set(actions)

    def test_refresh_reads_the_source_again(
        self, client: TestClient, world: World, clock: FakeClock, db: DbSession
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        first_id = db.scalars(select(Snapshot.id)).one()
        clock.advance(minutes=1)
        response = client.post(
            f"/connections/{created['id']}/refresh", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        db.expire_all()
        second = db.scalars(select(Snapshot)).one()  # the old snapshot is gone
        assert second.id != first_id
        assert second.fetched_at == clock.now

    def test_refresh_is_refused_while_a_read_is_running(
        self, client: TestClient, world: World, db: DbSession, clock: FakeClock
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        connection = db.get(Connection, created["id"])
        assert connection is not None
        connection.sync_state, connection.sync_requested_at = "running", clock.now
        db.commit()
        url = f"/connections/{created['id']}/refresh"
        assert client.post(url, headers=world.headers("owner_a")).status_code == 409
        # A read lost by a restarted worker (queued 20 minutes ago) may be started again.
        connection.sync_requested_at = clock.now - timedelta(minutes=20)
        db.commit()
        assert client.post(url, headers=world.headers("owner_a")).status_code == 200

    def test_test_says_what_it_checked(self, client: TestClient, world: World) -> None:
        created = upload(client, world.headers("owner_a")).json()
        response = client.post(
            f"/connections/{created['id']}/test", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert "version 1" in body["message"]
        assert {c["label"] for c in body["checks"]} == {"file_readable", "read_only"}

    def test_a_missing_file_fails_the_test_and_marks_the_connection(
        self, client: TestClient, world: World, object_store: MemoryObjectStore
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        object_store.objects.clear()
        body = client.post(
            f"/connections/{created['id']}/test", headers=world.headers("owner_a")
        ).json()
        assert body["ok"] is False
        assert "Upload it again" in body["message"]
        assert _detail(client, world, created["id"])["status"] == "error"

    def test_tests_are_rate_limited(self, client: TestClient, world: World) -> None:
        created = upload(client, world.headers("owner_a")).json()
        url = f"/connections/{created['id']}/test"
        codes = [client.post(url, headers=world.headers("owner_a")).status_code for _ in range(31)]
        assert codes[-1] == 429 and set(codes[:-1]) == {200}

    def test_removing_deletes_files_snapshots_and_rows(
        self, client: TestClient, world: World, db: DbSession, object_store: MemoryObjectStore
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        assert object_store.stored_keys(f"orgs/{world.org_a.id}/")  # the file and a snapshot
        response = client.delete(f"/connections/{created['id']}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        assert object_store.stored_keys() == []
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Snapshot)).all() == []
        assert db.scalars(select(FileVersion)).all() == []
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "connection.removed")).one()
        assert entry.details == {"type": "file", "name": "outlet_sales"}


class TestWhoSeesWhat:
    def test_analysts_see_connections_but_cannot_change_them(
        self, client: TestClient, world: World
    ) -> None:
        created = upload(client, world.headers("owner_a")).json()
        listed = client.get("/connections", headers=world.headers("analyst_a"))
        assert [c["id"] for c in listed.json()["connections"]] == [created["id"]]
        assert client.get("/connections", headers=world.headers("viewer_a")).status_code == 403
        rename = client.patch(
            f"/connections/{created['id']}", headers=world.headers("analyst_a"), json={"name": "x"}
        )
        assert rename.status_code == 403

    def test_access_can_be_limited_to_chosen_connections(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        first = make_file_connection(db, runtime, world.org_a.id, name="Sales")
        second = make_file_connection(db, runtime, world.org_a.id, name="Targets")
        ravi = db.scalars(select(Membership).where(Membership.user_id == world.analyst_a.id)).one()
        response = client.put(
            f"/connections/access/{ravi.id}",
            headers=world.headers("owner_a"),
            json={"access": "selected", "connection_ids": [str(first.id)]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["access"] == "selected"
        listed = client.get("/connections", headers=world.headers("analyst_a")).json()
        assert [c["name"] for c in listed["connections"]] == ["Sales"]
        assert (
            client.get(f"/connections/{second.id}", headers=world.headers("analyst_a")).status_code
            == 404
        )
        dataset = _detail(client, world, str(second.id))["datasets"][0]
        preview = client.get(
            f"/connections/{second.id}/datasets/{dataset['id']}/preview",
            headers=world.headers("analyst_a"),
        )
        assert preview.status_code == 404
        people = {
            p["name"]: p
            for p in client.get("/connections/access", headers=world.headers("owner_a")).json()[
                "people"
            ]
        }
        assert people["Ravi"]["connection_ids"] == [str(first.id)]
        assert people["Asha Rao"]["access"] == "owner"
        assert people["Lata"]["access"] == "all"
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.access_changed")
        ).one()
        assert entry.details["connections"] == ["Sales"]

    def test_removing_the_only_granted_connection_does_not_open_the_rest(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        """BUILD_SPEC's "no grants = all" would let a removed connection widen access (DECISIONS D50)."""
        only = make_file_connection(db, runtime, world.org_a.id, name="Sales")
        make_file_connection(db, runtime, world.org_a.id, name="Payroll")
        ravi = db.scalars(select(Membership).where(Membership.user_id == world.analyst_a.id)).one()
        client.put(
            f"/connections/access/{ravi.id}",
            headers=world.headers("owner_a"),
            json={"access": "selected", "connection_ids": [str(only.id)]},
        )
        client.delete(f"/connections/{only.id}", headers=world.headers("owner_a"))
        listed = client.get("/connections", headers=world.headers("analyst_a")).json()
        assert listed["connections"] == []

    def test_back_to_all_connections(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        make_file_connection(db, runtime, world.org_a.id, name="Sales")
        ravi = db.scalars(select(Membership).where(Membership.user_id == world.analyst_a.id)).one()
        url = f"/connections/access/{ravi.id}"
        client.put(
            url, headers=world.headers("owner_a"), json={"access": "selected", "connection_ids": []}
        )
        assert (
            client.get("/connections", headers=world.headers("analyst_a")).json()["connections"]
            == []
        )
        client.put(url, headers=world.headers("owner_a"), json={"access": "all"})
        assert (
            len(
                client.get("/connections", headers=world.headers("analyst_a")).json()["connections"]
            )
            == 1
        )

    def test_owners_always_see_everything(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        owner = db.scalars(select(Membership).where(Membership.user_id == world.owner_a.id)).one()
        response = client.put(
            f"/connections/access/{owner.id}",
            headers=world.headers("owner_a"),
            json={"access": "selected", "connection_ids": []},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "owners_see_everything"

    def test_another_workspaces_connection_cannot_be_granted(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        theirs = make_file_connection(db, runtime, world.org_b.id, name="Chai sales")
        ravi = db.scalars(select(Membership).where(Membership.user_id == world.analyst_a.id)).one()
        response = client.put(
            f"/connections/access/{ravi.id}",
            headers=world.headers("owner_a"),
            json={"access": "selected", "connection_ids": [str(theirs.id)]},
        )
        assert response.status_code == 404

    def test_the_list_says_which_sources_can_be_added(
        self, client: TestClient, world: World
    ) -> None:
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources == {
            "upload_max_mb": 50,
            "google_sheets": {"available": True, "stand_in": False},
            "razorpay": {"available": True, "stand_in": False},
            "shopify": {"available": True, "stand_in": False},
        }


def test_nothing_secret_is_stored_in_clear(client: TestClient, world: World, db: DbSession) -> None:
    upload(client, world.headers("owner_a"))
    assert db.scalars(select(Secret)).all() == []  # files need no credentials
