"""Goals end to end (BUILD_SPEC X7, 6.2, 13; M15): set, see progress computed with your own permissions, change,
remove; who may see which goal; goals in the briefing and on Today. Jobs run inline; no AI call."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import AuditLog, Goal, Membership
from tests.api.test_chat import orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

METRIC = "sum_total_price"
TOTAL = 6360.0  # sum of total_price in orders_csv()
JANUARY = {"period_start": "2026-01-01", "period_end": "2026-01-31"}


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def november_csv() -> bytes:
    lines = ["id,created_at,customer_id,sales_channel,total_price,currency"]
    for i in range(16):
        lines.append(
            f"{2000 + i},2026-11-{1 + i % 8:02d}T10:00:00+05:30,{7000 + i % 5},App,{500 + i}.0,INR"
        )
    return ("\n".join(lines) + "\n").encode()


def create(client: TestClient, world: World, who: str = "owner_a", **body: Any) -> Any:
    payload = {"metric": METRIC, "target": 6000, **JANUARY, **body}
    return client.post("/goals", json=payload, headers=world.headers(who))


def refresh(client: TestClient, world: World, who: str = "owner_a", force: bool = False) -> Any:
    return client.post("/goals/refresh", json={"force": force}, headers=world.headers(who))


class TestSettingGoals:
    def test_set_a_goal_and_see_its_progress(
        self, client: TestClient, world: World, orders: dict[str, Any], db: DbSession
    ) -> None:
        response = create(client, world, name="January sales")
        assert response.status_code == 201, response.text
        goal = response.json()
        assert goal["metric_label"] == "Total price"  # in words, not "sum_total_price" (M19)
        assert goal["unit"] == "INR" and goal["direction"] == "at_least"
        assert goal["created_by"] == "Asha Rao"
        listed = client.get("/goals", headers=world.headers("owner_a")).json()
        assert listed["stale"] is True and listed["goals"][0]["progress"] is None
        computed = refresh(client, world)
        assert computed.status_code == 202
        body = computed.json()
        progress = body["goals"][0]["progress"]
        assert progress["actual"] == pytest.approx(TOTAL)
        assert progress["status"] == "achieved" and progress["method"] == "final"
        rid = progress["result_id"]
        assert body["provenance"][rid]["tool"] == "goal_progress"
        assert body["stale"] is False and body["refreshing"] is False
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "goal.created")).one()
        assert entry.details == {"metric": METRIC} and entry.target_type == "goal"

    def test_analysts_set_goals_viewers_look(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        assert create(client, world, who="analyst_a").status_code == 201
        assert create(client, world, who="viewer_a").status_code == 403
        assert client.get("/goals/options", headers=world.headers("viewer_a")).status_code == 403
        seen = refresh(client, world, who="viewer_a").json()
        assert seen["goals"][0]["progress"]["actual"] == pytest.approx(TOTAL)

    def test_options_list_metrics_with_a_date(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        options = client.get("/goals/options", headers=world.headers("owner_a")).json()
        keys = {m["key"] for m in options["metrics"]}
        assert METRIC in keys
        metric = next(m for m in options["metrics"] if m["key"] == METRIC)
        assert metric["additive"] is True and metric["direction"] == "at_least"
        assert "sales_channel" in {d["key"] for d in metric["dimensions"]}

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ({"metric": "no_such_metric"}, "unknown_metric"),
            ({"filters": {"no_such_column": "x"}}, "unknown_column"),
        ],
    )
    def test_metric_and_columns_must_be_usable(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        body: dict[str, Any],
        code: str,
    ) -> None:
        response = create(client, world, **body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code

    @pytest.mark.parametrize(
        "body",
        [
            {"target": 0},
            {"target": -5},
            {"period_start": "2026-02-01", "period_end": "2026-01-01"},
            {"filters": {"a": "1", "b": "2", "c": "3", "d": "4"}},
            {"direction": "about"},
        ],
    )
    def test_validation(
        self, client: TestClient, world: World, orders: dict[str, Any], body: dict[str, Any]
    ) -> None:
        assert create(client, world, **body).status_code == 422

    def test_filters_narrow_the_goal(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        create(client, world, filters={"sales_channel": "App"})
        progress = refresh(client, world).json()["goals"][0]["progress"]
        app_total = sum(100 + i * 3 + 0.5 for i in range(40) if i % 2)
        assert progress["actual"] == pytest.approx(app_total)


class TestChanging:
    def test_change_and_remove(
        self, client: TestClient, world: World, orders: dict[str, Any], db: DbSession
    ) -> None:
        goal = create(client, world).json()
        refresh(client, world)
        changed = client.patch(
            f"/goals/{goal['id']}",
            json={"target": 9000, "name": "Stretch"},
            headers=world.headers("analyst_a"),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["target"] == 9000 and changed.json()["name"] == "Stretch"
        # A change drops everyone's computed progress.
        listed = client.get("/goals", headers=world.headers("owner_a")).json()
        assert listed["stale"] is True and listed["goals"][0]["progress"] is None
        assert refresh(client, world).json()["goals"][0]["progress"]["status"] == "missed"
        bad = client.patch(
            f"/goals/{goal['id']}",
            json={"period_end": "2025-12-01"},
            headers=world.headers("owner_a"),
        )
        assert bad.status_code == 422
        assert (
            client.delete(f"/goals/{goal['id']}", headers=world.headers("owner_a")).status_code
            == 204
        )
        assert db.scalars(select(Goal)).all() == []
        actions = set(db.scalars(select(AuditLog.action)))
        assert {"goal.updated", "goal.deleted"} <= actions

    def test_a_fresh_result_is_not_computed_again(
        self, client: TestClient, world: World, orders: dict[str, Any], clock: FakeClock
    ) -> None:
        create(client, world)
        first = refresh(client, world).json()["computed_at"]
        clock.advance(minutes=5)
        assert refresh(client, world).json()["computed_at"] == first
        assert refresh(client, world, force=True).json()["computed_at"] != first


class TestWhoSeesWhat:
    def test_goals_of_data_one_cannot_see_do_not_exist_for_them(
        self, client: TestClient, world: World, orders: dict[str, Any], db: DbSession
    ) -> None:
        goal = create(client, world).json()
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"  # and no grants: no connection at all
        db.commit()
        assert client.get("/goals", headers=world.headers("analyst_a")).json()["goals"] == []
        for method in ("PATCH", "DELETE"):
            response = client.request(
                method,
                f"/goals/{goal['id']}",
                json={"target": 1},
                headers=world.headers("analyst_a"),
            )
            assert response.status_code == 404
        assert create(client, world, who="analyst_a").json()["error"]["code"] == "unknown_metric"

    def test_another_workspace(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        goal = create(client, world).json()
        assert client.get("/goals", headers=world.headers("owner_b")).json()["goals"] == []
        response = client.patch(
            f"/goals/{goal['id']}", json={"target": 1}, headers=world.headers("owner_b")
        )
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}

    def test_without_a_dictionary_there_is_nothing_to_set(
        self, client: TestClient, world: World
    ) -> None:
        assert (
            client.get("/goals/options", headers=world.headers("owner_a")).json()["metrics"] == []
        )
        assert create(client, world).json()["error"]["code"] == "unknown_metric"
        assert refresh(client, world).json()["goals"] == []


class TestBriefingAndToday:
    def test_a_running_goal_in_the_briefing_and_on_today(
        self, client: TestClient, world: World
    ) -> None:
        # Orders up to 8 November; the clock is on 9 November.
        upload(client, world.headers("owner_a"), "orders.csv", november_csv())
        create(
            client,
            world,
            target=50_000,
            name="November push",
            period_start="2026-11-01",
            period_end="2026-11-30",
        )
        run = client.post("/briefings/send-now", headers=world.headers("owner_a")).json()
        report = client.get(f"/reports/{run['report_id']}", headers=world.headers("owner_a")).json()
        goals = report["body"]["goals"]
        assert goals[0]["status"] == "behind"
        section = next(s for s in report["body"]["sections"] if s["key"] == "goals")
        assert section["items"][0].startswith("November push (₹50,000 [r")
        assert "Behind" in section["items"][0]
        # Today shows it from the briefing until the Goals screen computes it.
        today = client.get("/today", headers=world.headers("owner_a")).json()
        assert today["goals"][0]["label"] == "November push"
        assert today["goals"][0]["status"] == "behind"
        refresh(client, world)
        today = client.get("/today", headers=world.headers("owner_a")).json()
        assert today["goals"][0]["status"] == "behind"
        assert today["goals"][0]["projection"] is not None

    def test_today_leaves_out_goals_that_are_not_running(
        self, client: TestClient, world: World, orders: dict[str, Any]
    ) -> None:
        create(client, world)  # January; the clock is in November
        assert client.get("/today", headers=world.headers("owner_a")).json()["goals"] == []
