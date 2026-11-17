"""Connecting Shopify through the API (M12), against the stand-in Shopify (no live calls). Jobs run at once."""

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
from app.connectors.registry import ConnectorRuntime
from app.connectors.shopify_stand_in import (
    REVOKED_TOKEN,
    STAND_IN_CLIENT_SECRET,
    StandInShopifyTransport,
    order_node,
    signed_callback,
)
from app.data.cache import DatasetCache
from app.dictionary import profiler
from app.models import AuditLog, Connection, Dataset, Secret, Snapshot
from tests.conftest import FakeClock
from tests.datasources import make_shopify_connection
from tests.recorded_shopify import SHOP, code
from tests.tenancy import World, build_world

TABLES = ["orders", "order_items", "customers", "products", "variants"]
MONEY = {
    "orders": [
        "total_price",
        "total_refunds",
        "total_discounts",
        "total_line_items_price",
        "total_shipping",
        "total_tax",
    ],
    "order_items": ["unit_price", "total_discount"],
    "customers": ["total_spent"],
    "products": ["price", "price_max"],
    "variants": ["price", "cost_per_item"],
}


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def start(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> Any:
    return client.post(
        "/connections/shopify/start",
        headers=world.headers(who),
        json=body if body else {"shop": "urbancart-demo"},
    )


def state_of(response: Any) -> str:
    return str(parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0])


def callback(client: TestClient, params: dict[str, str]) -> dict[str, str]:
    response = client.get("/connections/shopify/callback", params=params, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/connections/shopify/return#")
    assert response.headers["referrer-policy"] == "no-referrer"
    return {k: v[0] for k, v in parse_qs(urlsplit(location).fragment).items()}


def approve(
    client: TestClient,
    state: str,
    *,
    shop: str = SHOP,
    secret: str = STAND_IN_CLIENT_SECRET,
    **kw: str,
) -> dict[str, str]:
    issued = code(shop=shop, **kw) if kw else code(shop=shop)
    return callback(client, signed_callback(shop, state, issued, secret, 1_763_000_000))


def finish(client: TestClient, world: World, fragment: dict[str, str], who: str = "owner_a") -> Any:
    return client.post(
        "/connections/shopify/finish",
        headers=world.headers(who),
        json={"state": fragment["state"], "code": fragment["code"]},
    )


def connect(client: TestClient, world: World, **approval: str) -> Any:
    state = state_of(start(client, world))
    response = finish(client, world, approve(client, state, **approval))
    assert response.status_code == 200, response.text
    return response.json()


def detail(client: TestClient, world: World, connection_id: str, who: str = "owner_a") -> Any:
    response = client.get(f"/connections/{connection_id}", headers=world.headers(who))
    assert response.status_code == 200, response.text
    return response.json()


class TestStart:
    def test_the_approval_page_is_shopifys_with_read_scopes(
        self, client: TestClient, world: World
    ) -> None:
        response = start(client, world)
        assert response.status_code == 200, response.text
        url = urlsplit(response.json()["authorization_url"])
        assert url.netloc == SHOP and url.path == "/admin/oauth/authorize"
        query = parse_qs(url.query)
        assert query["scope"] == ["read_orders,read_products,read_customers,read_inventory"]
        assert query["redirect_uri"] == ["http://localhost:8000/connections/shopify/callback"]
        assert len(query["state"][0]) >= 32

    @pytest.mark.parametrize("shop", ["", "evil.com", "a b", "urbancart.myshopify.com.evil.com"])
    def test_a_wrong_store_address_is_a_field_error(
        self, client: TestClient, world: World, shop: str
    ) -> None:
        response = start(client, world, shop=shop)
        assert response.status_code == 422
        assert "shop" in response.json()["error"]["fields"]

    def test_the_sources_list_offers_shopify(self, client: TestClient, world: World) -> None:
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["shopify"] == {"available": True, "stand_in": False}

    def test_only_owners_start(self, client: TestClient, world: World) -> None:
        assert start(client, world, "analyst_a").status_code == 403
        assert start(client, world, "viewer_a").status_code == 403

    def test_starts_are_rate_limited(self, client: TestClient, world: World) -> None:
        codes = [start(client, world).status_code for _ in range(31)]
        assert codes[:30] == [200] * 30 and codes[30] == 429


class TestCallback:
    def test_a_signed_callback_passes_the_code_to_the_web_app(
        self, client: TestClient, world: World
    ) -> None:
        state = state_of(start(client, world))
        fragment = approve(client, state)
        assert fragment["state"] == state and fragment["code"]

    def test_a_forged_hmac_is_refused(self, client: TestClient, world: World) -> None:
        state = state_of(start(client, world))
        fragment = approve(client, state, secret="not-the-app-secret")
        assert fragment == {"error": "shopify_failed"}
        # and the code cannot be used afterwards: the state was never marked verified
        response = finish(client, world, {"state": state, "code": code()})
        assert response.status_code == 400

    def test_another_store_than_the_one_started_is_refused(
        self, client: TestClient, world: World
    ) -> None:
        state = state_of(start(client, world))
        assert approve(client, state, shop="other-shop.myshopify.com") == {
            "error": "shopify_failed"
        }

    def test_an_unknown_state_is_expired(self, client: TestClient) -> None:
        fragment = callback(
            client, signed_callback(SHOP, "x" * 40, code(), STAND_IN_CLIENT_SECRET, 1)
        )
        assert fragment == {"error": "expired"}


class TestFinish:
    def test_a_store_becomes_a_connection_with_five_tables(
        self, client: TestClient, world: World
    ) -> None:
        body = connect(client, world)
        assert body["reconnected"] is False
        created = body["connection"]
        assert created["type"] == "shopify" and created["name"] == "Shopify (UrbanCart (stand-in))"
        assert created["shopify"] == {
            "shop": SHOP,
            "shop_name": "UrbanCart (stand-in)",
            "currency": "INR",
            "all_orders": False,
        }
        full = detail(client, world, created["id"])
        assert full["status"] == "active" and full["sync_state"] == "idle"
        assert [d["name"] for d in full["datasets"]] == TABLES
        assert [d["row_count"] for d in full["datasets"]] == [40, 40, 10, 4, 4]

    def test_all_orders_access_is_shown(self, client: TestClient, world: World) -> None:
        body = connect(
            client,
            world,
            scopes="read_orders,read_products,read_customers,read_inventory,read_all_orders",
        )
        assert body["connection"]["shopify"]["all_orders"] is True

    def test_a_code_is_used_once(self, client: TestClient, world: World) -> None:
        state = state_of(start(client, world))
        fragment = approve(client, state)
        assert finish(client, world, fragment).status_code == 200
        again = finish(client, world, fragment)
        assert again.status_code == 400 and again.json()["error"]["code"] == "shopify_link_expired"

    def test_someone_else_cannot_finish_my_sign_in(self, client: TestClient, world: World) -> None:
        fragment = approve(client, state_of(start(client, world)))
        assert finish(client, world, fragment, "owner_b").status_code == 400

    def test_write_scopes_are_refused_and_nothing_is_saved(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        fragment = approve(
            client,
            state_of(start(client, world)),
            scopes="read_orders,read_products,read_customers,write_orders",
        )
        response = finish(client, world, fragment)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "shopify_refused"
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []

    def test_only_owners_finish(self, client: TestClient, world: World) -> None:
        fragment = approve(client, state_of(start(client, world)))
        assert finish(client, world, fragment, "analyst_a").status_code == 403


class TestSecrets:
    def test_the_token_is_encrypted_and_never_returned_logged_or_audited(
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
        token = connection_token(db, runtime, connection)
        assert token.startswith("shpat_")
        full = detail(client, world, body["connection"]["id"])
        listing = client.get("/connections", headers=world.headers("owner_a")).text
        assert token not in json.dumps([body, full]) + listing
        assert token not in caplog.text
        for entry in db.scalars(select(AuditLog)):
            assert token not in json.dumps(entry.details)
        assert token not in json.dumps(connection.config)
        assert set(connection.config) == {"shop", "shop_name", "currency", "scopes"}
        assert token not in repr(db.execute(text("select * from secrets")).all())
        added = db.scalars(select(AuditLog).where(AuditLog.action == "connection.added")).one()
        assert added.details == {
            "type": "shopify",
            "name": "Shopify (UrbanCart (stand-in))",
            "shop": SHOP,
        }


def connection_token(db: DbSession, runtime: ConnectorRuntime, connection: Connection) -> str:
    assert connection.secret_ref is not None
    return runtime.secrets.reveal(
        db,
        org_id=connection.org_id,
        secret_id=connection.secret_ref,
        purpose="shopify_access_token",
    )


class TestReading:
    def test_preview_shows_rupees_and_no_personal_data(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)["connection"]
        full = detail(client, world, created["id"])
        orders = full["datasets"][0]
        names = [c["name"] for c in orders["columns"]]
        assert not {"email", "phone", "first_name", "last_name", "address1", "zip"} & set(names)
        preview = client.get(
            f"/connections/{created['id']}/datasets/{orders['id']}/preview",
            headers=world.headers("owner_a"),
        ).json()
        price = [row[names.index("total_price")] for row in preview["rows"]]
        assert 1649.0 in price  # rupees as Shopify sends them, not paise

    def test_the_next_read_after_the_cache_expires_fetches_only_what_changed(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
        fake_shopify: StandInShopifyTransport,
    ) -> None:
        connect(client, world)
        orders = db.scalars(select(Dataset).where(Dataset.name == "orders")).one()
        cache = DatasetCache(runtime, world.org_a.id)
        assert cache.get(orders.id).row_count == 40
        # a new order arrives, and an old one is refunded
        data = fake_shopify.data
        data.orders.append(order_node(5000000900, created_at="2026-07-01T00:00:00Z"))
        refunded = data.orders[5]
        refunded["totalRefundedSet"] = {"shopMoney": {"amount": "1299.0", "currencyCode": "INR"}}
        refunded["updatedAt"] = "2026-07-01T01:00:00Z"
        clock.advance(seconds=runtime.settings.cache_ttl_seconds + 5)
        fake_shopify.requests.clear()
        second = cache.get(orders.id)
        assert second.source == "incremental"
        assert second.row_count == 41 and second.frame["id"].is_unique
        row = second.frame.loc[second.frame["id"] == "5000000006"].iloc[0]
        assert row["total_refunds"] == 1299.0
        searches = [
            p["variables"].get("q") for _, _, p in fake_shopify.requests if p.get("variables")
        ]
        assert any(q and "updated_at:>=" in q for q in searches)
        assert fake_shopify.bulk_started == 0

    def test_a_big_store_is_read_with_one_bulk_export_per_table(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        fake_shopify: StandInShopifyTransport,
    ) -> None:
        runtime_shopify = client.app.state.connector_runtime  # type: ignore[attr-defined]
        from dataclasses import replace

        runtime_shopify.shopify = replace(runtime_shopify.shopify, bulk_threshold=20)
        created = connect(client, world)["connection"]
        assert (
            fake_shopify.bulk_started == 2
        )  # orders and order_items (40 rows each); the rest are small
        full = detail(client, world, created["id"])
        assert [d["row_count"] for d in full["datasets"]] == [40, 40, 10, 4, 4]
        assert fake_shopify.writes_received == 0


class TestTokenProblems:
    def test_a_revoked_token_needs_a_new_sign_in_and_signing_in_again_fixes_it(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        clock: FakeClock,
    ) -> None:
        connection = make_shopify_connection(
            db, runtime, world.org_a.id, token=REVOKED_TOKEN, sync=False
        )
        old_secret = connection.secret_ref
        tested = client.post(
            f"/connections/{connection.id}/test", headers=world.headers("owner_a")
        ).json()
        assert tested["ok"] is False and "Sign in to Shopify again" in tested["message"]
        assert {c["label"]: c["ok"] for c in tested["checks"]}["signed_in"] is False
        db.expire_all()
        assert db.get(Connection, connection.id).status == "needs_reauth"  # type: ignore[union-attr]
        # sign in again: the start uses the connection's own store
        response = start(client, world, connection_id=str(connection.id))
        assert response.status_code == 200
        assert urlsplit(response.json()["authorization_url"]).netloc == SHOP
        again = finish(client, world, approve(client, state_of(response)))
        assert again.status_code == 200, again.text
        assert again.json()["reconnected"] is True
        db.expire_all()
        fixed = db.get(Connection, connection.id)
        assert fixed is not None and fixed.status == "active" and fixed.last_error is None
        assert fixed.secret_ref != old_secret and db.get(Secret, old_secret) is None
        assert len(db.scalars(select(Secret)).all()) == 1
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "connection.reauthorized")
        ).one()
        assert entry.details == {"type": "shopify", "name": "Shopify (UrbanCart)"}

    def test_signing_in_again_for_another_kind_of_connection_is_refused(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        from tests.datasources import make_file_connection

        file = make_file_connection(db, runtime, world.org_a.id)
        response = start(client, world, connection_id=str(file.id))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_shopify"

    def test_another_workspace_cannot_see_or_sign_in_again(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connection = make_shopify_connection(db, runtime, world.org_a.id, sync=False)
        assert (
            client.get(
                f"/connections/{connection.id}", headers=world.headers("owner_b")
            ).status_code
            == 404
        )
        assert start(client, world, "owner_b", connection_id=str(connection.id)).status_code == 404


class TestRemoval:
    def test_removing_deletes_the_token_and_the_cached_data(
        self, client: TestClient, world: World, db: DbSession, object_store: Any
    ) -> None:
        created = connect(client, world)["connection"]
        assert db.scalars(select(Snapshot)).all()
        response = client.delete(f"/connections/{created['id']}", headers=world.headers("owner_a"))
        assert response.status_code == 204
        assert db.scalars(select(Connection)).all() == []
        assert db.scalars(select(Secret)).all() == []
        assert db.scalars(select(Snapshot)).all() == []
        assert object_store.stored_keys() == []

    def test_analysts_see_the_connection_but_cannot_sign_in(
        self, client: TestClient, world: World
    ) -> None:
        created = connect(client, world)["connection"]
        assert detail(client, world, created["id"], "analyst_a")["shopify"]["shop"] == SHOP
        denied = start(client, world, "analyst_a", connection_id=created["id"])
        assert denied.status_code == 403


class TestDictionary:
    def test_every_money_column_is_rupees_and_nothing_is_divided(
        self, client: TestClient, world: World, db: DbSession, runtime: ConnectorRuntime
    ) -> None:
        connect(client, world)
        cache = DatasetCache(runtime, world.org_a.id)
        found: dict[str, dict[str, Any]] = {}
        for dataset in db.scalars(select(Dataset)):
            cached = cache.get(dataset.id)
            profile = profiler.profile_frame(
                cached.frame, dataset.schema_json["columns"], name=dataset.name
            )
            found[dataset.name] = {c["name"]: c["money"] for c in profile["columns"]}
        for table, columns in MONEY.items():
            for column in columns:
                money = found[table][column]
                assert money is not None, (table, column)
                assert money["unit"] == "INR" and money["divide_by"] is None, (table, column)
                assert "source_rupees" in money["evidence"], (table, column)
        assert found["orders"]["items_count"] is None
        assert found["customers"]["orders_count"] is None
        assert found["orders"]["id"] is None

    def test_the_uploaded_shopify_csv_keeps_working_next_to_the_connector(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        runtime: ConnectorRuntime,
        llm_factory: Any,
    ) -> None:
        from app.dictionary import service as dictionary_service
        from tests.datasources import make_file_connection

        csv = (
            b"id,created_at,customer_id,total_price,total_discounts,total_refunds,currency,shipping_state\n"
            b"5000000001,2026-06-01T10:00:00+05:30,7000000001,644.86,44.14,0.0,INR,Karnataka\n"
            b"5000000002,2026-06-02T10:00:00+05:30,7000000002,2322.40,76.60,0.0,INR,Bihar\n"
        )
        file = make_file_connection(
            db, runtime, world.org_a.id, filename="shopify_orders.csv", data=csv
        )
        connect(client, world)
        dictionary_service.run_draft(runtime, llm_factory, org_id=world.org_a.id)
        response = client.get("/dictionary", headers=world.headers("owner_a"))
        assert response.status_code == 200, response.text
        content = response.json()["draft"]["content"]
        tables = content["datasets"]
        # both sources are in the dictionary, side by side
        from_file = [k for k, v in tables.items() if v.get("connection_id") == str(file.id)]
        assert from_file, list(tables)
        shopify_orders = [
            k
            for k, v in tables.items()
            if v.get("connection_id") != str(file.id) and k.split(".")[-1] == "orders"
        ]
        assert shopify_orders, list(tables)
        price = tables[shopify_orders[0]]["columns"]["total_price"]
        assert price["unit"] == "INR" and not price.get("divide_by")
        uploaded = tables[from_file[0]]["columns"]["total_price"]
        assert uploaded["unit"] == "INR" and not uploaded.get("divide_by")
        # the uploaded file still reads and previews
        full = detail(client, world, str(file.id))
        assert full["status"] == "active" and full["datasets"][0]["row_count"] == 2


class TestModes:
    def test_the_stand_in_pages_do_not_exist_with_a_real_app(self, client: TestClient) -> None:
        assert client.get("/connections/shopify/stand-in?shop=x").status_code == 404
        assert client.post("/connections/shopify/stand-in", data={}).status_code == 404

    def test_without_an_app_shopify_is_off(
        self, client: TestClient, world: World, runtime: ConnectorRuntime
    ) -> None:
        from dataclasses import replace

        app_runtime = client.app.state.connector_runtime  # type: ignore[attr-defined]
        app_runtime.shopify = replace(app_runtime.shopify, mode="off")
        response = start(client, world)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "shopify_unavailable"
        sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
        assert sources["shopify"]["available"] is False
