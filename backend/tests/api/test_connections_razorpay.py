"""Connecting Razorpay through the API (M11), against a fake Razorpay (no live calls). Background jobs run at once."""

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
from app.data.cache import DatasetCache
from app.dictionary import profiler
from app.models import AuditLog, Connection, Dataset, Secret, Snapshot
from tests.conftest import FakeClock
from tests.datasources import make_razorpay_connection
from tests.recorded_razorpay import KEY_ID, KEY_SECRET, T0, TEST_KEY_ID, FakeRazorpay, payment
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture(autouse=True)
def razorpay_data(fake_razorpay: FakeRazorpay) -> FakeRazorpay:
    fake_razorpay.tables["payments"] = [payment(i, created_at=T0 - i * 3_600) for i in range(40)]
    fake_razorpay.tables["payments"][3].update(amount_refunded=50_000, refund_status="partial")
    fake_razorpay.tables["orders"] = [
        {
            "id": f"order_{i:06d}",
            "entity": "order",
            "amount": 100_000 + i,
            "amount_paid": 100_000 + i,
            "amount_due": 0,
            "currency": "INR",
            "receipt": f"r{i}",
            "status": "paid",
            "attempts": 1,
            "created_at": T0 - i * 3_600 - 60,
        }
        for i in range(40)
    ]
    fake_razorpay.tables["refunds"] = [
        {
            "id": "rfnd_1",
            "entity": "refund",
            "amount": 50_000,
            "currency": "INR",
            "payment_id": "pay_000003",
            "status": "processed",
            "speed_processed": "normal",
            "created_at": T0 - 3 * 3_600 + 600,
        }
    ]
    fake_razorpay.tables["settlements"] = [
        {
            "id": "setl_1",
            "entity": "settlement",
            "amount": 3_000_000,
            "status": "processed",
            "fees": 80_000,
            "tax": 14_400,
            "utr": "UTR0001",
            "created_at": T0 - 3_600,
        }
    ]
    return fake_razorpay


def add(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> Any:
    payload = {"key_id": KEY_ID, "key_secret": KEY_SECRET} | body
    return client.post("/connections/razorpay", headers=world.headers(who), json=payload)


def detail(client: TestClient, world: World, connection_id: str, who: str = "owner_a") -> Any:
    response = client.get(f"/connections/{connection_id}", headers=world.headers(who))
    assert response.status_code == 200, response.text
    return response.json()


class TestConnect:
    def test_a_key_becomes_a_connection_with_four_tables(
        self, client: TestClient, world: World
    ) -> None:
        response = add(client, world)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["type"] == "razorpay" and body["name"] == "Razorpay"
        assert body["razorpay"] == {"key_id_masked": "rzp_live_AbCd••••", "mode": "live"}
        full = detail(client, world, body["id"])
        assert full["status"] == "active" and full["sync_state"] == "idle"
        assert [d["name"] for d in full["datasets"]] == [
            "payments",
            "orders",
            "refunds",
            "settlements",
        ]
        assert [d["row_count"] for d in full["datasets"]] == [40, 40, 1, 1]
        assert full["last_success_at"] is not None

    def test_a_test_mode_key_is_named_and_labelled(self, client: TestClient, world: World) -> None:
        body = add(client, world, key_id=TEST_KEY_ID).json()
        assert body["name"] == "Razorpay (test mode)" and body["razorpay"]["mode"] == "test"

    def test_a_chosen_name_is_kept(self, client: TestClient, world: World) -> None:
        assert add(client, world, name="Shop payments").json()["name"] == "Shop payments"

    def test_the_sources_list_offers_razorpay(self, client: TestClient, world: World) -> None:
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["razorpay"]["available"] is True

    @pytest.mark.parametrize(
        "body",
        [
            {"key_id": "hello"},
            {"key_id": "rzp_live_"},
            {"key_id": "sk_live_AbCdEf123456"},
            {"key_secret": "short"},
            {"key_secret": "has a space inside!"},
            {"unknown": "field"},
        ],
    )
    def test_malformed_keys_are_a_validation_error(
        self, client: TestClient, world: World, fake_razorpay: FakeRazorpay, body: dict[str, Any]
    ) -> None:
        response = add(client, world, **body)
        assert response.status_code == 422
        assert fake_razorpay.requests == []  # nothing was sent to Razorpay
        assert "has a space inside!" not in response.text

    def test_a_key_razorpay_refuses_is_not_saved(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        response = add(client, world, key_secret="wrongWrongWrong1")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "razorpay_refused"
        assert "key" in response.json()["error"]["message"]
        assert "wrongWrongWrong1" not in response.text
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []

    def test_razorpay_down_is_a_clear_refusal_and_nothing_is_saved(
        self, client: TestClient, world: World, db: DbSession, fake_razorpay: FakeRazorpay
    ) -> None:
        fake_razorpay.always = 503
        response = add(client, world)
        assert response.status_code == 422
        assert db.scalars(select(Connection)).all() == []

    def test_key_checks_are_rate_limited(self, client: TestClient, world: World) -> None:
        codes = [add(client, world, key_secret="wrongWrongWrong1").status_code for _ in range(21)]
        assert codes[:20] == [422] * 20 and codes[20] == 429

    def test_only_owners_connect(self, client: TestClient, world: World) -> None:
        assert add(client, world, "analyst_a").status_code == 403
        assert add(client, world, "viewer_a").status_code == 403


class TestSecrets:
    def test_the_secret_is_encrypted_and_never_returned_logged_or_audited(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        created = add(client, world)
        full = detail(client, world, created.json()["id"])
        everything = json.dumps([created.json(), full])
        assert KEY_SECRET not in everything
        assert KEY_ID not in everything  # only the masked id is shown
        assert KEY_SECRET not in caplog.text
        for entry in db.scalars(select(AuditLog)):
            assert KEY_SECRET not in json.dumps(entry.details)
        connection = db.scalars(select(Connection)).one()
        assert KEY_SECRET not in json.dumps(connection.config)
        assert connection.config == {"key_id": KEY_ID, "mode": "live"}
        row = db.scalars(select(Secret)).one()
        assert row.id == connection.secret_ref
        dump = db.execute(text("select * from secrets")).all()
        assert KEY_SECRET not in repr(dump)

    def test_the_activity_log_names_the_connection_not_the_key(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        add(client, world)
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "connection.added")).one()
        assert entry.details == {"type": "razorpay", "name": "Razorpay", "mode": "live"}


class TestReading:
    def test_requests_are_reads_with_the_workspaces_key(
        self, client: TestClient, world: World, fake_razorpay: FakeRazorpay
    ) -> None:
        add(client, world)
        assert fake_razorpay.requests
        assert {r.method for r in fake_razorpay.requests} == {"GET"}

    def test_preview_shows_paise_as_stored_and_no_personal_data(
        self, client: TestClient, world: World
    ) -> None:
        created = add(client, world).json()
        full = detail(client, world, created["id"])
        dataset = full["datasets"][0]
        names = [c["name"] for c in dataset["columns"]]
        assert not {"email", "contact", "card_id", "vpa"} & set(names)
        preview = client.get(
            f"/connections/{created['id']}/datasets/{dataset['id']}/preview",
            headers=world.headers("owner_a"),
        ).json()
        assert "buyer@example.com" not in json.dumps(preview)
        amount = preview["rows"][0][names.index("amount")]
        assert amount == 100_000  # whole paise, not 1000.00

    def test_the_next_read_after_the_cache_expires_fetches_only_what_changed(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
        fake_razorpay: FakeRazorpay,
    ) -> None:
        created = add(client, world).json()
        datasets = {d.name: d for d in db.scalars(select(Dataset))}
        cache = DatasetCache(runtime, world.org_a.id)
        first = cache.get(datasets["payments"].id)
        assert first.source == "cache" and first.row_count == 40
        # a later payment arrives and an old one is refunded
        fake_razorpay.tables["payments"].append(payment(900, created_at=T0 + 600))
        fake_razorpay.tables["payments"][5].update(amount_refunded=100_005, refund_status="full")
        clock.advance(seconds=runtime.settings.cache_ttl_seconds + 5)
        fake_razorpay.requests.clear()
        second = cache.get(datasets["payments"].id)
        assert second.source == "incremental"
        assert second.row_count == 41 and second.frame["id"].is_unique
        refunded = second.frame.loc[second.frame["id"] == "pay_000005", "amount_refunded"].iloc[0]
        assert refunded == 100_005
        calls = fake_razorpay.calls("payments")
        # only the time since the newest row (less a 30-day look-back), never the whole year again
        assert min(int(c["from"]) for c in calls) >= T0 - 30 * 86_400
        assert connection_status(db, created["id"]) == "active"

    def test_refresh_now_reads_everything_again(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        clock: FakeClock,
        fake_razorpay: FakeRazorpay,
    ) -> None:
        created = add(client, world).json()
        fake_razorpay.tables["payments"].append(payment(901, created_at=T0 + 900))
        clock.advance(minutes=1)
        response = client.post(
            f"/connections/{created['id']}/refresh", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        full = detail(client, world, created["id"])
        assert full["datasets"][0]["row_count"] == 41


def connection_status(db: DbSession, connection_id: str) -> str:
    db.expire_all()
    return str(db.scalars(select(Connection.status).where(Connection.id == connection_id)).one())


class TestKeyProblems:
    def test_a_revoked_key_needs_a_new_one_and_a_new_one_fixes_it(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        clock: FakeClock,
        fake_razorpay: FakeRazorpay,
    ) -> None:
        created = add(client, world).json()
        old_secret = db.scalars(select(Connection.secret_ref)).one()
        fake_razorpay.valid = ()  # Razorpay revoked the key
        tested = client.post(
            f"/connections/{created['id']}/test", headers=world.headers("owner_a")
        ).json()
        assert tested["ok"] is False and "key" in tested["message"]
        assert {c["label"]: c["ok"] for c in tested["checks"]}["signed_in"] is False
        assert connection_status(db, created["id"]) == "needs_reauth"
        # a refresh fails the same way and keeps the status
        clock.advance(minutes=1)
        client.post(f"/connections/{created['id']}/refresh", headers=world.headers("owner_a"))
        full = detail(client, world, created["id"])
        assert full["status"] == "needs_reauth" and full["last_error"]
        # a wrong replacement is refused and changes nothing
        bad = client.put(
            f"/connections/{created['id']}/razorpay/key",
            headers=world.headers("owner_a"),
            json={"key_id": KEY_ID, "key_secret": "stillWrong12345"},
        )
        assert bad.status_code == 422
        assert connection_status(db, created["id"]) == "needs_reauth"
        # a working replacement
        fake_razorpay.valid = (("rzp_live_NewKey123456", "newSecretValue12"),)
        ok = client.put(
            f"/connections/{created['id']}/razorpay/key",
            headers=world.headers("owner_a"),
            json={"key_id": "rzp_live_NewKey123456", "key_secret": "newSecretValue12"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["razorpay"]["key_id_masked"] == "rzp_live_NewK••••"
        db.expire_all()
        connection = db.scalars(select(Connection)).one()
        assert connection.status == "active" and connection.last_error is None
        assert connection.secret_ref != old_secret
        assert db.get(Secret, old_secret) is None  # the old secret was deleted
        assert len(db.scalars(select(Secret)).all()) == 1
        assert "newSecretValue12" not in ok.text
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.reauthorized")
        ).one()
        assert "newSecretValue12" not in json.dumps(entry.details)

    def test_replacing_the_key_of_another_kind_of_connection_is_refused(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        from tests.datasources import make_file_connection

        file = make_file_connection(db, runtime, world.org_a.id)
        response = client.put(
            f"/connections/{file.id}/razorpay/key",
            headers=world.headers("owner_a"),
            json={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_razorpay"

    def test_a_paused_connection_is_not_read_when_its_key_is_replaced(
        self, client: TestClient, world: World, db: DbSession, fake_razorpay: FakeRazorpay
    ) -> None:
        created = add(client, world).json()
        client.patch(
            f"/connections/{created['id']}",
            headers=world.headers("owner_a"),
            json={"paused": True},
        )
        fake_razorpay.requests.clear()
        response = client.put(
            f"/connections/{created['id']}/razorpay/key",
            headers=world.headers("owner_a"),
            json={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        assert len(fake_razorpay.requests) == 1  # only the key check, no reading

    def test_a_key_that_works_but_a_table_that_fails_marks_the_error(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        fake_razorpay: FakeRazorpay,
    ) -> None:
        connection = make_razorpay_connection(db, runtime, world.org_a.id, sync=False)
        fake_razorpay.always = 503
        from app.connectors import service

        outcome = service.sync_connection(
            runtime, org_id=world.org_a.id, connection_id=connection.id
        )
        assert outcome.status == "error"
        db.expire_all()
        assert connection.last_error and "Razorpay" in connection.last_error


class TestRemoval:
    def test_removing_deletes_the_secret_and_the_cached_data(
        self, client: TestClient, world: World, db: DbSession, object_store: Any
    ) -> None:
        created = add(client, world).json()
        assert db.scalars(select(Snapshot)).all()
        response = client.delete(f"/connections/{created['id']}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(Snapshot)).all() == []
        assert object_store.stored_keys() == []

    def test_analysts_see_the_connection_but_never_a_key(
        self, client: TestClient, world: World
    ) -> None:
        created = add(client, world).json()
        listing = client.get("/connections", headers=world.headers("analyst_a"))
        assert listing.status_code == 200
        assert KEY_ID not in listing.text and KEY_SECRET not in listing.text
        assert detail(client, world, created["id"], "analyst_a")["razorpay"]["mode"] == "live"
        denied = client.put(
            f"/connections/{created['id']}/razorpay/key",
            headers=world.headers("analyst_a"),
            json={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        )
        assert denied.status_code == 403

    def test_another_workspace_cannot_see_or_replace_it(
        self, client: TestClient, world: World
    ) -> None:
        created = add(client, world).json()
        assert (
            client.get(
                f"/connections/{created['id']}", headers=world.headers("owner_b")
            ).status_code
            == 404
        )
        replaced = client.put(
            f"/connections/{created['id']}/razorpay/key",
            headers=world.headers("owner_b"),
            json={"key_id": KEY_ID, "key_secret": KEY_SECRET},
        )
        assert replaced.status_code == 404


class TestDictionary:
    def test_every_money_column_is_recognised_as_paise(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        add(client, world)
        cache = DatasetCache(runtime, world.org_a.id)
        found: dict[str, dict[str, Any]] = {}
        for dataset in db.scalars(select(Dataset)):
            cached = cache.get(dataset.id)
            profile = profiler.profile_frame(
                cached.frame, dataset.schema_json["columns"], name=dataset.name
            )
            found[dataset.name] = {c["name"]: c["money"] for c in profile["columns"]}
        expected = {
            "payments": ["amount", "amount_refunded", "fee", "tax"],
            "orders": ["amount", "amount_paid", "amount_due"],
            "refunds": ["amount"],
            "settlements": ["amount", "fees", "tax"],
        }
        for table, columns in expected.items():
            for column in columns:
                money = found[table][column]
                assert money is not None, (table, column)
                assert money["unit"] == "INR" and money["divide_by"] == 100, (table, column)
        # not money: counts and ids
        assert found["orders"]["attempts"] is None
        assert found["payments"]["id"] is None
