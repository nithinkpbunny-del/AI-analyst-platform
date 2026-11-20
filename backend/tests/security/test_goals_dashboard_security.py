"""M15 security: goals and tiles are walled off by RLS (P3); re-running a tile and computing goals stay read-only
(P2) and reach only what the person may see (P6); a tile's stored calls cannot become a write or an AI call."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.analytics.common import Period
from app.analytics.goals import GoalSpec
from app.config import Settings
from app.dashboard import service as dashboard
from app.db.tenant import scope_to_org
from app.goals import service as goal_service
from app.models import DashboardTile, Goal
from tests import analytics_data as demo
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def _rows(db: DbSession, world: World) -> None:
    for org, owner in ((world.org_a, world.owner_a), (world.org_b, world.owner_b)):
        db.add(
            Goal(
                org_id=org.id,
                metric_key="revenue",
                target_value=1,
                direction="at_least",
                period_start=date(2026, 11, 1),
                period_end=date(2026, 11, 30),
                filters={},
            )
        )
        db.add(
            DashboardTile(
                org_id=org.id,
                user_id=owner.id,
                title="t",
                spec={},
                position=0,
                refresh_minutes=60,
                shared=True,
            )
        )
    db.commit()


@pytest.mark.parametrize("model", [Goal, DashboardTile], ids=lambda m: m.__tablename__)
def test_a_workspace_sees_only_its_own_rows(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession], model: Any
) -> None:
    _rows(db, world)
    with session_factory() as scoped:
        scope_to_org(scoped, world.org_a.id)
        found = list(scoped.scalars(select(model)))
        assert found and {r.org_id for r in found} == {world.org_a.id}


@pytest.mark.parametrize("model", [Goal, DashboardTile], ids=lambda m: m.__tablename__)
def test_rows_cannot_be_moved_into_another_workspace(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession], model: Any
) -> None:
    _rows(db, world)
    with session_factory() as scoped:
        scope_to_org(scoped, world.org_a.id)
        row = scoped.scalars(select(model)).first()
        assert row is not None
        row.org_id = world.org_b.id
        with pytest.raises(Exception):  # noqa: B017 - RLS WITH CHECK refuses it
            scoped.commit()


def test_a_stored_write_is_refused_and_changes_nothing() -> None:
    """A tile spec is built by the server from the answer's trace, but even a tampered one only reaches the guarded,
    read-only workspace: writes, other tables and file access are refused."""
    ctx = demo.agent_context()
    before = len(ctx.workspace.frame("SELECT * FROM shopify_orders"))
    spec = {
        "tables": ["shopify_orders"],
        "dates": "fixed",
        "anchor": "2026-11-01",
        "calls": [
            {
                "id": "a",
                "tool": "run_sql",
                "input": {"sql": "DELETE FROM shopify_orders"},
                "result_id": "r1",
            },
            {
                "id": "b",
                "tool": "run_sql",
                "input": {"sql": "SELECT * FROM read_csv('/etc/passwd')"},
                "result_id": "r2",
            },
            {"id": "c", "tool": "give_answer", "input": {"lead": "x"}, "result_id": "r3"},
        ],
        "charts": [],
        "headline": None,
        "table_result": "r1",
    }
    out = dashboard.replay(ctx, spec)
    assert out["status"] == "error"
    assert len(ctx.workspace.frame("SELECT * FROM shopify_orders")) == before
    assert [t.get("error") for t in ctx.trace] == ["unsafe_sql", "unsafe_sql", "unknown_tool"]


def test_a_tampered_spec_cannot_reach_a_hidden_table() -> None:
    ctx = demo.agent_context(hidden=("shopify_customers",))
    spec = {
        "tables": ["shopify_orders"],  # lies about what it reads
        "dates": "fixed",
        "anchor": "2026-11-01",
        "calls": [
            {
                "id": "a",
                "tool": "run_sql",
                "input": {"sql": "SELECT count(*) AS n FROM shopify_customers"},
                "result_id": "r1",
            }
        ],
        "charts": [],
        "headline": None,
        "table_result": "r1",
    }
    out = dashboard.replay(ctx, spec)
    assert out["status"] == "error"
    assert not ctx.workspace.has("shopify_customers")


def test_goal_progress_runs_only_on_visible_goals() -> None:
    ctx = demo.agent_context(hidden=("shopify_orders",))
    ctx.today = date(2026, 9, 16)
    hidden_goal = GoalSpec("revenue", 1, Period(date(2026, 9, 1), date(2026, 9, 30)), goal_id="g1")
    ctx.goals = goal_service.visible_specs([hidden_goal], ctx.visible)
    assert ctx.goals == []
    assert goal_service.compute(ctx) == {"goals": {}, "provenance": {}, "notes": [], "quality": []}
    assert not ctx.workspace.has("shopify_orders")
