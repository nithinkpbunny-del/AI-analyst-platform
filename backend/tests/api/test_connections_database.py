"""Connecting a PostgreSQL or MySQL database through the API (M14). PostgreSQL is a real database in the test
container behind a "public" name; MySQL is the fake. Production rules apply: public addresses only, SSL required."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.models import AuditLog, Connection, Dataset, Secret, Snapshot
from tests.conftest import FakeClock
from tests.customer_db import READER, WRITER, FakeMySql
from tests.datasources import make_database_connection
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def body(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "engine": "postgresql",
        "host": "db.example.com",
        "database": "customer_demo",
        "username": READER[0],
        "password": READER[1],
    }
    return values | overrides


def add(client: TestClient, world: World, who: str = "owner_a", **overrides: Any) -> Any:
    return client.post("/connections/database", headers=world.headers(who), json=body(**overrides))


def connect(client: TestClient, world: World, **overrides: Any) -> dict[str, Any]:
    response = add(client, world, **overrides)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def choose(
    client: TestClient, world: World, connection_id: str, tables: list[str], who: str = "owner_a"
) -> Any:
    return client.put(
        f"/connections/{connection_id}/database/tables",
        headers=world.headers(who),
        json={"tables": tables},
    )


def detail(client: TestClient, world: World, connection_id: str, who: str = "owner_a") -> Any:
    response = client.get(f"/connections/{connection_id}", headers=world.headers(who))
    assert response.status_code == 200, response.text
    return response.json()


class TestAdd:
    def test_a_read_only_user_connects_and_nothing_is_read_yet(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)
        assert created["type"] == "sql_database"
        assert created["name"] == "PostgreSQL (customer_demo)"
        assert created["database"] == {
            "engine": "postgresql",
            "host": "db.example.com",
            "port": 5432,
            "database": "customer_demo",
            "username_masked": "m1••••",
            "ssl_mode": "require",
            "tables": [],
            "can_write": False,
            "write_reasons": [],
            "server_version": created["database"]["server_version"],
        }
        assert created["datasets_count"] == 0 and created["sync_state"] == "idle"

    def test_a_user_who_can_write_is_accepted_with_a_warning(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world, username=WRITER[0], password=WRITER[1])
        assert created["database"]["can_write"] is True
        assert "rows" in created["database"]["write_reasons"]

    @pytest.mark.parametrize(
        ("host", "words"),
        [
            ("localhost", "private or internal"),
            ("127.0.0.1", "private or internal"),
            ("10.0.0.7", "private or internal"),
            ("192.168.1.20", "private or internal"),
            ("169.254.169.254", "private or internal"),
            ("internal.example.com", "private or internal"),
            ("postgres", "could not be found"),
            ("nowhere.example.com", "could not be found"),
        ],
    )
    def test_private_internal_and_unknown_hosts_are_refused(
        self, client: TestClient, world: World, db: DbSession, host: str, words: str
    ) -> None:
        response = add(client, world, host=host)
        assert response.status_code == 422
        assert words in response.json()["error"]["message"]
        assert db.scalars(select(Connection)).all() == [] and db.scalars(select(Secret)).all() == []

    def test_ssl_cannot_be_turned_off_outside_development(
        self, client: TestClient, world: World
    ) -> None:
        response = add(client, world, ssl_mode="disable")
        assert response.status_code == 422
        assert "only in development" in response.json()["error"]["message"]

    def test_a_wrong_password_is_refused(self, client: TestClient, world: World) -> None:
        response = add(client, world, password="wrong-password")
        assert response.status_code == 422
        assert "user name or password" in response.json()["error"]["message"]

    def test_mysql_connects_through_the_same_rules(
        self, client: TestClient, world: World, fake_mysql: FakeMySql
    ) -> None:
        created = connect(
            client,
            world,
            engine="mysql",
            host="mysql.example.com",
            database="shop",
            username="ro_user",
            password="ro-pass",
        )
        assert created["database"]["port"] == 3306 and created["name"] == "MySQL (shop)"
        assert fake_mysql.connections[-1].address == "52.66.10.21"

    @pytest.mark.parametrize(
        "bad",
        [
            {"engine": "oracle"},
            {"host": ""},
            {"port": 0},
            {"password": ""},
            {"ssl_mode": "prefer"},
            {"host": "db.exa\nmple.com"},
            {"database": "x" * 200},
        ],
    )
    def test_validation(self, client: TestClient, world: World, bad: dict[str, Any]) -> None:
        assert add(client, world, **bad).status_code == 422

    def test_only_owners_add(self, client: TestClient, world: World) -> None:
        assert add(client, world, "analyst_a").status_code == 403
        assert add(client, world, "viewer_a").status_code == 403

    def test_checks_are_rate_limited(self, client: TestClient, world: World) -> None:
        codes = [add(client, world, host="internal.example.com").status_code for _ in range(21)]
        assert codes[:20] == [422] * 20 and codes[20] == 429


class TestSecrets:
    def test_the_password_is_encrypted_and_never_returned_logged_or_audited(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        created = connect(client, world)
        choose(client, world, created["id"], ["public.orders"])
        full = detail(client, world, created["id"])
        listing = client.get("/connections", headers=world.headers("owner_a")).text
        password = READER[1]
        assert password not in json.dumps([created, full]) + listing
        assert READER[0] not in json.dumps([created, full]) + listing  # the user is masked
        assert password not in caplog.text
        for entry in db.scalars(select(AuditLog)):
            assert password not in json.dumps(entry.details)
        connection = db.scalars(select(Connection)).one()
        assert password not in json.dumps(connection.config)
        assert password not in repr(db.execute(text("select * from secrets")).all())
        assert connection.secret_ref is not None
        assert (
            runtime.secrets.reveal(
                db,
                org_id=connection.org_id,
                secret_id=connection.secret_ref,
                purpose="database_password",
            )
            == password
        )
        added = db.scalars(select(AuditLog).where(AuditLog.action == "connection.added")).one()
        assert added.details == {
            "type": "sql_database",
            "name": "PostgreSQL (customer_demo)",
            "engine": "postgresql",
            "host": "db.example.com",
        }


class TestTables:
    def test_listing_shows_what_the_user_may_read(self, client: TestClient, world: World) -> None:
        created = connect(client, world)
        response = client.get(
            f"/connections/{created['id']}/database/tables", headers=world.headers("owner_a")
        )
        assert response.status_code == 200, response.text
        listed = response.json()
        assert [(t["ref"], t["name"], t["kind"], t["chosen"]) for t in listed["tables"]] == [
            ("public.monthly", "monthly", "view", False),
            ("public.orders", "orders", "table", False),
            ("public.visits", "visits", "table", False),
            ("sales.targets", "sales.targets", "table", False),
        ]
        assert listed["max_tables"] == 100

    def test_choosing_tables_reads_them(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        created = connect(client, world)
        response = choose(client, world, created["id"], ["public.orders", "sales.targets"])
        assert response.status_code == 200, response.text
        full = detail(client, world, created["id"])
        assert full["status"] == "active" and full["database"]["tables"] == [
            "public.orders",
            "sales.targets",
        ]
        assert [(d["name"], d["row_count"]) for d in full["datasets"]] == [
            ("orders", 500),
            ("sales.targets", 2),
        ]
        orders = next(d for d in full["datasets"] if d["name"] == "orders")
        assert "photo" not in [c["name"] for c in orders["columns"]]
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.tables_changed")
        ).one()
        assert entry.details == {
            "type": "sql_database",
            "name": "PostgreSQL (customer_demo)",
            "added": 2,
            "removed": 0,
        }
        # dropping a table removes its dataset
        choose(client, world, created["id"], ["sales.targets"])
        assert [d["name"] for d in detail(client, world, created["id"])["datasets"]] == [
            "sales.targets"
        ]

    def test_a_table_the_user_cannot_read_is_refused(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)
        response = choose(client, world, created["id"], ["public.secret_payroll"])
        assert response.status_code == 422
        assert "tables" in response.json()["error"]["fields"]

    def test_preview_and_a_later_incremental_read(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
    ) -> None:
        from app.data.cache import DatasetCache

        created = connect(client, world)
        choose(client, world, created["id"], ["public.orders"])
        dataset = db.scalars(select(Dataset)).one()
        preview = client.get(
            f"/connections/{created['id']}/datasets/{dataset.id}/preview",
            headers=world.headers("owner_a"),
        )
        assert preview.status_code == 200 and preview.json()["row_count"] == 500
        clock.advance(minutes=10)
        got = DatasetCache(runtime, world.org_a.id).get(dataset.id)
        assert got.source == "incremental" and got.row_count == 500

    def test_mysql_tables_are_read_too(
        self, client: TestClient, world: World, fake_mysql: FakeMySql
    ) -> None:
        created = connect(
            client,
            world,
            engine="mysql",
            host="mysql.example.com",
            database="shop",
            username="ro_user",
            password="ro-pass",
        )
        assert choose(client, world, created["id"], ["orders"]).status_code == 200
        full = detail(client, world, created["id"])
        assert [(d["name"], d["row_count"]) for d in full["datasets"]] == [("orders", 60)]
        assert not any(
            s.upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in fake_mysql.statements
        )


class TestCredentials:
    def test_replacing_the_password_keeps_the_tables(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_database_connection(db, runtime, world.org_a.id, password="old-and-wrong")
        old_secret = connection.secret_ref
        tested = client.post(f"/connections/{connection.id}/test", headers=world.headers("owner_a"))
        assert tested.json()["ok"] is False
        db.expire_all()
        assert db.get(Connection, connection.id).status == "needs_reauth"  # type: ignore[union-attr]
        response = client.put(
            f"/connections/{connection.id}/database/credentials",
            headers=world.headers("owner_a"),
            json=body(),
        )
        assert response.status_code == 200, response.text
        db.expire_all()
        fixed = db.get(Connection, connection.id)
        assert fixed is not None and fixed.status == "active"
        assert fixed.config["tables"] == ["public.orders"]
        assert fixed.secret_ref != old_secret and db.get(Secret, old_secret) is None
        assert db.scalars(select(Dataset)).one().row_count_estimate == 500

    def test_the_kind_of_database_cannot_change(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_database_connection(db, runtime, world.org_a.id, sync=False)
        response = client.put(
            f"/connections/{connection.id}/database/credentials",
            headers=world.headers("owner_a"),
            json=body(engine="mysql", host="mysql.example.com"),
        )
        assert response.status_code == 422 and "engine" in response.json()["error"]["fields"]

    def test_other_kinds_of_connection_are_refused(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        from tests.datasources import make_file_connection

        file = make_file_connection(db, runtime, world.org_a.id)
        for method, path, payload in (
            ("PUT", f"/connections/{file.id}/database/credentials", body()),
            ("GET", f"/connections/{file.id}/database/tables", None),
            ("PUT", f"/connections/{file.id}/database/tables", {"tables": []}),
        ):
            response = client.request(method, path, headers=world.headers("owner_a"), json=payload)
            assert (
                response.status_code == 409 and response.json()["error"]["code"] == "not_database"
            )


class TestAccess:
    def test_analysts_see_the_connection_but_not_the_controls(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_database_connection(db, runtime, world.org_a.id)
        seen = detail(client, world, str(connection.id), "analyst_a")
        assert seen["database"]["host"] == "db.example.com"
        headers = world.headers("analyst_a")
        assert (
            client.get(f"/connections/{connection.id}/database/tables", headers=headers).status_code
            == 403
        )
        assert choose(client, world, str(connection.id), [], "analyst_a").status_code == 403

    def test_another_workspace_gets_404(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_database_connection(db, runtime, world.org_a.id, sync=False)
        headers = world.headers("owner_b")
        assert client.get(f"/connections/{connection.id}", headers=headers).status_code == 404
        assert (
            client.get(f"/connections/{connection.id}/database/tables", headers=headers).status_code
            == 404
        )
        assert choose(client, world, str(connection.id), [], "owner_b").status_code == 404
        replaced = client.put(
            f"/connections/{connection.id}/database/credentials", headers=headers, json=body()
        )
        assert replaced.status_code == 404

    def test_removing_deletes_the_password_and_cached_rows(
        self, client: TestClient, world: World, db: DbSession, object_store: Any
    ) -> None:
        created = connect(client, world)
        choose(client, world, created["id"], ["public.orders"])
        assert db.scalars(select(Snapshot)).all()
        response = client.delete(f"/connections/{created['id']}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(Snapshot)).all() == []
        assert object_store.stored_keys() == []

    def test_the_sources_list_offers_databases_without_development_exceptions(
        self, client: TestClient, world: World
    ) -> None:
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["database"] == {
            "available": True,
            "ssl_off_allowed": False,
            "demo_hosts": [],
        }
