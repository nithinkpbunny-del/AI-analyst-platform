"""``/audit``: the activity log (BUILD_SPEC 6.7): who sees what (6.2), filters, pages and CSV export."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import AuditLog
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    world = build_world(db, settings, clock.now)
    base = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # 5:30 pm IST on 8 Nov
    entries = [
        (world.org_a.id, world.owner_a.id, "auth.login.success", base),
        (world.org_a.id, world.analyst_a.id, "auth.login.success", base + timedelta(minutes=1)),
        (world.org_a.id, world.analyst_a.id, "auth.login.failure", base + timedelta(minutes=2)),
        (world.org_a.id, world.owner_a.id, "team.invite_sent", base + timedelta(minutes=3)),
        # 19:00 UTC on 8 Nov is 00:30 IST on 9 Nov: a "9 Nov" entry in the workspace's own time zone.
        (world.org_a.id, world.owner_a.id, "team.role_changed", base + timedelta(hours=7)),
        (world.org_b.id, world.owner_b.id, "auth.login.success", base),
        (None, None, "auth.login.failure", base),
    ]
    for org_id, user_id, action, at in entries:
        db.add(
            AuditLog(
                org_id=org_id,
                user_id=user_id,
                action=action,
                at=at,
                ip="203.0.113.9",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130.0 Safari/537.36",
                details={"reason": "bad_password"} if action == "auth.login.failure" else {},
            )
        )
    db.commit()
    return world


def actions(response: object) -> list[str]:
    return [e["action"] for e in response.json()["entries"]]  # type: ignore[attr-defined]


class TestWhoSeesWhat:
    def test_owner_sees_the_whole_workspace_newest_first(
        self, client: TestClient, world: World
    ) -> None:
        response = client.get("/audit", headers=world.headers("owner_a"))
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "workspace"
        assert actions(response) == [
            "team.role_changed",
            "team.invite_sent",
            "auth.login.failure",
            "auth.login.success",
            "auth.login.success",
        ]
        first = body["entries"][0]
        assert first["user_name"] == "Asha Rao"
        assert first["browser"] == "Chrome on macOS"

    def test_analyst_sees_only_their_own(self, client: TestClient, world: World) -> None:
        response = client.get("/audit", headers=world.headers("analyst_a"))
        body = response.json()
        assert body["scope"] == "own"
        assert {e["user_id"] for e in body["entries"]} == {str(world.analyst_a.id)}
        # Asking for someone else's entries returns nothing rather than an error.
        other = client.get(
            "/audit", params={"user_id": str(world.owner_a.id)}, headers=world.headers("analyst_a")
        )
        assert other.json()["entries"] == []

    def test_viewer_is_refused(self, client: TestClient, world: World) -> None:
        assert client.get("/audit", headers=world.headers("viewer_a")).status_code == 403

    def test_other_workspaces_and_orgless_entries_never_show(
        self, client: TestClient, world: World
    ) -> None:
        response = client.get("/audit", headers=world.headers("owner_b"))
        assert {e["user_id"] for e in response.json()["entries"]} == {str(world.owner_b.id)}
        response = client.get(
            "/audit", params={"user_id": str(world.owner_a.id)}, headers=world.headers("owner_b")
        )
        assert response.json()["entries"] == []


class TestFilters:
    def test_by_person(self, client: TestClient, world: World) -> None:
        response = client.get(
            "/audit", params={"user_id": str(world.analyst_a.id)}, headers=world.headers("owner_a")
        )
        assert actions(response) == ["auth.login.failure", "auth.login.success"]

    def test_by_action_prefix_or_exact(self, client: TestClient, world: World) -> None:
        headers = world.headers("owner_a")
        assert actions(client.get("/audit", params={"action": "team"}, headers=headers)) == [
            "team.role_changed",
            "team.invite_sent",
        ]
        exact = client.get("/audit", params={"action": "auth.login.failure"}, headers=headers)
        assert actions(exact) == ["auth.login.failure"]
        # A prefix matches whole segments only: "auth.log" is not "auth.login".
        assert (
            client.get("/audit", params={"action": "auth.log"}, headers=headers).json()["entries"]
            == []
        )

    def test_dates_are_in_the_workspace_time_zone(self, client: TestClient, world: World) -> None:
        headers = world.headers("owner_a")
        nov8 = client.get(
            "/audit", params={"from": "2026-11-08", "to": "2026-11-08"}, headers=headers
        )
        assert "team.role_changed" not in actions(nov8)
        assert len(actions(nov8)) == 4
        nov9 = client.get("/audit", params={"from": "2026-11-09"}, headers=headers)
        assert actions(nov9) == ["team.role_changed"]

    def test_bad_filters(self, client: TestClient, world: World) -> None:
        headers = world.headers("owner_a")
        for params in (
            {"from": "8 Nov"},
            {"action": "DROP TABLE"},
            {"limit": "500"},
            {"user_id": "x"},
        ):
            assert client.get("/audit", params=params, headers=headers).status_code == 422
        bad_cursor = client.get("/audit", params={"cursor": "!!!"}, headers=headers)
        assert bad_cursor.status_code == 422


class TestPages:
    def test_cursor_walks_every_entry_once(self, client: TestClient, world: World) -> None:
        headers = world.headers("owner_a")
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            body = client.get("/audit", params=params, headers=headers).json()
            seen += [e["id"] for e in body["entries"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 5 == len(set(seen))


class TestExport:
    def test_csv_with_filters_and_logged(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        response = client.get(
            "/audit/export.csv", params={"action": "auth"}, headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="activity-2026-11-09.csv"' in response.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
        assert rows[0][:4] == ["Time", "Person", "Email", "Action"]
        assert len(rows) == 4
        assert rows[1][0] == "2026-11-08T17:32:00+05:30"  # workspace time zone
        assert rows[1][3] == "auth.login.failure"
        assert rows[1][7] == '{"reason": "bad_password"}'
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "audit.exported")).one()
        assert entry.details["rows"] == 3 and entry.details["scope"] == "workspace"
        assert entry.details["filters"] == {"action": "auth"}

    def test_cells_that_look_like_formulas_are_neutralised(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        world.owner_a.name = '=HYPERLINK("http://evil")'
        db.commit()
        response = client.get("/audit/export.csv", headers=world.headers("owner_a"))
        assert "'=HYPERLINK" in response.text
        assert ",=HYPERLINK" not in response.text

    def test_analyst_exports_only_their_own(self, client: TestClient, world: World) -> None:
        response = client.get("/audit/export.csv", headers=world.headers("analyst_a"))
        rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))[1:]
        assert {r[2] for r in rows} == {"ravi@urbancart.in"}
