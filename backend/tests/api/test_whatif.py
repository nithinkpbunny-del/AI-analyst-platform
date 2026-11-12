"""The What-if API (BUILD_SPEC 10.4, 6.2; M9) with the UrbanCart demo orders uploaded as a real connection.

A run is the agent's ``what_if`` tool without a model: nothing is metered and no question is counted. Covered:
options, scope values, a run, validation (422), 401/403, no data (409), per-person visibility, other workspaces,
the per-minute limit, and that no AI was used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.api import whatif as whatif_api
from app.config import Settings
from app.models import UsageEvent
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

ORDERS = Path(__file__).resolve().parents[3] / "eval" / "fixtures" / "shopify_orders.csv"
RUN = {
    "lever": "price",
    "change": 10,
    "metric": "sum_total_price",
    "scope": {"product_type": "Electronics"},
    "horizon": 3,
}


def orders_csv() -> bytes:
    """Every third order of the demo file: the same 21 months and shapes, a third of the rows (faster to read)."""
    lines = ORDERS.read_text(encoding="utf-8").splitlines()
    return ("\n".join([lines[0], *lines[1::3]]) + "\n").encode()


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "shopify_orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def post_run(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> Any:
    return client.post("/whatif/run", json={**RUN, **body}, headers=world.headers(who))


ROUTES = [
    ("GET", "/whatif/options", None),
    ("GET", "/whatif/values?table=orders&column=sales_channel", None),
    ("POST", "/whatif/run", RUN),
]


class TestAccess:
    @pytest.mark.parametrize(("method", "path", "body"), ROUTES)
    def test_signed_out_is_401(
        self, client: TestClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        assert client.request(method, path, json=body).status_code == 401

    @pytest.mark.parametrize(("method", "path", "body"), ROUTES)
    def test_viewers_are_403(
        self,
        client: TestClient,
        world: World,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        response = client.request(method, path, json=body, headers=world.headers("viewer_a"))
        assert response.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body"), ROUTES)
    def test_an_owner_without_two_step_verification_is_refused(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        settings: Settings,
        clock: FakeClock,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        from tests.tenancy import add_member, token_for

        nisha = add_member(db, world.org_a.id, email="nisha@urbancart.in", role="owner", name="N")
        token = token_for(db, settings, clock.now, nisha, world.org_a.id)
        response = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


class TestWithoutData:
    def test_options_are_empty_and_a_run_says_to_connect_data(
        self, client: TestClient, world: World
    ) -> None:
        options = client.get("/whatif/options", headers=world.headers("owner_a"))
        assert options.status_code == 200
        assert options.json() == {
            "metrics": [],
            "channels": None,
            "industry": options.json()["industry"],
            "max_horizon": 6,
        }
        run = post_run(client, world)
        assert run.status_code == 409 and run.json()["error"]["code"] == "no_dictionary"


class TestWithData:
    def test_options_list_what_can_be_simulated_for_the_metrics_of_the_orders(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        body = client.get("/whatif/options", headers=world.headers("owner_a")).json()
        metric = next(m for m in body["metrics"] if m["key"] == "sum_total_price")
        assert metric["levers"] == ["price", "discount"] and metric["unit"] == "INR"
        columns = {d["column"] for d in metric["dimensions"]}
        assert {"product_type", "shipping_region", "sales_channel"} <= columns
        assert body["max_horizon"] == 6 and body["channels"] is None  # no marketing file here

    def test_values_of_a_scope_column_come_from_the_data(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        response = client.get(
            "/whatif/values",
            params={"table": "shopify_orders", "column": "shipping_region"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 200
        assert sorted(response.json()["values"]) == ["East", "North", "South", "West"]

    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("shopify_orders", "total_price"),  # a measure is not a scope
            ("shopify_orders", "no_such_column"),
            ("no_such_table", "shipping_region"),
        ],
    )
    def test_values_only_for_dimensions_of_tables_that_exist(
        self, client: TestClient, world: World, orders: dict[str, Any], table: str, column: str
    ) -> None:
        response = client.get(
            "/whatif/values",
            params={"table": table, "column": column},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 404

    def test_a_run_returns_the_range_assumptions_confidence_and_sources(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        response = post_run(client, world)
        assert response.status_code == 200, response.text
        body = response.json()
        facts = body["facts"]
        assert facts["delta_low"] <= facts["delta_expected"] <= facts["delta_high"]
        assert facts["range"] == "80%" and facts["confidence"]["level"] in ("low", "medium", "high")
        assert facts["assumptions"][0]["key"] == "price_elasticity"
        assert len(body["rows"]) == 3 and "scenario_low" in body["columns"]
        assert body["tables"][0]["connection"] == orders["name"] and body["tables"][0]["fetched_at"]
        assert body["sql"]
        assert "what_if_assumptions" in body["notes"]

    def test_moving_a_slider_is_the_same_run_with_other_numbers(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        small = post_run(client, world, change=5).json()["facts"]
        large = post_run(client, world, change=20).json()["facts"]
        assert small["units_change_pct"] > large["units_change_pct"]

    def test_the_person_can_set_an_assumption(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        facts = post_run(client, world, assumptions={"price_elasticity": -3.0}).json()["facts"]
        first = facts["assumptions"][0]
        assert first["source"] == "you" and first["value"] == -3.0
        assert facts["delta_expected"] < 0

    def test_an_analyst_can_run_it_too(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        assert post_run(client, world, who="analyst_a").status_code == 200

    def test_nothing_is_metered_and_no_question_is_counted(
        self, client: TestClient, world: World, orders: dict[str, Any], db: DbSession
    ) -> None:
        before = db.scalar(select(func.count()).select_from(UsageEvent))
        assert post_run(client, world).status_code == 200
        assert db.scalar(select(func.count()).select_from(UsageEvent)) == before

    def test_another_workspace_sees_none_of_it(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        run = post_run(client, world, who="owner_b")
        assert run.status_code == 409 and run.json()["error"]["code"] == "no_dictionary"
        options = client.get("/whatif/options", headers=world.headers("owner_b")).json()
        assert options["metrics"] == []
        values = client.get(
            "/whatif/values",
            params={"table": "shopify_orders", "column": "shipping_region"},
            headers=world.headers("owner_b"),
        )
        assert values.status_code == 409


class TestValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {"lever": "stock"},
            {"change": "a lot"},
            {"horizon": 0},
            {"horizon": 7},
            {"scope": {"a": 1}},
            {"assumptions": {"price_elasticity": "x"}},
            {"extra": 1},
        ],
    )
    def test_malformed_bodies_are_422(
        self, client: TestClient, world: World, orders: dict[str, Any], body: dict[str, Any]
    ) -> None:
        assert post_run(client, world, **body).status_code == 422

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ({"change": 500}, "whatif_bad_change"),
            ({"assumptions": {"price_elasticity": 2}}, "whatif_bad_assumption"),
            ({"assumptions": {"discount_elasticity": -1}}, "whatif_bad_assumption"),
            ({"metric": "nope"}, "whatif_unknown_metric"),
            (
                {"lever": "marketing_spend", "scope": {"channel": "Radio"}},
                "whatif_no_marketing_data",
            ),
            ({"scope": {"no_such_column": "x"}}, "whatif_unknown_column"),
        ],
    )
    def test_scenarios_that_cannot_run_say_why(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        body: dict[str, Any],
        code: str,
    ) -> None:
        response = post_run(client, world, **body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code

    def test_the_per_minute_limit(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(whatif_api, "RUNS_PER_MINUTE", 2)
        assert post_run(client, world, change=1).status_code == 200
        assert post_run(client, world, change=2).status_code == 200
        limited = post_run(client, world, change=3)
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "too_many_runs"
