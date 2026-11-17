"""Connecting Zoho Books through the API (M13), against the stand-in Zoho (no live calls). Jobs run at once."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.registry import ConnectorRuntime, zoho_quota
from app.connectors.zoho_stand_in import REVOKED_REFRESH_TOKEN, StandInZohoTransport
from app.data.cache import DatasetCache
from app.dictionary import profiler
from app.models import AuditLog, Connection, Dataset, Secret, Snapshot
from tests.conftest import FakeClock
from tests.datasources import make_zoho_connection
from tests.recorded_zoho import ACCOUNTS_SERVER, LABS_ORG_NAME, ORG_ID, OTHER_ORG_ID, code
from tests.tenancy import World, build_world

TABLES = [
    "invoices",
    "bills",
    "expenses",
    "contacts",
    "items",
    "chart of accounts",
    "profit and loss",
]


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def start(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> Any:
    return client.post("/connections/zoho/start", headers=world.headers(who), json=body)


def state_of(response: Any) -> str:
    return str(parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0])


def callback(client: TestClient, params: dict[str, str]) -> dict[str, str]:
    response = client.get("/connections/zoho/callback", params=params, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/connections/zoho/return#")
    assert response.headers["referrer-policy"] == "no-referrer"
    return {k: v[0] for k, v in parse_qs(urlsplit(location).fragment).items()}


def approve(
    client: TestClient,
    state: str,
    *,
    server: str | None = ACCOUNTS_SERVER,
    scope: str | None = None,
) -> dict[str, str]:
    params = {"state": state, "code": code(scope), "location": "in"}
    if server is not None:
        params["accounts-server"] = server
    return callback(client, params)


def finish(client: TestClient, world: World, fragment: dict[str, str], who: str = "owner_a") -> Any:
    return client.post(
        "/connections/zoho/finish",
        headers=world.headers(who),
        json={"state": fragment["state"], "code": fragment["code"]},
    )


def connect(client: TestClient, world: World) -> Any:
    response = finish(client, world, approve(client, state_of(start(client, world))))
    assert response.status_code == 200, response.text
    return response.json()


def detail(client: TestClient, world: World, connection_id: str, who: str = "owner_a") -> Any:
    response = client.get(f"/connections/{connection_id}", headers=world.headers(who))
    assert response.status_code == 200, response.text
    return response.json()


def token_of(db: DbSession, runtime: ConnectorRuntime, connection: Connection) -> str:
    assert connection.secret_ref is not None
    return runtime.secrets.reveal(
        db, org_id=connection.org_id, secret_id=connection.secret_ref, purpose="zoho_refresh_token"
    )


class TestStart:
    def test_the_consent_page_is_zohos_india_data_centre_with_read_scopes(
        self, client: TestClient, world: World
    ) -> None:
        response = start(client, world)
        assert response.status_code == 200, response.text
        url = urlsplit(response.json()["authorization_url"])
        assert url.netloc == "accounts.zoho.in" and url.path == "/oauth/v2/auth"
        query = parse_qs(url.query)
        assert all(s.endswith(".READ") for s in query["scope"][0].split(","))
        assert query["redirect_uri"] == ["http://localhost:8000/connections/zoho/callback"]
        assert len(query["state"][0]) >= 32

    def test_the_sources_list_offers_zoho(self, client: TestClient, world: World) -> None:
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["zoho_books"] == {"available": True, "stand_in": False, "region": "in"}

    def test_only_owners_start(self, client: TestClient, world: World) -> None:
        assert start(client, world, "analyst_a").status_code == 403
        assert start(client, world, "viewer_a").status_code == 403

    def test_starts_are_rate_limited(self, client: TestClient, world: World) -> None:
        codes = [start(client, world).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429


class TestCallback:
    def test_an_unknown_state_is_expired(self, client: TestClient) -> None:
        assert callback(client, {"state": "nope", "code": code()}) == {"error": "expired"}

    def test_a_refusal_on_zohos_page_comes_back_as_access_denied(
        self, client: TestClient, world: World
    ) -> None:
        state = state_of(start(client, world))
        assert callback(client, {"state": state, "error": "access_denied"}) == {
            "error": "access_denied"
        }

    @pytest.mark.parametrize(
        "server",
        ["https://accounts.zoho.in.evil.com", "https://evil.com", "http://accounts.zoho.in"],
    )
    def test_a_data_centre_that_is_not_zohos_is_refused(
        self, client: TestClient, world: World, server: str
    ) -> None:
        state = state_of(start(client, world))
        assert approve(client, state, server=server) == {"error": "zoho_failed"}

    def test_the_persons_own_data_centre_is_used(
        self, client: TestClient, world: World, fake_zoho: StandInZohoTransport, db: DbSession
    ) -> None:
        fragment = approve(
            client, state_of(start(client, world)), server="https://accounts.zoho.eu"
        )
        response = finish(client, world, fragment)
        assert response.status_code == 200, response.text
        assert response.json()["connection"]["zoho"]["region"] == "eu"
        connection = db.scalars(select(Connection)).one()
        assert connection.config["api_domain"] == "https://www.zohoapis.eu"

    def test_without_a_data_centre_the_configured_one_is_used(
        self, client: TestClient, world: World
    ) -> None:
        fragment = approve(client, state_of(start(client, world)), server=None)
        assert finish(client, world, fragment).json()["connection"]["zoho"]["region"] == "in"


class TestFinish:
    def test_connecting_reads_every_table_in_rupees(
        self, client: TestClient, world: World, fake_zoho: StandInZohoTransport
    ) -> None:
        body = connect(client, world)
        assert body["reconnected"] is False
        connection = body["connection"]
        assert connection["type"] == "zoho_books"
        assert connection["name"] == "Zoho Books (UrbanCart)"
        assert connection["zoho"]["organization_name"] == "UrbanCart"
        assert connection["zoho"]["organizations"] == [{"id": OTHER_ORG_ID, "name": LABS_ORG_NAME}]
        full = detail(client, world, connection["id"])
        assert full["status"] == "active" and full["sync_state"] == "idle"
        assert [d["name"] for d in full["datasets"]] == TABLES
        units = {
            c["name"]: c["unit"]
            for d in full["datasets"]
            if d["name"] == "invoices"
            for c in d["columns"]
        }
        assert units["total"] == "INR" and units["balance"] == "INR"
        assert full["zoho"]["calls_today"] > 24 and full["zoho"]["daily_limit"] == 1000
        assert fake_zoho.writes_received == 0

    def test_a_code_is_used_once(self, client: TestClient, world: World) -> None:
        fragment = approve(client, state_of(start(client, world)))
        assert finish(client, world, fragment).status_code == 200
        again = finish(client, world, fragment)
        assert again.status_code == 400
        assert again.json()["error"]["code"] == "zoho_link_expired"

    def test_only_the_person_who_started_finishes(self, client: TestClient, world: World) -> None:
        fragment = approve(client, state_of(start(client, world)))
        assert finish(client, world, fragment, "owner_b").status_code == 400

    def test_a_state_that_skipped_the_callback_is_refused(
        self, client: TestClient, world: World
    ) -> None:
        state = state_of(start(client, world))
        response = finish(client, world, {"state": state, "code": code()})
        assert response.status_code == 400

    def test_a_token_that_can_write_is_refused_and_nothing_is_stored(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        fragment = approve(
            client,
            state_of(start(client, world)),
            scope="ZohoBooks.invoices.READ,ZohoBooks.settings.READ,ZohoBooks.contacts.READ,"
            "ZohoBooks.invoices.CREATE",
        )
        response = finish(client, world, fragment)
        assert response.status_code == 422
        assert "only reading" in response.json()["error"]["message"]
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(Connection)).all() == []

    def test_an_account_without_books_is_explained(
        self, client: TestClient, world: World, fake_zoho: StandInZohoTransport
    ) -> None:
        fake_zoho.orgs = {}
        response = finish(client, world, approve(client, state_of(start(client, world))))
        assert response.status_code == 422
        assert "no Zoho Books organisation" in response.json()["error"]["message"]

    def test_only_owners_finish(self, client: TestClient, world: World) -> None:
        fragment = approve(client, state_of(start(client, world)))
        assert finish(client, world, fragment, "analyst_a").status_code == 403


class TestSecrets:
    def test_the_refresh_token_is_encrypted_and_never_returned_logged_or_audited(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        body = connect(client, world)
        connection = db.scalars(select(Connection)).one()
        token = token_of(db, runtime, connection)
        assert token.startswith("1000.standin-refresh.")
        full = detail(client, world, body["connection"]["id"])
        listing = client.get("/connections", headers=world.headers("owner_a")).text
        assert token not in json.dumps([body, full]) + listing
        assert "standin-access" not in json.dumps([body, full]) + listing
        assert token not in caplog.text and "standin-access" not in caplog.text
        for entry in db.scalars(select(AuditLog)):
            assert token not in json.dumps(entry.details)
        assert token not in json.dumps(connection.config)
        assert set(connection.config) == {
            "region",
            "api_domain",
            "organization_id",
            "organization_name",
            "currency",
            "organizations",
            "daily_limit",
        }
        assert token not in repr(db.execute(text("select * from secrets")).all())
        added = db.scalars(select(AuditLog).where(AuditLog.action == "connection.added")).one()
        assert added.details == {
            "type": "zoho_books",
            "name": "Zoho Books (UrbanCart)",
            "region": "in",
        }


class TestData:
    def test_preview_is_in_rupees_without_personal_columns(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)["connection"]
        full = detail(client, world, created["id"])
        invoices = next(d for d in full["datasets"] if d["name"] == "invoices")
        preview = client.get(
            f"/connections/{created['id']}/datasets/{invoices['id']}/preview",
            headers=world.headers("owner_a"),
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        names = [c["name"] for c in body["columns"]]
        assert "email" not in names and "@" not in json.dumps(body["rows"])
        total = names.index("total")
        assert isinstance(body["rows"][0][total], float)
        assert body["columns"][total]["unit"] == "INR"

    def test_the_dictionary_keeps_rupees_and_hides_names(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        connect(client, world)
        invoices = db.scalars(select(Dataset).where(Dataset.name == "invoices")).one()
        columns = {c["name"]: c for c in invoices.schema_json["columns"]}
        assert columns["total"]["stored_in"] == "rupees"
        profiled = invoices.profile_json or {}
        by_name = {c["name"]: c for c in profiled.get("columns", [])}
        if by_name:
            money = by_name["total"]["money"]
            assert money["unit"] == "INR" and not money["divide_by"]
            assert "source_rupees" in money["evidence"]
            assert by_name["customer_name"]["personal"] is True
            assert by_name["customer_name"]["samples"] == []
        assert profiler.PII_NAME.search("contact_name")

    def test_a_later_read_is_incremental(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
        fake_zoho: StandInZohoTransport,
    ) -> None:
        connect(client, world)
        invoices = db.scalars(select(Dataset).where(Dataset.name == "invoices")).one()
        first = db.scalars(select(Snapshot).where(Snapshot.dataset_id == invoices.id)).one()
        rows = first.row_count
        clock.advance(minutes=10)
        before = fake_zoho.calls
        got = DatasetCache(runtime, world.org_a.id).get(invoices.id)
        assert got.source == "incremental" and got.row_count == rows
        assert fake_zoho.calls - before == 1  # one page of changes

    def test_at_the_daily_limit_the_stored_copy_is_used(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
        fake_zoho: StandInZohoTransport,
    ) -> None:
        connect(client, world)
        expenses = db.scalars(select(Dataset).where(Dataset.name == "expenses")).one()
        quota = zoho_quota(runtime)
        quota.exhausted(ORG_ID)
        assert detail(client, world, str(expenses.connection_id))["zoho"]["calls_today"] == 1000
        clock.advance(minutes=10)
        before = fake_zoho.calls
        got = DatasetCache(runtime, world.org_a.id).get(expenses.id)
        assert got.source == "cache" and got.row_count == 315
        assert fake_zoho.calls == before  # nothing was asked of Zoho


class TestReports:
    def test_a_refused_report_leaves_the_connection_live(
        self, client: TestClient, world: World, fake_zoho: StandInZohoTransport
    ) -> None:
        fake_zoho.refuse_reports = True
        created = connect(client, world)["connection"]
        full = detail(client, world, created["id"])
        assert full["status"] == "active"
        by_name = {d["name"]: d for d in full["datasets"]}
        assert "profit and loss report" in by_name["profit and loss"]["last_error"]
        assert by_name["invoices"]["last_error"] is None and by_name["invoices"]["row_count"]


class TestOrganisation:
    def test_switching_reads_the_other_organisation_from_scratch(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
    ) -> None:
        created = connect(client, world)["connection"]
        response = client.put(
            f"/connections/{created['id']}/zoho/organization",
            headers=world.headers("owner_a"),
            json={"organization_id": OTHER_ORG_ID},
        )
        assert response.status_code == 200, response.text
        assert response.json()["zoho"]["organization_name"] == LABS_ORG_NAME
        full = detail(client, world, created["id"])
        invoices = next(d for d in full["datasets"] if d["name"] == "invoices")
        assert invoices["row_count"] == 1
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.organization_changed")
        ).one()
        assert entry.details["to"] == LABS_ORG_NAME and entry.details["from"] == "UrbanCart"

    def test_an_organisation_the_account_cannot_see_is_refused(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)["connection"]
        response = client.put(
            f"/connections/{created['id']}/zoho/organization",
            headers=world.headers("owner_a"),
            json={"organization_id": "123"},
        )
        assert response.status_code == 422
        assert "organization_id" in response.json()["error"]["fields"]

    def test_switching_a_non_zoho_connection_is_refused(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        from tests.datasources import make_file_connection

        file = make_file_connection(db, runtime, world.org_a.id)
        response = client.put(
            f"/connections/{file.id}/zoho/organization",
            headers=world.headers("owner_a"),
            json={"organization_id": OTHER_ORG_ID},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_zoho"


class TestTokenProblems:
    def test_a_revoked_token_needs_a_new_sign_in_and_signing_in_again_fixes_it(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_zoho_connection(
            db, runtime, world.org_a.id, token=REVOKED_REFRESH_TOKEN, sync=False
        )
        old_secret = connection.secret_ref
        tested = client.post(
            f"/connections/{connection.id}/test", headers=world.headers("owner_a")
        ).json()
        assert tested["ok"] is False and "Sign in to Zoho again" in tested["message"]
        db.expire_all()
        assert db.get(Connection, connection.id).status == "needs_reauth"  # type: ignore[union-attr]
        response = start(client, world, connection_id=str(connection.id))
        assert response.status_code == 200
        again = finish(client, world, approve(client, state_of(response)))
        assert again.status_code == 200, again.text
        assert again.json()["reconnected"] is True
        db.expire_all()
        fixed = db.get(Connection, connection.id)
        assert fixed is not None and fixed.status == "active" and fixed.last_error is None
        assert fixed.secret_ref != old_secret and db.get(Secret, old_secret) is None
        assert fixed.config["organization_id"] == ORG_ID
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.reauthorized")
        ).one()
        assert entry.details == {"type": "zoho_books", "name": "Zoho Books (UrbanCart)"}

    def test_another_workspace_cannot_see_or_sign_in_again(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_zoho_connection(db, runtime, world.org_a.id, sync=False)
        headers = world.headers("owner_b")
        assert client.get(f"/connections/{connection.id}", headers=headers).status_code == 404
        assert start(client, world, "owner_b", connection_id=str(connection.id)).status_code == 404
        moved = client.put(
            f"/connections/{connection.id}/zoho/organization",
            headers=headers,
            json={"organization_id": OTHER_ORG_ID},
        )
        assert moved.status_code == 404


class TestRemoval:
    def test_removing_revokes_the_token_and_deletes_the_cached_data(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        object_store: Any,
        fake_zoho: StandInZohoTransport,
    ) -> None:
        created = connect(client, world)["connection"]
        token = token_of(db, runtime, db.scalars(select(Connection)).one())
        assert db.scalars(select(Snapshot)).all()
        response = client.delete(f"/connections/{created['id']}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        assert token in fake_zoho.revoked
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(Snapshot)).all() == []
        assert object_store.stored_keys() == []

    def test_analysts_see_the_connection_but_cannot_change_it(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)["connection"]
        seen = detail(client, world, created["id"], "analyst_a")
        assert seen["zoho"]["organization_name"] == "UrbanCart"
        assert start(client, world, "analyst_a", connection_id=created["id"]).status_code == 403
        moved = client.put(
            f"/connections/{created['id']}/zoho/organization",
            headers=world.headers("analyst_a"),
            json={"organization_id": OTHER_ORG_ID},
        )
        assert moved.status_code == 403


class TestStandInPages:
    def test_the_stand_in_pages_are_off_with_a_real_zoho_setup(self, client: TestClient) -> None:
        assert client.get("/connections/zoho/stand-in").status_code == 404
        assert client.post("/connections/zoho/stand-in", data={}).status_code == 404
