"""Security of the billing hooks (BUILD_SPEC 6.2, P3; M18): workspaces see only their own events, cannot change or
remove the history, cannot write into another workspace, and a deleted workspace leaves nothing behind.
Also: only Owners are told about usage, and a data export carries the billing history without anything else."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.tenant import scope_to_org
from app.models import BillingEvent, Organization
from tests.api.test_billing import add_runs, set_limit
from tests.conftest import FakeClock
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    world = build_world(db, settings, clock.now)
    for org in (world.org_a, world.org_b):
        org.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        db.add(
            BillingEvent(
                org_id=org.id,
                kind="usage_warning",
                period="2026-11",
                details={"used": 400, "limit": 500},
                provider="stand-in",
                delivery="not_sent",
            )
        )
    db.commit()
    return world


def scoped(session_factory: sessionmaker[DbSession], org: Organization) -> DbSession:
    session = session_factory()
    scope_to_org(session, org.id)
    return session


def test_only_your_own_workspaces_events_are_visible(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    with scoped(session_factory, world.org_a) as session:
        rows = list(session.scalars(select(BillingEvent)))
        assert {r.org_id for r in rows} == {world.org_a.id}


def test_history_is_append_only(session_factory: sessionmaker[DbSession], world: World) -> None:
    for statement in (
        "UPDATE billing_events SET delivery = 'sent'",
        "DELETE FROM billing_events",
    ):
        with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError) as caught:
            session.execute(text(statement))
        assert "permission denied" in str(caught.value)


def test_cannot_write_an_event_into_another_workspace(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError):
        session.add(
            BillingEvent(
                org_id=world.org_b.id,
                kind="plan_changed",
                period=None,
                details={},
                provider="stand-in",
                delivery="not_sent",
            )
        )
        session.commit()


def test_the_same_warning_cannot_be_recorded_twice_for_a_month(
    session_factory: sessionmaker[DbSession], world: World
) -> None:
    with scoped(session_factory, world.org_a) as session, pytest.raises(DBAPIError):
        session.add(
            BillingEvent(
                org_id=world.org_a.id,
                kind="usage_warning",
                period="2026-11",
                details={},
                provider="stand-in",
                delivery="not_sent",
            )
        )
        session.commit()


def test_deleting_a_workspace_leaves_no_billing_events(db: DbSession, world: World) -> None:
    db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": world.org_a.id})
    db.commit()
    assert (
        db.scalar(
            select(func.count())
            .select_from(BillingEvent)
            .where(BillingEvent.org_id == world.org_a.id)
        )
        == 0
    )
    assert db.scalar(select(func.count()).select_from(BillingEvent)) == 1  # the other one stays


def test_only_owners_see_the_usage_warning_on_today(
    client: TestClient, db: DbSession, world: World, clock: FakeClock
) -> None:
    set_limit(db, world.org_a, 10)
    add_runs(db, world.org_a, world.analyst_a, 8, clock.now)
    owner = client.get("/today", headers=world.headers("owner_a")).json()["usage_warning"]
    assert owner == {
        "level": "warning",
        "used": 8,
        "limit": 10,
        "percent": 80.0,
        "resets_on": "2026-12-01",
    }
    for who in ("analyst_a", "viewer_a"):
        assert client.get("/today", headers=world.headers(who)).json()["usage_warning"] is None
    # Another workspace's Owner sees nothing of it.
    assert client.get("/today", headers=world.headers("owner_b")).json()["usage_warning"] is None
    add_runs(db, world.org_a, world.analyst_a, 2, clock.now)
    assert (
        client.get("/today", headers=world.headers("owner_a")).json()["usage_warning"]["level"]
        == "reached"
    )


def test_the_export_has_the_billing_history_and_only_that_workspaces(
    client: TestClient, world: World
) -> None:
    response = client.get("/org/export.zip", headers=world.headers("owner_a"))
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "billing.json" in archive.namelist()
        billing = json.loads(archive.read("billing.json"))
        assert "billing.json" in archive.read("README.txt").decode()
    assert [(e["kind"], e["period"], e["provider"]) for e in billing] == [
        ("usage_warning", "2026-11", "stand-in")
    ]
