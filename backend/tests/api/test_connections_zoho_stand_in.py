"""The development stand-in for Zoho Books (M13): the consent page, the callback, the demo organisation."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.connectors.zoho_books import ZohoSetup
from app.connectors.zoho_stand_in import StandInZohoTransport
from tests.conftest import FakeClock
from tests.recorded_zoho import zoho_test_setup
from tests.tenancy import World, build_world


@pytest.fixture
def zoho_setup(fake_zoho: StandInZohoTransport) -> ZohoSetup:
    return zoho_test_setup(fake_zoho, mode="stand_in")


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def test_the_stand_in_flow_connects_the_demo_organisation(
    client: TestClient, world: World, fake_zoho: StandInZohoTransport
) -> None:
    sources = client.get("/connections", headers=world.headers("owner_a")).json()["sources"]
    assert sources["zoho_books"] == {"available": True, "stand_in": True, "region": "in"}
    started = client.post("/connections/zoho/start", headers=world.headers("owner_a"), json={})
    url = urlsplit(started.json()["authorization_url"])
    assert url.path == "/connections/zoho/stand-in"
    page = client.get(f"{url.path}?{url.query}")
    assert page.status_code == 200 and "ZohoBooks.invoices.READ" in page.text
    assert ".CREATE" not in page.text and ".ALL" not in page.text
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    decided = client.post(
        "/connections/zoho/stand-in",
        data={"state": query["state"], "scope": query["scope"], "decision": "allow"},
        follow_redirects=False,
    )
    assert decided.status_code == 303
    back = urlsplit(decided.headers["location"])
    assert back.path == "/connections/zoho/callback"
    params = {k: v[0] for k, v in parse_qs(back.query).items()}
    assert params["accounts-server"] == "https://accounts.zoho.in"
    returned = client.get(f"{back.path}?{back.query}", follow_redirects=False)
    fragment = {
        k: v[0] for k, v in parse_qs(urlsplit(returned.headers["location"]).fragment).items()
    }
    finished = client.post(
        "/connections/zoho/finish", headers=world.headers("owner_a"), json=fragment
    )
    assert finished.status_code == 200, finished.text
    connection = finished.json()["connection"]
    assert connection["zoho"]["organization_name"] == "UrbanCart"
    assert connection["datasets_count"] == 7 and connection["rows_total"] > 0
    assert fake_zoho.writes_received == 0


def test_rejecting_on_the_stand_in_page(client: TestClient, world: World) -> None:
    started = client.post("/connections/zoho/start", headers=world.headers("owner_a"), json={})
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    decided = client.post(
        "/connections/zoho/stand-in",
        data={"state": state, "decision": "deny"},
        follow_redirects=False,
    )
    back = urlsplit(decided.headers["location"])
    returned = client.get(f"{back.path}?{back.query}", follow_redirects=False)
    assert returned.headers["location"].endswith("/connections/zoho/return#error=access_denied")


def test_the_stand_in_page_escapes_what_it_shows(client: TestClient) -> None:
    page = client.get(
        "/connections/zoho/stand-in", params={"state": '"><script>', "scope": "<b>x</b>"}
    )
    assert "<script>" not in page.text and "<b>x</b>" not in page.text
