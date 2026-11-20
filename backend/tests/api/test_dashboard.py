"""The pinned dashboard end to end (BUILD_SPEC X8, 6.2; M15): pin an answer, see it re-run for you, share it with
the team, reorder, change and remove tiles; who sees which tile. Jobs run inline; re-running makes no AI call."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.llm.fake import FakeLLMFactory
from app.models import AuditLog, DashboardTile, Membership, UsageEvent
from tests.api.test_chat import answer_of, answer_turn, ask, kpi_turn, orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

TOTAL = 6360.0


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def answered(
    client: TestClient, world: World, llm_factory: FakeLLMFactory, who: str = "owner_a"
) -> dict[str, Any]:
    llm_factory.next_script = [kpi_turn(), answer_turn()]
    message = answer_of(ask(client, world, who=who).json())
    assert message["status"] == "done", message
    return message


def pin(
    client: TestClient, world: World, message_id: str, who: str = "owner_a", **body: Any
) -> Any:
    return client.post(
        "/dashboard/tiles", json={"message_id": message_id, **body}, headers=world.headers(who)
    )


def board(client: TestClient, world: World, who: str = "owner_a") -> dict[str, Any]:
    response = client.get("/dashboard", headers=world.headers(who))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestPinning:
    def test_pin_an_answer_and_see_it_live(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        message = answered(client, world, llm_factory)
        calls_before = db.scalars(select(UsageEvent)).all()
        response = pin(client, world, message["id"])
        assert response.status_code == 201, response.text
        tile = response.json()
        assert tile["title"] == "How much did we sell in January?"
        assert tile["mine"] and tile["dates"] == "rolling" and tile["shared"] is False
        assert tile["conversation_id"] and tile["message_id"] == message["id"]
        # It ran once at once, for the person pinning it, without any AI call.
        result = tile["result"]
        assert result["status"] == "ok"
        assert result["headline"]["value"] == pytest.approx(TOTAL)
        assert result["headline"]["format"] == "inr"
        assert result["table"]["rows"]
        rid = result["headline"]["result_id"]
        assert result["provenance"][rid]["tool"] == "kpi_summary"
        assert result["provenance"][rid]["tables"][0]["name"]
        assert len(db.scalars(select(UsageEvent)).all()) == len(calls_before)
        assert tile["stale"] is False and tile["refreshing"] is False
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "dashboard.tile_pinned")).one()
        assert entry.target_type == "tile" and entry.details == {"dates": "rolling"}
        listed = board(client, world)
        assert [t["id"] for t in listed["tiles"]] == [tile["id"]]
        assert listed["can_edit"] is True

    def test_custom_title_and_fixed_dates(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        message = answered(client, world, llm_factory)
        tile = pin(client, world, message["id"], title="  January   sales ", dates="fixed").json()
        assert tile["title"] == "January sales" and tile["dates"] == "fixed"

    def test_only_your_own_finished_answers(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
    ) -> None:
        message = answered(client, world, llm_factory)
        assert pin(client, world, message["id"], who="analyst_a").status_code == 404
        assert pin(client, world, message["id"], who="owner_b").status_code == 404
        assert pin(client, world, message["id"], who="viewer_a").status_code == 403
        llm_factory.next_script = []
        failed = answer_of(ask(client, world, question="And February?").json())
        if failed["status"] != "done":
            response = pin(client, world, failed["id"])
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "not_pinnable"

    def test_an_answer_with_no_figures_cannot_be_pinned(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
    ) -> None:
        llm_factory.next_script = [answer_turn(lead="I need to know which month you mean.")]
        message = answer_of(ask(client, world).json())
        response = pin(client, world, message["id"])
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_pinnable"

    def test_a_limit_on_tiles(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.dashboard import access

        monkeypatch.setattr(access, "MAX_TILES", 1)
        message = answered(client, world, llm_factory)
        assert pin(client, world, message["id"]).status_code == 201
        response = pin(client, world, message["id"])
        assert response.status_code == 409 and response.json()["error"]["code"] == "too_many_tiles"


class TestRefreshAndEdit:
    def test_refresh_only_what_is_due_unless_forced(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        clock: FakeClock,
    ) -> None:
        tile = pin(client, world, answered(client, world, llm_factory)["id"]).json()
        first = tile["result"]["computed_at"]
        clock.advance(minutes=5)
        again = client.post("/dashboard/refresh", json={}, headers=world.headers("owner_a")).json()
        assert again["tiles"][0]["result"]["computed_at"] == first
        forced = client.post(
            "/dashboard/refresh", json={"force": True}, headers=world.headers("owner_a")
        ).json()
        assert forced["tiles"][0]["result"]["computed_at"] != first
        assert forced["tiles"][0]["result"]["headline"]["value"] == pytest.approx(TOTAL)

    def test_rename_refresh_time_dates_and_remove(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        tile = pin(client, world, answered(client, world, llm_factory)["id"]).json()
        url = f"/dashboard/tiles/{tile['id']}"
        changed = client.patch(
            url,
            json={"title": "Sales", "refresh_minutes": 240, "dates": "fixed"},
            headers=world.headers("owner_a"),
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert (body["title"], body["refresh_minutes"], body["dates"]) == ("Sales", 240, "fixed")
        assert body["result"] is None  # new dates: the old result is dropped
        assert (
            client.patch(url, json={"title": "  "}, headers=world.headers("owner_a")).status_code
            == 422
        )
        assert (
            client.patch(
                url, json={"refresh_minutes": 7}, headers=world.headers("owner_a")
            ).status_code
            == 422
        )
        assert client.delete(url, headers=world.headers("owner_a")).status_code == 204
        assert board(client, world)["tiles"] == []
        assert "dashboard.tile_removed" in set(db.scalars(select(AuditLog.action)))

    def test_reorder(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
    ) -> None:
        message = answered(client, world, llm_factory)
        first = pin(client, world, message["id"], title="First").json()
        second = pin(client, world, message["id"], title="Second").json()
        # New pins go on top.
        assert [t["title"] for t in board(client, world)["tiles"]] == ["Second", "First"]
        response = client.put(
            "/dashboard/order",
            json={"tile_ids": [first["id"], second["id"]]},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 200
        assert [t["title"] for t in response.json()["tiles"]] == ["First", "Second"]
        # Ids that are not yours are ignored.
        other = client.put(
            "/dashboard/order",
            json={"tile_ids": [second["id"]]},
            headers=world.headers("analyst_a"),
        )
        assert other.status_code == 200 and other.json()["tiles"] == []
        assert [t["title"] for t in board(client, world)["tiles"]] == ["First", "Second"]


class TestSharing:
    def test_shared_tiles_run_with_the_viewers_permissions(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        tile = pin(client, world, answered(client, world, llm_factory)["id"]).json()
        assert board(client, world, "viewer_a")["tiles"] == []
        shared = client.patch(
            f"/dashboard/tiles/{tile['id']}",
            json={"shared": True},
            headers=world.headers("owner_a"),
        )
        assert shared.json()["shared"] is True
        seen = board(client, world, "viewer_a")
        assert seen["can_edit"] is False
        theirs = seen["tiles"][0]
        assert theirs["mine"] is False and theirs["owner_name"] == "Asha Rao"
        assert theirs["result"] is None  # nothing computed for the viewer yet
        assert theirs["conversation_id"] is None  # the answer is not theirs to open
        refreshed = client.post(
            "/dashboard/refresh", json={}, headers=world.headers("viewer_a")
        ).json()
        assert refreshed["tiles"][0]["result"]["headline"]["value"] == pytest.approx(TOTAL)
        # Viewers cannot change it; for them it does not exist as their own.
        url = f"/dashboard/tiles/{tile['id']}"
        assert (
            client.patch(url, json={"title": "x"}, headers=world.headers("viewer_a")).status_code
            == 403
        )
        assert (
            client.patch(url, json={"title": "x"}, headers=world.headers("analyst_a")).status_code
            == 404
        )
        assert client.delete(url, headers=world.headers("analyst_a")).status_code == 404
        assert "dashboard.tile_shared" in set(db.scalars(select(AuditLog.action)))

    def test_people_who_cannot_see_the_data_do_not_see_the_tile(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        tile = pin(client, world, answered(client, world, llm_factory)["id"]).json()
        client.patch(
            f"/dashboard/tiles/{tile['id']}",
            json={"shared": True},
            headers=world.headers("owner_a"),
        )
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"  # and no grants
        db.commit()
        assert board(client, world, "analyst_a")["tiles"] == []
        refreshed = client.post(
            "/dashboard/refresh", json={"force": True}, headers=world.headers("analyst_a")
        ).json()
        assert refreshed["tiles"] == []
        assert board(client, world, "owner_b")["tiles"] == []

    def test_a_tile_whose_data_was_hidden_later_says_no_access(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        message = answered(client, world, llm_factory, who="analyst_a")
        tile_id = pin(client, world, message["id"], who="analyst_a").json()["id"]
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"
        db.commit()
        refreshed = client.post(
            "/dashboard/refresh", json={"force": True}, headers=world.headers("analyst_a")
        ).json()
        own = next(t for t in refreshed["tiles"] if t["id"] == tile_id)
        assert own["result"] == {
            "status": "error",
            "error": "no_sources",
            "computed_at": own["result"]["computed_at"],
        }
        # The tile itself stays: access may come back.
        assert db.scalars(select(DashboardTile)).one().title


class TestPersonalRoutes:
    @pytest.fixture
    def owned(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
    ) -> dict[str, str]:
        ids = {}
        for who in ("owner_a", "analyst_a"):
            ids[who] = pin(
                client, world, answered(client, world, llm_factory, who=who)["id"], who=who
            ).json()["id"]
        return ids

    @pytest.mark.parametrize(
        ("method", "body"),
        [("PATCH", {"title": "x"}), ("DELETE", None)],
    )
    def test_tile_routes(
        self,
        client: TestClient,
        world: World,
        owned: dict[str, str],
        method: str,
        body: dict[str, Any] | None,
    ) -> None:
        url = f"/dashboard/tiles/{owned['owner_a']}"
        assert client.request(method, url, json=body).status_code == 401
        assert (
            client.request(method, url, json=body, headers=world.headers("viewer_a")).status_code
            == 403
        )
        theirs = f"/dashboard/tiles/{owned['analyst_a']}"
        response = client.request(method, theirs, json=body, headers=world.headers("owner_a"))
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}
        other_org = client.request(method, url, json=body, headers=world.headers("owner_b"))
        assert other_org.status_code == 404
        bad = client.request(
            method, "/dashboard/tiles/not-a-uuid", json=body, headers=world.headers("owner_a")
        )
        assert bad.status_code == 422
        ok = client.request(method, url, json=body, headers=world.headers("owner_a"))
        assert ok.status_code in (200, 204)

    def test_pin_route(self, client: TestClient, world: World, owned: dict[str, str]) -> None:
        body = {"message_id": "0190c0de-0000-7000-8000-000000000000"}
        assert client.post("/dashboard/tiles", json=body).status_code == 401
        assert (
            client.post(
                "/dashboard/tiles", json=body, headers=world.headers("viewer_a")
            ).status_code
            == 403
        )
        assert (
            client.post("/dashboard/tiles", json=body, headers=world.headers("owner_a")).status_code
            == 404
        )
        bad = client.post(
            "/dashboard/tiles", json={"message_id": "x"}, headers=world.headers("owner_a")
        )
        assert bad.status_code == 422
        bad = client.post(
            "/dashboard/tiles",
            json={**body, "dates": "sometimes"},
            headers=world.headers("owner_a"),
        )
        assert bad.status_code == 422
