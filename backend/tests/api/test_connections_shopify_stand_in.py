"""The development stand-in for Shopify (M12): the consent page, a signed callback, a demo store. Also which mode
the server picks from its settings."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.shopify import ShopifySetup, default_setup, verify_callback
from app.connectors.shopify_stand_in import StandInShopifyTransport
from tests.conftest import FakeClock
from tests.recorded_shopify import SHOP, shopify_test_setup
from tests.tenancy import World, build_world


@pytest.fixture
def shopify_setup(fake_shopify: StandInShopifyTransport) -> ShopifySetup:
    return shopify_test_setup(fake_shopify, mode="stand_in")


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def test_the_stand_in_flow_connects_a_demo_store(
    client: TestClient, world: World, shopify_setup: ShopifySetup
) -> None:
    sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
    assert sources["shopify"] == {"available": True, "stand_in": True}
    started = client.post(
        "/connections/shopify/start", headers=world.headers("owner_a"), json={"shop": SHOP}
    ).json()
    url = urlsplit(started["authorization_url"])
    assert url.path == "/connections/shopify/stand-in"
    page = client.get(f"{url.path}?{url.query}")
    assert page.status_code == 200 and SHOP in page.text and "read_orders" in page.text
    assert "write_" not in page.text
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    decided = client.post(
        "/connections/shopify/stand-in",
        data={
            "state": query["state"],
            "shop": SHOP,
            "scope": query["scope"],
            "all_orders": "1",
            "decision": "allow",
        },
        follow_redirects=False,
    )
    assert decided.status_code == 303
    back = urlsplit(decided.headers["location"])
    assert back.path == "/connections/shopify/callback"
    params = {k: v[0] for k, v in parse_qs(back.query).items()}
    assert verify_callback(shopify_setup, params)  # signed like Shopify signs it
    returned = client.get(f"{back.path}?{back.query}", follow_redirects=False)
    fragment = {
        k: v[0] for k, v in parse_qs(urlsplit(returned.headers["location"]).fragment).items()
    }
    finished = client.post(
        "/connections/shopify/finish", headers=world.headers("owner_a"), json=fragment
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["connection"]["shopify"]["all_orders"] is True


def test_cancelling_on_the_stand_in_page(client: TestClient) -> None:
    decided = client.post(
        "/connections/shopify/stand-in",
        data={"state": "s", "shop": SHOP, "decision": "deny"},
        follow_redirects=False,
    )
    assert decided.headers["location"].endswith("/connections/shopify/return#error=access_denied")


def make_settings(**values: Any) -> Settings:
    return Settings(
        _env_file=None,
        master_key="test-master-key-0000000000000000000000",
        jwt_secret="test-jwt-secret-0000000000000000000000",
        **values,
    )


def test_modes_follow_the_settings() -> None:
    assert default_setup(make_settings(app_env="development")).mode == "stand_in"
    assert default_setup(make_settings(app_env="test")).mode == "off"
    real = default_setup(
        make_settings(app_env="test", shopify_api_key="key", shopify_api_secret="secret")
    )
    assert real.mode == "shopify" and real.transport is None
    assert real.scopes == ("read_orders", "read_products", "read_customers", "read_inventory")


def test_a_write_scope_in_the_settings_is_never_requested() -> None:
    setup = default_setup(
        make_settings(
            app_env="development", shopify_scopes="read_orders,write_orders read_all_orders"
        )
    )
    assert setup.scopes == ("read_orders", "read_all_orders")
