"""Billing hooks (BUILD_SPEC R7, M18; D117-D121): plans, the 80%/100% warnings, the plan change flow and invoice-ready
summaries. **There is no payment anywhere**: the tests also check that nothing here can reach a network.

401/403/404/422 for the read routes are in ``test_route_matrix``; ``PUT /org/billing/plan`` needs state (a saved key)
and is covered here (``TestPlanChange``). Plans, limits and usage follow M5 (``app.llm.usage``): the counts below are
the same ones ``/org/usage`` gives.
"""

from __future__ import annotations

import csv
import io
import logging
import socket
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.billing import limits, plans, summary
from app.billing import provider as billing_provider
from app.config import Settings
from app.connectors.registry import ConnectorRuntime
from app.email.sender import MemoryEmailSender
from app.llm import usage
from app.llm.base import Usage
from app.llm.fake import FakeLLMFactory
from app.models import AuditLog, BillingEvent, Organization
from tests.api.test_chat import answer_turn, ask, kpi_turn, orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

KEY = "AIzaSyOwnWorkspaceKey-000000000000000000"


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    world = build_world(db, settings, clock.now)
    # Created long before the months the tests look at (the real clock stamps ``created_at``).
    for org in (world.org_a, world.org_b):
        org.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    return world


def owner(world: World) -> dict[str, str]:
    return world.headers("owner_a")


def set_limit(db: DbSession, org: Organization, limit: int) -> None:
    org.monthly_question_limit = limit
    db.commit()


def add_runs(
    db: DbSession,
    org: Organization,
    user: Any,
    count: int,
    at: datetime,
    *,
    kind: str = "question",
    key_source: str = "managed",
) -> None:
    for _ in range(count):
        usage.record(
            db,
            org_id=org.id,
            user_id=user.id,
            run_id=uuid.uuid4(),
            kind=kind,
            provider="gemini",
            model="gemini-2.5-flash",
            usage=Usage(1_000, 200),
            key_source=key_source,
            at=at,
        )
    db.commit()


def events(db: DbSession, org: Organization, kind: str | None = None) -> list[BillingEvent]:
    db.expire_all()
    query = (
        select(BillingEvent).where(BillingEvent.org_id == org.id).order_by(BillingEvent.created_at)
    )
    if kind:
        query = query.where(BillingEvent.kind == kind)
    return list(db.scalars(query))


def put_key(client: TestClient, world: World) -> None:
    response = client.put(
        "/org/ai/key", json={"provider": "gemini", "api_key": KEY}, headers=owner(world)
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------------------------------------ levels
class TestLevels:
    @pytest.mark.parametrize(
        ("used", "level"),
        [(0, "ok"), (7, "ok"), (8, "warning"), (9, "warning"), (10, "reached"), (12, "reached")],
    )
    def test_80_and_100_percent_of_ten(self, used: int, level: str) -> None:
        assert limits.level_for(used, 10) == level

    def test_a_limit_of_zero_is_always_reached(self) -> None:
        assert limits.level_for(0, 0) == "reached"

    def test_status_counts_exactly_what_the_usage_screen_counts(
        self, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        a, user = world.org_a, world.analyst_a
        add_runs(db, a, user, 3, clock.now)  # counted
        add_runs(db, a, user, 2, clock.now, kind="task")  # counted
        add_runs(db, a, user, 4, clock.now, kind="briefing")  # never counted
        add_runs(db, a, user, 1, clock.now, kind="check")  # never counted
        add_runs(db, a, user, 2, clock.now, key_source="byok")  # not on the platform's key
        add_runs(db, a, user, 5, clock.now - timedelta(days=20))  # last month
        add_runs(db, world.org_b, world.analyst_b, 9, clock.now)  # another workspace
        current = limits.status(db, a, clock.now)
        assert (current.used, current.limit, current.level, current.percent) == (5, 10, "ok", 50.0)
        assert current.resets_on == date(2026, 12, 1)
        month = usage.current_month(clock.now, a.timezone)
        assert usage.questions_used(db, a.id, month) == current.used  # M5's own number

    def test_a_workspace_with_its_own_key_has_no_platform_limit(
        self, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        world.org_a.plan = "byok"
        db.commit()
        add_runs(db, world.org_a, world.analyst_a, 50, clock.now, key_source="byok")
        current = limits.status(db, world.org_a, clock.now)
        assert (current.level, current.limit, current.percent) == ("none", None, None)


# ---------------------------------------------------------------------------------------------- warnings
class TestWarnings:
    def finish(self, runtime: ConnectorRuntime, world: World) -> None:
        limits.after_limited_run(runtime, world.org_a.id)

    def test_80_percent_records_one_event_and_emails_the_owner_once(
        self,
        runtime: ConnectorRuntime,
        db: DbSession,
        world: World,
        clock: FakeClock,
        outbox: MemoryEmailSender,
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 7, clock.now)
        self.finish(runtime, world)
        assert events(db, world.org_a) == [] and outbox.sent == []
        add_runs(db, world.org_a, world.analyst_a, 1, clock.now)
        self.finish(runtime, world)
        [event] = events(db, world.org_a)
        assert (event.kind, event.period, event.provider, event.delivery) == (
            "usage_warning",
            "2026-11",
            "stand-in",
            "not_sent",
        )
        assert event.details["used"] == 8 and event.details["limit"] == 10
        assert event.details["percent"] == 80.0 and event.details["resets_on"] == "2026-12-01"
        [mail] = outbox.to(world.owner_a.email)
        assert "80%" in mail.subject and "8 of 10" in mail.text and "1 December 2026" in mail.text
        assert "Nothing is charged" in mail.text
        assert outbox.to(world.analyst_a.email) == []  # only Owners are told
        # More runs below the limit do not repeat it.
        add_runs(db, world.org_a, world.analyst_a, 1, clock.now)
        self.finish(runtime, world)
        assert len(events(db, world.org_a)) == 1 and len(outbox.sent) == 1

    def test_reaching_the_limit_is_its_own_event_and_email(
        self,
        runtime: ConnectorRuntime,
        db: DbSession,
        world: World,
        clock: FakeClock,
        outbox: MemoryEmailSender,
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 8, clock.now)
        self.finish(runtime, world)
        add_runs(db, world.org_a, world.analyst_a, 2, clock.now)
        self.finish(runtime, world)
        self.finish(runtime, world)  # a second run finishing changes nothing
        assert [e.kind for e in events(db, world.org_a)] == ["usage_warning", "usage_limit_reached"]
        assert len(outbox.sent) == 2
        assert "limit is reached" in outbox.sent[1].subject
        assert "paused until 1 December 2026" in outbox.sent[1].text

    def test_going_straight_to_the_limit_records_only_that(
        self, runtime: ConnectorRuntime, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 3)
        add_runs(db, world.org_a, world.analyst_a, 3, clock.now)
        self.finish(runtime, world)
        assert [e.kind for e in events(db, world.org_a)] == ["usage_limit_reached"]

    def test_a_new_month_warns_again(
        self, runtime: ConnectorRuntime, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 8, clock.now)
        self.finish(runtime, world)
        clock.advance(days=30)  # 9 December
        add_runs(db, world.org_a, world.analyst_a, 8, clock.now)
        self.finish(runtime, world)
        assert [e.period for e in events(db, world.org_a)] == ["2026-11", "2026-12"]

    def test_two_runs_finishing_at_once_record_it_once(
        self, runtime: ConnectorRuntime, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 9, clock.now)
        provider = billing_provider.StandInProvider()
        first = limits.record_event(
            db, provider, org_id=world.org_a.id, kind="usage_warning", period="2026-11", details={}
        )
        # The second run's check came before the first's insert, so it tries to add the same row.
        second = limits.record_event(
            db, provider, org_id=world.org_a.id, kind="usage_warning", period="2026-11", details={}
        )
        db.commit()
        assert first is not None and second is None
        assert len(events(db, world.org_a)) == 1

    def test_other_plans_and_other_kinds_never_warn(
        self,
        runtime: ConnectorRuntime,
        db: DbSession,
        world: World,
        clock: FakeClock,
        outbox: MemoryEmailSender,
    ) -> None:
        set_limit(db, world.org_a, 5)
        add_runs(db, world.org_a, world.analyst_a, 20, clock.now, kind="briefing")
        add_runs(db, world.org_a, world.analyst_a, 20, clock.now, key_source="byok")
        self.finish(runtime, world)
        assert events(db, world.org_a) == [] and outbox.sent == []
        world.org_a.plan = "byok"
        db.commit()
        add_runs(db, world.org_a, world.analyst_a, 20, clock.now)
        self.finish(runtime, world)
        assert events(db, world.org_a) == [] and outbox.sent == []

    def test_it_never_breaks_the_answer(
        self,
        runtime: ConnectorRuntime,
        db: DbSession,
        world: World,
        clock: FakeClock,
        outbox: MemoryEmailSender,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 9, clock.now)

        def broken(_: Any) -> None:
            raise RuntimeError("mail server down")

        monkeypatch.setattr(outbox, "send", broken)
        with caplog.at_level(logging.ERROR):
            self.finish(runtime, world)  # does not raise
        assert len(events(db, world.org_a)) == 1  # the event is kept even when the email failed
        monkeypatch.setattr(limits, "status", lambda *a, **k: 1 / 0)
        self.finish(runtime, world)  # nor does a failure while checking

    def test_a_real_question_through_the_api_triggers_it(
        self,
        client: TestClient,
        db: DbSession,
        world: World,
        llm_factory: FakeLLMFactory,
        outbox: MemoryEmailSender,
    ) -> None:
        upload(client, owner(world), "orders.csv", orders_csv())
        set_limit(db, world.org_a, 1)
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        assert ask(client, world).status_code in (200, 201, 202)
        assert [e.kind for e in events(db, world.org_a)] == ["usage_limit_reached"]
        assert any("limit is reached" in m.subject for m in outbox.to(world.owner_a.email))
        # The next question is refused by M5's rule, as before.
        refused = ask(client, world)
        assert (
            refused.status_code == 429 and refused.json()["error"]["code"] == "usage_limit_reached"
        )


# ------------------------------------------------------------------------------------------------ plans
class TestOverview:
    def test_plans_limit_and_the_stand_in_notice(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        add_runs(db, world.org_a, world.analyst_a, 8, clock.now)
        body = client.get("/org/billing", headers=owner(world)).json()
        assert body["plan"] == "managed"
        assert [
            (p["key"], p["monthly_questions"], p["price_inr_per_month"]) for p in body["plans"]
        ] == [
            ("managed", 10, None),
            ("byok", None, None),
        ]
        assert body["limit"] == {
            "plan": "managed",
            "month": "2026-11",
            "used": 8,
            "limit": 10,
            "percent": 80.0,
            "level": "warning",
            "resets_on": "2026-12-01",
        }
        assert body["provider"]["name"] == "stand-in" and body["provider"]["live"] is False
        assert "not switched on" in body["provider"]["note"]

    def test_it_matches_the_usage_screen(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        add_runs(db, world.org_a, world.analyst_a, 4, clock.now)
        add_runs(db, world.org_a, world.analyst_a, 3, clock.now, kind="task")
        billing = client.get("/org/billing", headers=owner(world)).json()["limit"]
        shown = client.get("/org/usage", headers=owner(world)).json()
        assert billing["used"] == shown["totals"]["limited_questions"] == 7
        assert billing["limit"] == shown["limit"]

    def test_the_catalog_has_no_price_anywhere(self, world: World) -> None:
        assert all(p.price_inr_per_month is None for p in plans.catalog(world.org_a))

    def test_only_owners_see_billing(self, client: TestClient, world: World) -> None:
        for who in ("analyst_a", "viewer_a"):
            assert client.get("/org/billing", headers=world.headers(who)).status_code == 403
        assert client.get("/org/billing").status_code == 401

    def test_another_workspaces_events_are_not_listed(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        db.add(
            BillingEvent(
                org_id=world.org_b.id,
                kind="usage_warning",
                period="2026-11",
                details={"used": 400},
                provider="stand-in",
                delivery="not_sent",
            )
        )
        db.commit()
        assert client.get("/org/billing", headers=owner(world)).json()["events"] == []


class TestPlanChange:
    def preview(self, client: TestClient, world: World, plan: str) -> dict[str, Any]:
        response = client.post(
            "/org/billing/plan-change/preview", json={"plan": plan}, headers=owner(world)
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def codes(self, body: dict[str, Any]) -> list[str]:
        return [e["code"] for e in body["effects"]]

    def test_preview_to_your_own_key_needs_a_key_and_changes_nothing(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        body = self.preview(client, world, "byok")
        assert body["allowed"] is False and body["blockers"] == ["key_missing"]
        assert "no_platform_limit" in self.codes(body) and "not_charged" in self.codes(body)
        db.expire_all()
        assert db.get(Organization, world.org_a.id).plan == "managed"  # type: ignore[union-attr]
        assert events(db, world.org_a) == []

    def test_preview_with_a_key_names_it_and_allows_the_change(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        put_key(client, world)
        body = self.preview(client, world, "byok")
        assert body["allowed"] is True and body["blockers"] == []
        uses = next(e for e in body["effects"] if e["code"] == "uses_own_key")
        assert uses == {"code": "uses_own_key", "provider": "gemini", "last4": "0000"}
        assert KEY not in str(body)

    def test_preview_the_same_plan_is_not_a_change(self, client: TestClient, world: World) -> None:
        body = self.preview(client, world, "managed")
        assert body["allowed"] is False and body["blockers"] == ["same_plan"]

    def test_preview_back_to_managed_says_what_the_month_already_holds(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        world.org_a.plan = "byok"
        db.commit()
        add_runs(db, world.org_a, world.analyst_a, 9, clock.now)
        near = self.preview(client, world, "managed")
        assert near["allowed"] is True
        assert {"code": "month_so_far", "used": 9, "limit": 10} in near["effects"]
        assert "near_limit" in self.codes(near)
        add_runs(db, world.org_a, world.analyst_a, 1, clock.now)
        full = self.preview(client, world, "managed")
        assert "already_at_limit" in self.codes(full)
        assert full["allowed"] is True  # allowed, with the warning (M5 allows it too)

    def test_confirm_switches_logs_and_records_the_event(
        self, client: TestClient, db: DbSession, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        put_key(client, world)
        response = client.put("/org/billing/plan", json={"plan": "byok"}, headers=owner(world))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["plan"] == "byok" and body["limit"]["level"] == "none"
        [event] = events(db, world.org_a, "plan_changed")
        assert (
            event.period is None and event.provider == "stand-in" and event.delivery == "not_sent"
        )
        assert event.details["from"] == "managed" and event.details["to"] == "byok"
        assert event.details["via"] == "billing" and event.created_by == world.owner_a.id
        assert body["events"][0]["kind"] == "plan_changed"
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "org.plan_changed")).one()
        assert entry.details == {"from": "managed", "to": "byok"}
        # And back again: every change is its own event (no period, so no once-a-month rule).
        client.put("/org/billing/plan", json={"plan": "managed"}, headers=owner(world))
        assert [e.details["to"] for e in events(db, world.org_a, "plan_changed")] == [
            "byok",
            "managed",
        ]

    def test_confirm_refuses_what_the_preview_blocks(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        missing = client.put("/org/billing/plan", json={"plan": "byok"}, headers=owner(world))
        assert missing.status_code == 409 and missing.json()["error"]["code"] == "key_missing"
        same = client.put("/org/billing/plan", json={"plan": "managed"}, headers=owner(world))
        assert same.status_code == 409 and same.json()["error"]["code"] == "same_plan"
        assert events(db, world.org_a) == []

    def test_validation_and_access(self, client: TestClient, world: World) -> None:
        for body in ({"plan": "gold"}, {}, {"plan": "byok", "price": 1}):
            assert (
                client.put("/org/billing/plan", json=body, headers=owner(world)).status_code == 422
            )
        for who in ("analyst_a", "viewer_a"):
            denied = client.put(
                "/org/billing/plan", json={"plan": "byok"}, headers=world.headers(who)
            )
            assert denied.status_code == 403
        assert client.put("/org/billing/plan", json={"plan": "byok"}).status_code == 401

    def test_the_older_plan_routes_record_the_same_event(
        self, client: TestClient, db: DbSession, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        put_key(client, world)
        assert (
            client.put("/org/ai/plan", json={"plan": "byok"}, headers=owner(world)).status_code
            == 200
        )
        assert client.delete("/org/ai/key", headers=owner(world)).status_code == 204
        changes = events(db, world.org_a, "plan_changed")
        assert [(e.details["to"], e.details["via"]) for e in changes] == [
            ("byok", "ai_settings"),
            ("managed", "ai_settings"),
        ]
        assert changes[1].details["reason"] == "key_removed"

    def test_another_workspace_is_untouched(
        self, client: TestClient, db: DbSession, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        put_key(client, world)
        client.put("/org/billing/plan", json={"plan": "byok"}, headers=owner(world))
        db.expire_all()
        assert db.get(Organization, world.org_b.id).plan == "managed"  # type: ignore[union-attr]
        assert events(db, world.org_b) == []


# ------------------------------------------------------------------------------------------------ summary
class TestSummary:
    def lines(self, body: dict[str, Any]) -> dict[str, Any]:
        return {line["code"]: line["quantity"] for line in body["lines"]}

    def test_this_month_is_invoice_ready_but_not_an_invoice(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        set_limit(db, world.org_a, 10)
        a, user = world.org_a, world.analyst_a
        add_runs(db, a, user, 4, clock.now)
        add_runs(db, a, user, 2, clock.now, kind="task")
        add_runs(db, a, user, 3, clock.now, kind="briefing")
        add_runs(db, a, user, 2, clock.now, key_source="byok")
        add_runs(db, a, user, 6, clock.now - timedelta(days=20))  # October
        add_runs(db, world.org_b, world.analyst_b, 9, clock.now)
        body = client.get("/org/billing/summary", headers=owner(world)).json()
        assert body["workspace"] == {"id": str(a.id), "name": "UrbanCart"}
        assert body["period"] == {
            "month": "2026-11",
            "start": "2026-11-01",
            "end": "2026-11-30",
            "timezone": "Asia/Kolkata",
        }
        assert body["plan"] == {"key": "managed", "name": "Managed AI"}
        assert self.lines(body) == {
            "questions_included": 10,
            "questions_used": 6,
            "over_limit": 0,
            "own_key_runs": 2,
            "briefings": 3,
            "ai_cost_estimate": body["lines"][-1]["quantity"],
        }
        assert (body["currency"], body["amount_due_inr"]) == ("INR", 0)
        assert (body["price_status"], body["status"], body["frozen_at"]) == (
            "not_priced",
            "draft_no_payment",
            None,
        )
        assert "not an invoice" in body["notes"][0]
        # The same month through M5's usage route.
        shown = client.get("/org/usage", headers=owner(world)).json()["totals"]
        assert shown["limited_questions"] == 6 and shown["briefings"] == 3

    def test_a_past_month_and_its_own_key_workspace(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        add_runs(db, world.org_a, world.analyst_a, 6, clock.now - timedelta(days=20))
        october = client.get("/org/billing/summary?month=2026-10", headers=owner(world)).json()
        assert self.lines(october)["questions_used"] == 6
        assert october["period"]["end"] == "2026-10-31"
        world.org_a.plan = "byok"
        db.commit()
        now = client.get("/org/billing/summary", headers=owner(world)).json()
        assert now["plan"]["key"] == "byok"
        assert self.lines(now)["questions_included"] is None and "over_limit" not in self.lines(now)

    def test_a_plan_change_inside_the_month_is_listed_and_older_months_keep_their_plan(
        self,
        client: TestClient,
        db: DbSession,
        world: World,
        clock: FakeClock,
        llm_factory: FakeLLMFactory,
    ) -> None:
        put_key(client, world)
        client.put("/org/billing/plan", json={"plan": "byok"}, headers=owner(world))
        db.expire_all()
        # The change happened "now" by the real clock; put it on the fake clock's month for the test.
        change = events(db, world.org_a, "plan_changed")[0]
        change.created_at = clock.now
        db.commit()
        body = client.get("/org/billing/summary", headers=owner(world)).json()
        assert [(c["from"], c["to"]) for c in body["plan_changes"]] == [("managed", "byok")]
        october = client.get("/org/billing/summary?month=2026-10", headers=owner(world)).json()
        assert october["plan"]["key"] == "managed"  # it was Managed until the November change

    def test_a_future_month_is_refused(self, client: TestClient, world: World) -> None:
        response = client.get("/org/billing/summary?month=2027-01", headers=owner(world))
        assert response.status_code == 422 and response.json()["error"]["code"] == "future_month"

    def test_csv_has_one_row_per_line_and_is_logged(
        self, client: TestClient, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        add_runs(db, world.org_a, world.analyst_a, 3, clock.now)
        response = client.get("/org/billing/summary.csv?month=2026-11", headers=owner(world))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="usage-2026-11.csv"' in response.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0] == [
            "workspace",
            "month",
            "plan",
            "line",
            "description",
            "quantity",
            "unit",
            "status",
        ]
        by_line = {r[3]: r for r in rows[1:]}
        assert by_line["questions_used"][5] == "3" and by_line["amount_due"][5] == "0"
        assert {r[0] for r in rows[1:]} == {"UrbanCart"} and {r[7] for r in rows[1:]} == {
            "draft_no_payment"
        }
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == "billing.summary_exported")
        ).one()
        assert entry.details == {"month": "2026-11"}

    def test_csv_never_runs_a_workspace_name_as_a_formula(
        self, client: TestClient, db: DbSession, world: World
    ) -> None:
        world.org_a.name = '=HYPERLINK("http://evil.example","x")'
        db.commit()
        text = client.get("/org/billing/summary.csv", headers=owner(world)).text
        assert "'=HYPERLINK" in text and "\n=HYPERLINK" not in text


class TestClosingTheMonth:
    def test_last_month_is_frozen_once_and_later_changes_do_not_alter_it(
        self,
        runtime: ConnectorRuntime,
        client: TestClient,
        db: DbSession,
        world: World,
        clock: FakeClock,
    ) -> None:
        add_runs(db, world.org_a, world.analyst_a, 4, clock.now)  # November
        december = clock.now + timedelta(
            days=23
        )  # 2 December (the clock stays put: the token is still valid)
        assert summary.close_months(runtime, december) == 2  # both workspaces
        assert summary.close_months(runtime, december) == 0  # once per workspace and month
        [frozen] = events(db, world.org_a, "usage_summary")
        assert (frozen.period, frozen.provider, frozen.delivery) == (
            "2026-11",
            "stand-in",
            "not_sent",
        )
        assert frozen.details["lines"][1] == {
            "code": "questions_used",
            "description": "Questions and long analyses on the platform's AI",
            "quantity": 4,
            "unit": "questions",
        }
        # Usage that arrives late is not in the frozen record, and the screen shows the frozen one.
        add_runs(db, world.org_a, world.analyst_a, 5, datetime(2026, 11, 20, tzinfo=UTC))
        body = client.get("/org/billing/summary?month=2026-11", headers=owner(world)).json()
        assert body["frozen_at"] is not None
        assert {line["code"]: line["quantity"] for line in body["lines"]}["questions_used"] == 4
        assert "delivery_note" not in str(body)

    def test_a_workspace_created_after_the_month_has_no_summary(
        self, runtime: ConnectorRuntime, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        world.org_b.created_at = datetime(2026, 12, 1, 5, 0, tzinfo=UTC)
        db.commit()
        clock.advance(days=23)
        assert summary.close_months(runtime, clock.now) == 1
        assert events(db, world.org_b, "usage_summary") == []

    def test_january_closes_december_of_the_year_before(
        self, runtime: ConnectorRuntime, db: DbSession, world: World, clock: FakeClock
    ) -> None:
        clock.now = datetime(2027, 1, 3, 9, 0, tzinfo=UTC)
        summary.close_months(runtime, clock.now)
        assert [e.period for e in events(db, world.org_a, "usage_summary")] == ["2026-12"]

    def test_one_failing_workspace_does_not_stop_the_others(
        self,
        runtime: ConnectorRuntime,
        db: DbSession,
        world: World,
        clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = summary.build

        def sometimes(db_: DbSession, org: Organization, month: Any) -> dict[str, Any]:
            if org.id == world.org_a.id:
                raise RuntimeError("boom")
            return real(db_, org, month)

        monkeypatch.setattr(summary, "build", sometimes)
        clock.advance(days=23)
        assert summary.close_months(runtime, clock.now) == 1
        assert [e.org_id for e in events(db, world.org_b, "usage_summary")] == [world.org_b.id]


# ------------------------------------------------------------------------ the stand-in and "no payments"
class TestStandIn:
    def test_the_only_provider_sends_nothing(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = billing_provider.get_provider(settings)
        assert isinstance(provider, billing_provider.StandInProvider)
        assert (provider.name, provider.live) == ("stand-in", False)

        def no_network(*_: Any, **__: Any) -> None:
            raise AssertionError("the stand-in tried to reach the network")

        monkeypatch.setattr(socket, "socket", no_network)
        monkeypatch.setattr(socket, "create_connection", no_network)
        result = provider.deliver(billing_provider.Offered("plan_changed", "org", None, {}))
        assert result.status == "not_sent" and "nothing was sent or charged" in result.note

    def test_no_other_provider_can_be_configured(self, settings: Settings) -> None:
        assert settings.billing_provider == "stand_in"
        with pytest.raises(ValidationError):
            Settings(**{**settings.model_dump(), "billing_provider": "stripe"})

    def test_the_billing_code_has_no_payment_or_network_code(self) -> None:
        """No gateway can hide in the billing package: no HTTP client, no payment SDK, no card or key fields."""
        import ast
        from pathlib import Path

        root = Path(billing_provider.__file__).parent
        modules: set[str] = set()
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module.split(".")[0])
        assert not modules & {
            "httpx",
            "requests",
            "urllib",
            "aiohttp",
            "socket",
            "stripe",
            "razorpay",
        }
        text = "\n".join(p.read_text() for p in root.glob("*.py")).lower()
        for word in ("card_number", "cvv", "api_key", "secret", "paypal"):
            assert word not in text, word
