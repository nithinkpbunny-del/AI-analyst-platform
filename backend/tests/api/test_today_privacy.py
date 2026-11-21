"""Today (BUILD_SPEC 11a.8) and privacy (BUILD_SPEC 6.6: data export, workspace deletion with a 7-day grace)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app import templates_industry
from app.config import Settings
from app.data.objects import MemoryObjectStore
from app.email.sender import MemoryEmailSender
from app.llm.fake import FakeLLMFactory
from app.models import (
    AuditLog,
    Connection,
    Conversation,
    Membership,
    Message,
    Organization,
    Report,
    Secret,
    Snapshot,
    User,
)
from app.privacy import deletion
from tests.api.test_chat import answer_turn, ask, kpi_turn, orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, add_member, build_world, token_for


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


class TestToday:
    def test_a_new_workspace_shows_the_checklist(self, client: TestClient, world: World) -> None:
        body = client.get("/today", headers=world.headers("owner_a")).json()
        assert body["checklist"] == {
            "company": False,
            "connect": False,
            "dictionary": False,
            "ask": False,
            "done": False,
        }
        assert body["briefing"] is None and body["health_check"].startswith(
            "Give me a health check"
        )
        # Always three (M19): the data question first, then general ones.
        assert body["suggestions"] == [
            "What can you tell me about my data?",
            "Were there any unusual days last month?",
            "What is working well in the business right now?",
        ]

    def test_a_known_kind_of_business_gets_its_templates_questions(
        self, client: TestClient, world: World
    ) -> None:
        """M17: with no data yet, the suggestions are the template's, not the generic one."""
        headers = world.headers("owner_a")
        assert (
            client.patch("/org", json={"industry": "fuel_station"}, headers=headers).status_code
            == 200
        )
        body = client.get("/today", headers=headers).json()
        assert body["checklist"]["company"] is True
        assert body["suggestions"][0] == "What can you tell me about my data?"
        assert (
            body["suggestions"][1:] == templates_industry.load("fuel_station").starter_questions[:2]
        )
        # The generic template adds nothing.
        client.patch("/org", json={"industry": "other"}, headers=headers)
        assert client.get("/today", headers=headers).json()["suggestions"] == [
            "What can you tell me about my data?",
            "Were there any unusual days last month?",
            "What is working well in the business right now?",
        ]

    def test_steps_tick_as_they_are_done(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory
    ) -> None:
        headers = world.headers("owner_a")
        assert client.patch("/org", json={"industry": "d2c"}, headers=headers).status_code == 200
        upload(client, headers, "orders.csv", orders_csv())
        assert client.post("/dictionary/draft/confirm", headers=headers).status_code == 200
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        ask(client, world)
        body = client.get("/today", headers=headers).json()
        assert body["checklist"]["done"] is True
        assert any("change last month" in s for s in body["suggestions"])
        # In words, never keys (M19): "sum_total_price" and "sales_channel" are read as names.
        assert len(body["suggestions"]) == 3
        assert body["suggestions"][0] == "How did total price change last month, and why?"
        assert (
            body["suggestions"][1] == "Which sales channel grew total price the most last quarter?"
        )
        assert not any("_" in s for s in body["suggestions"])
        # Another person's checklist counts their own first question.
        assert (
            client.get("/today", headers=world.headers("analyst_a")).json()["checklist"]["ask"]
            is False
        )

    def test_the_latest_briefing_and_its_findings(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        db.add(
            Report(
                org_id=world.org_a.id,
                user_id=world.owner_a.id,
                title="Morning briefing, 13 Nov 2026",
                source="briefing",
                body={
                    "kind": "briefing",
                    "headline": "Revenue fell 54% [r3] on 15 Aug.",
                    "sections": [],
                    "growth": [{"segment": "West × Beauty"}],  # noqa: RUF001
                    "unusual": [{"metric": "revenue", "series": "App", "bucket": "2026-08-15"}],
                },
                provenance={},
                quality_warnings=[],
            )
        )
        db.commit()
        body = client.get("/today", headers=world.headers("owner_a")).json()
        assert body["briefing"]["body"]["headline"].startswith("Revenue fell")
        assert body["suggestions"][:2] == [
            "Why is West × Beauty growing, and can we repeat it elsewhere?",  # noqa: RUF001
            "What happened to revenue in App on 2026-08-15?",
        ]
        # Someone else's briefing is not on their Today.
        assert client.get("/today", headers=world.headers("analyst_a")).json()["briefing"] is None

    def test_everyone_has_a_today(self, client: TestClient, world: World) -> None:
        assert client.get("/today", headers=world.headers("viewer_a")).status_code == 200
        assert client.get("/today").status_code == 401


def _seed(client: TestClient, world: World, llm_factory: FakeLLMFactory) -> None:
    headers = world.headers("owner_a")
    upload(client, headers, "orders.csv", orders_csv())
    llm_factory.next_script = [kpi_turn(), answer_turn()]
    ask(client, world)
    client.put(
        "/org/ai/key",
        json={"provider": "claude", "api_key": "sk-ant-urbancart-secret-key-0000000000"},
        headers=headers,
    )


class TestExport:
    def test_zip_has_the_data_and_no_secrets(
        self, client: TestClient, world: World, llm_factory: FakeLLMFactory, db: DbSession
    ) -> None:
        _seed(client, world, llm_factory)
        response = client.get("/org/export.zip", headers=world.headers("owner_a"))
        assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(archive.namelist())
        assert {
            "README.txt",
            "workspace.json",
            "members.json",
            "connections.json",
            "conversations.json",
        } <= names
        assert "activity_log.csv" in names
        everything = b"".join(archive.read(n) for n in names).decode()
        assert "sk-ant-urbancart" not in everything and "password" not in everything.replace(
            "passwords", ""
        )
        assert "Chaiwala" not in everything and "chaiwala.in" not in everything
        conversations = json.loads(archive.read("conversations.json"))
        assert conversations[0]["messages"][0]["text"] == "How much did we sell in January?"
        assert db.scalars(select(AuditLog).where(AuditLog.action == "org.data_exported")).one()

    def test_owners_only_and_rate_limited(self, client: TestClient, world: World) -> None:
        assert client.get("/org/export.zip", headers=world.headers("analyst_a")).status_code == 403
        codes = [
            client.get("/org/export.zip", headers=world.headers("owner_a")).status_code
            for _ in range(6)
        ]
        assert codes[-1] == 429 and codes[0] == 200


class TestDeletion:
    def test_request_cancel_and_emails(
        self, client: TestClient, world: World, outbox: MemoryEmailSender, db: DbSession
    ) -> None:
        headers = world.headers("owner_a")
        wrong = client.post("/org/deletion", json={"confirm_name": "Urban"}, headers=headers)
        assert wrong.status_code == 422
        response = client.post("/org/deletion", json={"confirm_name": "urbancart"}, headers=headers)
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["deletion_requested_at"] and body["deletion_due_at"]
        assert outbox.to("asha@urbancart.in")[-1].subject == "Your workspace will be deleted"
        assert (
            client.post(
                "/org/deletion", json={"confirm_name": "UrbanCart"}, headers=headers
            ).status_code
            == 409
        )
        assert client.get("/org", headers=world.headers("viewer_a")).json()["deletion_due_at"]
        cancelled = client.delete("/org/deletion", headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()["deletion_requested_at"] is None
        assert client.delete("/org/deletion", headers=headers).status_code == 409
        actions = set(db.scalars(select(AuditLog.action)))
        assert {"org.deletion_requested", "org.deletion_cancelled"} <= actions

    def test_only_owners_with_two_step(
        self, client: TestClient, world: World, db: DbSession, settings: Settings, clock: FakeClock
    ) -> None:
        body = {"confirm_name": "UrbanCart"}
        nisha = add_member(
            db, world.org_a.id, email="nisha@urbancart.in", role="owner", name="Nisha"
        )
        no_2fa = {
            "Authorization": f"Bearer {token_for(db, settings, clock.now, nisha, world.org_a.id)}"
        }
        for headers in (world.headers("analyst_a"), world.headers("viewer_a"), no_2fa):
            assert client.post("/org/deletion", json=body, headers=headers).status_code == 403
            assert client.delete("/org/deletion", headers=headers).status_code == 403
        assert client.delete("/org/deletion").status_code == 401

    def test_purge_after_the_grace_period_deletes_everything(
        self,
        client: TestClient,
        world: World,
        llm_factory: FakeLLMFactory,
        db: DbSession,
        runtime: Any,
        object_store: MemoryObjectStore,
        clock: FakeClock,
    ) -> None:
        _seed(client, world, llm_factory)
        client.post(
            "/org/deletion", json={"confirm_name": "UrbanCart"}, headers=world.headers("owner_a")
        )
        org_a, org_b = world.org_a.id, world.org_b.id
        assert object_store.stored_keys(f"orgs/{org_a}/")
        # Not yet: the grace period is 7 days.
        assert deletion.purge_due(runtime, clock.now + timedelta(days=6)) == 0
        assert db.get(Organization, org_a) is not None
        assert deletion.purge_due(runtime, clock.now + timedelta(days=7, minutes=1)) == 1
        db.expire_all()
        assert db.get(Organization, org_a) is None
        for model in (Membership, Connection, Conversation, Message, Report, Secret, Snapshot):
            count = db.scalar(select(func.count()).select_from(model).where(model.org_id == org_a))
            assert count == 0, model.__tablename__
        assert object_store.stored_keys(f"orgs/{org_a}/") == []
        # People's accounts stay (they are global); the other workspace is untouched.
        assert db.scalar(select(User).where(User.email == "asha@urbancart.in")) is not None
        assert db.get(Organization, org_b) is not None
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "org.deleted")).one()
        assert entry.user_id is None and "UrbanCart" not in json.dumps(entry.details)
        assert entry.details["connections"] == 1
        # The workspace's earlier activity is kept, anonymised (D124): what and when, never who, where or on what.
        earlier = list(
            db.scalars(
                select(AuditLog).where(AuditLog.org_id == org_a, AuditLog.action != "org.deleted")
            )
        )
        assert earlier and {e.action for e in earlier} >= {"question.asked", "connection.added"}
        for kept in earlier:
            assert kept.user_id is None and kept.ip is None and kept.user_agent is None
            assert kept.target_id is None and kept.details == {"anonymised": True}
        assert entry.details["activity_entries_anonymised"] == len(earlier)
        # The other workspace's entries are untouched.
        others = list(db.scalars(select(AuditLog).where(AuditLog.org_id == org_b)))
        assert all(e.details != {"anonymised": True} for e in others)
        # The activity log still refuses changes (append-only).
        with pytest.raises(Exception):  # noqa: B017
            db.execute(text("DELETE FROM audit_log"))
        db.rollback()
