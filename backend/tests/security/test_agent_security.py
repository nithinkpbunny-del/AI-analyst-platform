"""Security of the Ask surface (P2, P3, P6, BUILD_SPEC 6.2, 6.3, 6.4, 6.6; M8).

* RLS on conversations, messages and saved questions;
* the agent sees only what the asking person may see: another workspace's tables, hidden connections and paused
  connections never reach a tool or the prompt;
* nothing a question does changes a stored file or snapshot (read-only), whatever SQL the model writes;
* the prompt holds the dictionary and aggregates, never raw rows beyond the few sample rows, never secrets;
* the fake client is the only one used.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.agent.access import access_context, visible_dictionary
from app.config import Settings
from app.db.tenant import scope_to_org
from app.dictionary import service as dictionary_service
from app.llm.base import ToolCall
from app.llm.fake import FakeLLM, FakeLLMFactory, FakeTurn
from app.models import Connection, Conversation, Message, SavedQuestion, Snapshot
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

ORDERS = (
    "id,created_at,customer_email,total_price,currency\n"
    + "\n".join(
        f"{i},2026-01-{1 + i % 28:02d},buyer{i}@example.com,{100 + i},INR" for i in range(30)
    )
    + "\n"
).encode()


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def both(client: TestClient, world: World) -> dict[str, str]:
    """An orders file in each workspace."""
    ids = {}
    for who in ("owner_a", "owner_b"):
        response = upload(client, world.headers(who), "orders.csv", ORDERS)
        assert response.status_code in (200, 201), response.text
        ids[who] = response.json()["id"]
    return ids


def scoped(factory: sessionmaker[DbSession], org_id: uuid.UUID) -> DbSession:
    session = factory()
    scope_to_org(session, org_id)
    return session


class TestRls:
    def test_chat_rows_of_another_workspace_are_invisible(
        self,
        world: World,
        db: DbSession,
        session_factory: sessionmaker[DbSession],
        clock: FakeClock,
    ) -> None:
        for org, user in ((world.org_a, world.owner_a), (world.org_b, world.owner_b)):
            conversation = Conversation(
                org_id=org.id,
                user_id=user.id,
                title="t",
                lens="business",
                last_message_at=clock.now,
            )
            db.add(conversation)
            db.flush()
            db.add(
                Message(
                    org_id=org.id,
                    conversation_id=conversation.id,
                    role="user",
                    content="secret",
                    status="done",
                )
            )
            db.add(SavedQuestion(org_id=org.id, user_id=user.id, text="q"))
        db.commit()
        with scoped(session_factory, world.org_a.id) as session:
            for model in (Conversation, Message, SavedQuestion):
                rows: list[Any] = list(session.scalars(select(model)))
                assert rows and {r.org_id for r in rows} == {world.org_a.id}
            other = db.scalars(
                select(Conversation).where(Conversation.org_id == world.org_b.id)
            ).one()
            session.add(
                Message(
                    org_id=world.org_b.id,
                    conversation_id=other.id,
                    role="user",
                    content="x",
                    status="done",
                )
            )
            with pytest.raises(Exception, match="row-level security"):
                session.flush()


class TestWhatTheAgentSees:
    def test_another_workspaces_tables_are_never_visible(
        self, both: dict[str, str], world: World, session_factory: sessionmaker[DbSession]
    ) -> None:
        with scoped(session_factory, world.org_a.id) as session:
            access = access_context(session, world.org_a.id, world.owner_a.id)
            active = dictionary_service.active(session, world.org_a.id)
            assert active.dictionary is not None
            dictionary = active.dictionary.model_copy(deep=True)
        with scoped(session_factory, world.org_b.id) as session:
            theirs = dictionary_service.active(session, world.org_b.id).dictionary
            assert theirs is not None
        # A dictionary that somehow points at the other workspace's dataset still cannot reach it.
        dictionary.datasets["stolen"] = next(iter(theirs.datasets.values())).model_copy()
        visible = visible_dictionary(dictionary, access)
        assert "stolen" not in visible.dictionary.datasets
        assert {str(r.connection_id) for r in visible.tables.values()} == {both["owner_a"]}

    def test_paused_and_ungranted_connections_are_left_out(
        self,
        both: dict[str, str],
        world: World,
        db: DbSession,
        session_factory: sessionmaker[DbSession],
    ) -> None:
        with scoped(session_factory, world.org_a.id) as session:
            assert access_context(session, world.org_a.id, world.analyst_a.id).datasets
        connection = db.get(Connection, uuid.UUID(both["owner_a"]))
        assert connection is not None
        connection.status = "disabled"
        db.commit()
        with scoped(session_factory, world.org_a.id) as session:
            assert access_context(session, world.org_a.id, world.owner_a.id).datasets == {}


class TestReadOnly:
    def test_no_sql_the_model_writes_changes_stored_data(
        self,
        client: TestClient,
        world: World,
        both: dict[str, str],
        llm_factory: FakeLLMFactory,
        db: DbSession,
        object_store: Any,
    ) -> None:
        before = (
            {k: bytes(v) for k, v in object_store.objects.items()}
            if hasattr(object_store, "objects")
            else None
        )
        snapshots = sorted(s.storage_key for s in db.scalars(select(Snapshot)))
        attacks = [
            "DROP TABLE orders",
            "DELETE FROM orders",
            "COPY orders TO '/tmp/stolen.csv'",
            "SELECT * FROM read_csv('/etc/passwd')",
            "ATTACH '/tmp/x.db' AS x",
            "SELECT 1; DROP TABLE orders",
            "UPDATE orders SET total_price = 0",
        ]
        llm_factory.next_script = [
            FakeTurn(
                tool_calls=[
                    ToolCall(f"c{i}", "run_sql", {"sql": sql}) for i, sql in enumerate(attacks)
                ]
            ),
            FakeTurn(
                tool_calls=[ToolCall("c9", "run_sql", {"sql": "SELECT count(*) AS n FROM orders"})]
            ),
            FakeTurn(
                tool_calls=[
                    ToolCall(
                        "f", "give_answer", {"lead": "There are 30 orders [r1].", "evidence": []}
                    )
                ]
            ),
        ]
        response = client.post(
            "/conversations",
            json={"question": "How many orders?"},
            headers=world.headers("owner_a"),
        )
        message = response.json()["messages"][-1]
        assert message["status"] == "done", message
        errors = [t.get("error") for t in message["tool_trace"]]
        assert errors[: len(attacks)] == ["unsafe_sql"] * len(attacks)
        assert message["answer"]["lead"] == "There are 30 orders [r1]."
        db.expire_all()
        assert sorted(s.storage_key for s in db.scalars(select(Snapshot))) == snapshots
        if before is not None:
            assert {k: bytes(v) for k, v in object_store.objects.items()} == before


class TestPrompts:
    def test_no_rows_personal_values_or_secrets_reach_the_model_or_the_logs(
        self,
        client: TestClient,
        world: World,
        both: dict[str, str],
        llm_factory: FakeLLMFactory,
        settings: Settings,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        llm_factory.next_script = [
            FakeTurn(tool_calls=[ToolCall("d", "describe_dataset", {"table": "orders"})]),
            FakeTurn(
                tool_calls=[
                    ToolCall(
                        "f", "give_answer", {"lead": "Orders have an email column.", "evidence": []}
                    )
                ]
            ),
        ]
        client.post(
            "/conversations",
            json={"question": "What is in orders?"},
            headers=world.headers("owner_a"),
        )
        fake: FakeLLM = llm_factory.clients[-1]
        sent = json.dumps(
            [c.system for c in fake.calls] + [repr(m) for c in fake.calls for m in c.messages],
            default=str,
        )
        assert "buyer1@example.com" not in sent and "@example.com" not in sent
        assert "(hidden)" in sent  # the sample rows hide the personal column
        for secret in settings.secret_values():
            assert secret not in sent
            assert secret not in caplog.text
        assert "buyer" not in caplog.text

    def test_the_fake_client_is_the_only_one_used(self, client: TestClient) -> None:
        assert isinstance(client.app.state.llm_factory, FakeLLMFactory)  # type: ignore[attr-defined]
