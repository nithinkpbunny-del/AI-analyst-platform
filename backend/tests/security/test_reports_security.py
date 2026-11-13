"""M10 security: the new tables are walled off by RLS (P3), briefings and tasks run with the recipient's
permissions (P6), and the growth tool, like every tool, cannot reach a hidden table."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.agent.tools import execute
from app.config import Settings
from app.db.tenant import scope_to_org
from app.llm.base import ToolCall
from app.models import BriefingRun, BriefingSetting, Report, Share, Task
from tests import analytics_data as demo
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


def _rows(db: DbSession, world: World) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for org, owner in ((world.org_a, world.owner_a), (world.org_b, world.owner_b)):
        report = Report(
            org_id=org.id,
            user_id=owner.id,
            title=f"{org.name} report",
            source="task",
            body={},
            provenance={},
            quality_warnings=[],
        )
        db.add(report)
        db.flush()
        db.add(
            Task(
                org_id=org.id,
                user_id=owner.id,
                prompt="p" * 10,
                title="t",
                lens="business",
                status="done",
                progress_json=[],
                token_usage={},
                result_report_id=report.id,
            )
        )
        db.add(
            BriefingSetting(
                org_id=org.id,
                user_id=owner.id,
                enabled=True,
                local_time=time(6),
                frequency="daily",
                weekday=0,
                language="en",
            )
        )
        db.add(
            BriefingRun(
                org_id=org.id,
                user_id=owner.id,
                scheduled_for=datetime(2026, 11, 13, tzinfo=UTC),
                kind="scheduled",
                status="sent",
                findings=[],
            )
        )
        db.add(
            Share(
                org_id=org.id,
                object_type="report",
                object_id=report.id,
                shared_with_user_id=owner.id,
                shared_by=owner.id,
            )
        )
        out[str(org.id)] = report.id
    db.commit()
    return out


@pytest.mark.parametrize(
    "model", [Report, Task, BriefingSetting, BriefingRun, Share], ids=lambda m: m.__tablename__
)
def test_a_workspace_sees_only_its_own_rows(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession], model: Any
) -> None:
    _rows(db, world)
    with session_factory() as scoped:
        scope_to_org(scoped, world.org_a.id)
        found = list(scoped.scalars(select(model)))
        assert found and {r.org_id for r in found} == {world.org_a.id}


def test_rows_cannot_be_written_into_another_workspace(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession]
) -> None:
    with session_factory() as scoped:
        scope_to_org(scoped, world.org_a.id)
        scoped.add(
            Report(
                org_id=world.org_b.id,
                user_id=None,
                title="x",
                source="task",
                body={},
                provenance={},
                quality_warnings=[],
            )
        )
        with pytest.raises(Exception):  # noqa: B017 - RLS WITH CHECK refuses it
            scoped.commit()


def test_briefing_history_cannot_be_deleted_by_a_workspace(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession]
) -> None:
    _rows(db, world)
    with session_factory() as scoped:
        scope_to_org(scoped, world.org_a.id)
        run = scoped.scalars(select(BriefingRun)).first()
        scoped.delete(run)
        with pytest.raises(Exception):  # noqa: B017 - no DELETE grant on briefing_runs
            scoped.commit()


def test_growth_signals_cannot_reach_a_hidden_table() -> None:
    ctx = demo.agent_context(hidden=("shopify_orders",))
    result = execute(
        ctx,
        ToolCall(
            "c1",
            "growth_signals",
            {"metric": "orders", "period": {"start": "2025-10-01", "end": "2026-09-30"}},
        ),
    )
    assert result.is_error
    assert not ctx.workspace.has("shopify_orders")


def test_growth_signals_through_the_agent_tool() -> None:
    ctx = demo.agent_context()
    result = execute(
        ctx,
        ToolCall(
            "c1",
            "growth_signals",
            {"metric": "orders", "period": {"start": "2025-10-01", "end": "2026-09-30"}},
        ),
    )
    assert not result.is_error
    content: dict[str, Any] = result.content
    assert content["result_id"] == "r1" and content["facts"]["top"]["segment"].startswith("West")
    assert ctx.store.provenance["r1"].tool == "growth_signals"


def test_scheduled_runs_carry_their_own_workspace(
    db: DbSession, world: World, session_factory: sessionmaker[DbSession]
) -> None:
    from app.briefings import schedule

    with session_factory() as unscoped:
        created = schedule.plan(unscoped, datetime(2026, 11, 13, 0, 31, tzinfo=UTC))
    for org_id, run_id in created:
        run = db.get(BriefingRun, run_id)
        assert run is not None and run.org_id == org_id
        owner = world.owner_a if org_id == world.org_a.id else world.owner_b
        assert run.user_id == owner.id
    assert {uuid.UUID(str(o)) for o, _ in created} == {world.org_a.id, world.org_b.id}
