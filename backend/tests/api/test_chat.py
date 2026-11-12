"""The Ask API end to end with the fake LLM (BUILD_SPEC 7.1, 7.7, 7.8, 6.5, 6.7, X2; M8).

Background jobs run at once (``inline_jobs``): a question is answered before the POST returns. Each test scripts
the fake model; no provider is called.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.llm.base import LLMError, ToolCall
from app.llm.fake import FakeLLMFactory, FakeTurn
from app.models import AuditLog, Conversation, Membership, Message, Organization, UsageEvent
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world

JAN = {"start": "2026-01-01", "end": "2026-01-31"}
TOTAL = "₹6,360"  # sum of total_price in ORDERS


def orders_csv(rows: int = 40) -> bytes:
    lines = ["id,created_at,customer_id,sales_channel,total_price,currency"]
    for i in range(rows):
        lines.append(
            f"{1000 + i},2026-01-{1 + i % 28:02d}T10:00:00+05:30,{7000 + i % 5},"
            f"{'App' if i % 2 else 'Website'},{100 + i * 3}.5,INR"
        )
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def orders(client: TestClient, world: World) -> dict[str, Any]:
    response = upload(client, world.headers("owner_a"), "orders.csv", orders_csv())
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


def kpi_turn() -> FakeTurn:
    return FakeTurn(
        tool_calls=[
            ToolCall(
                "c1",
                "kpi_summary",
                {"metrics": ["sum_total_price"], "period": JAN, "compare": "none"},
            )
        ]
    )


def answer_turn(lead: str = f"Sales in January were {TOTAL} [r1].", **more: Any) -> FakeTurn:
    return FakeTurn(
        tool_calls=[
            ToolCall(
                "c2",
                "give_answer",
                {
                    "lead": lead,
                    "key_figure": TOTAL,
                    "evidence": ["Both channels sold in January"],
                    "next_questions": ["How did February compare?"],
                    **more,
                },
            )
        ]
    )


def ask(
    client: TestClient,
    world: World,
    question: str = "How much did we sell in January?",
    who: str = "owner_a",
    **body: Any,
) -> Any:
    return client.post(
        "/conversations", json={"question": question, **body}, headers=world.headers(who)
    )


def answer_of(body: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = body["messages"][-1]
    assert message["role"] == "assistant"
    return message


class TestAsking:
    def test_a_question_is_answered_with_provenance_progress_and_notes(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        response = ask(client, world)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["title"] == "How much did we sell in January?" and body["lens"] == "business"
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        message = answer_of(body)
        assert message["status"] == "done", message
        answer = message["answer"]
        assert answer["lead"] == f"Sales in January were {TOTAL} [r1]."
        assert answer["key_figure"] == TOTAL and answer["cited"] == ["r1"]
        assert {"code": "dictionary_not_confirmed"} in answer["notes"]
        assert answer["next_questions"] == ["How did February compare?"]
        prov = message["provenance"]["r1"]
        assert prov["tool"] == "kpi_summary"
        assert prov["tables"][0]["connection"] == orders["name"] and prov["tables"][0]["fetched_at"]
        assert [s["code"] for s in message["progress"]][:3] == [
            "reading_question",
            "thinking",
            "computing",
        ]
        assert {"code": "fetching", "table": "orders", "connection": orders["name"]} in message[
            "progress"
        ]
        assert message["progress"][-1] == {"code": "done"}
        trace = message["tool_trace"]
        assert (
            trace[0]["tool"] == "kpi_summary" and trace[0]["result_id"] == "r1" and trace[0]["sql"]
        )
        # Two model calls, both metered as one question run.
        events = list(
            db.scalars(
                select(UsageEvent).where(
                    UsageEvent.org_id == world.org_a.id, UsageEvent.kind == "question"
                )
            )
        )
        assert len(events) == 2 and len({e.run_id for e in events}) == 1
        # The prompt held the dictionary, never the rows.
        call = llm_factory.clients[-1].calls[0]
        assert "table `orders`" in call.system and "7001" not in call.system
        assert call.messages[-1].text == "How much did we sell in January?"  # type: ignore[union-attr]

    def test_the_activity_log_gets_a_hash_and_the_tables_never_the_text(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        ask(client, world, "Secret plans for Diwali sales?")
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "question.asked")).one()
        assert entry.user_id == world.owner_a.id and entry.org_id == world.org_a.id
        assert (
            entry.details["datasets"] == ["orders"] and len(entry.details["question_sha256"]) == 64
        )
        assert "Diwali" not in json.dumps(entry.details) and TOTAL not in json.dumps(entry.details)

    def test_an_answer_can_be_polled(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        body = ask(client, world).json()
        message = answer_of(body)
        polled = client.get(
            f"/conversations/{body['id']}/messages/{message['id']}",
            headers=world.headers("owner_a"),
        )
        assert polled.status_code == 200 and polled.json()["status"] == "done"
        assert (
            client.get(f"/conversations/{body['id']}", headers=world.headers("owner_a")).json()[
                "messages"
            ][1]["id"]
            == message["id"]
        )

    def test_follow_ups_carry_the_earlier_turns(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        first = ask(client, world).json()
        llm_factory.next_script = [answer_turn(lead=f"Still {TOTAL} [r1]?")]
        response = client.post(
            f"/conversations/{first['id']}/messages",
            json={"question": "And by channel?"},
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 201
        messages = llm_factory.clients[-1].calls[0].messages
        texts = [getattr(m, "text", "") for m in messages]
        assert texts[0] == "How much did we sell in January?"
        assert texts[1].startswith(f"Sales in January were {TOTAL}")
        assert texts[-1] == "And by channel?"
        # The second answer cited a figure the second question never computed: removed, with a note.
        follow = answer_of(response.json())
        assert follow["status"] == "failed" or "numbers_removed" in json.dumps(follow["answer"])

    def test_a_follow_up_waits_for_a_running_answer(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        body = ask(client, world).json()
        message = db.get(Message, uuid.UUID(answer_of(body)["id"]))
        assert message is not None
        message.status = "running"
        db.commit()
        response = client.post(
            f"/conversations/{body['id']}/messages",
            json={"question": "Next?"},
            headers=world.headers("owner_a"),
        )
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "answer_in_progress"
        )

    def test_an_answer_whose_job_died_is_reported_after_a_while(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
        clock: FakeClock,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        body = ask(client, world).json()
        message = db.get(Message, uuid.UUID(answer_of(body)["id"]))
        assert message is not None
        message.status, message.started_at = "running", clock.now - timedelta(minutes=31)
        db.commit()
        polled = client.get(
            f"/conversations/{body['id']}/messages/{message.id}", headers=world.headers("owner_a")
        ).json()
        assert polled["status"] == "failed" and polled["error_code"] == "timeout"

    def test_the_same_first_question_moments_later_reuses_the_answer(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        first = answer_of(ask(client, world).json())
        clients = len(llm_factory.clients)
        again = answer_of(ask(client, world, "how much did we  sell in January?").json())
        assert len(llm_factory.clients) == clients  # no AI call
        assert again["reused"] is True and again["answer"] == first["answer"]
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "question.asked")))
        assert [a.details["reused"] for a in audits] == [False, True]
        # Another lens is another question.
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        other = answer_of(ask(client, world, lens="finance").json())
        assert other["reused"] is False

    def test_lenses_change_the_prompt(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        ask(client, world, lens="finance")
        assert "Your lens: Finance" in llm_factory.clients[-1].calls[0].system


class TestRefusals:
    def test_too_many_questions_a_minute(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        settings: Settings,
        llm_factory: FakeLLMFactory,
    ) -> None:
        settings.questions_per_user_per_minute = 1
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        assert ask(client, world).status_code == 201
        response = ask(client, world, "Another one?")
        assert (
            response.status_code == 429 and response.json()["error"]["code"] == "too_many_questions"
        )
        # The limit is per person.
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        assert ask(client, world, who="analyst_a").status_code == 201

    def test_the_monthly_limit_is_checked_first(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        db: DbSession,
        llm_factory: FakeLLMFactory,
    ) -> None:
        org = db.get(Organization, world.org_a.id)
        assert org is not None
        org.monthly_question_limit = 0
        db.commit()
        created = len(llm_factory.created)
        response = ask(client, world)
        assert (
            response.status_code == 429
            and response.json()["error"]["code"] == "usage_limit_reached"
        )
        assert len(llm_factory.created) == created
        assert db.scalars(select(Conversation)).all() == []

    def test_without_ai_nothing_is_queued(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        settings: Settings,
        db: DbSession,
    ) -> None:
        settings.gemini_api_key = SecretStr("")
        response = ask(client, world)
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "ai_not_configured"
        )
        assert db.scalars(select(Conversation)).all() == []

    def test_viewers_cannot_ask(self, client: TestClient, world: World) -> None:
        assert ask(client, world, who="viewer_a").status_code == 403
        assert client.get("/conversations", headers=world.headers("viewer_a")).status_code == 403

    @pytest.mark.parametrize(
        "body",
        [
            {"question": ""},
            {"question": "   "},
            {"question": "x" * 2001},
            {"question": "ok", "lens": "astrology"},
            {"q": "x"},
        ],
    )
    def test_bad_questions(self, client: TestClient, world: World, body: dict[str, Any]) -> None:
        assert (
            client.post("/conversations", json=body, headers=world.headers("owner_a")).status_code
            == 422
        )


class TestFailures:
    def test_a_quota_error_fails_the_answer_with_its_kind(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [LLMError("quota", "gemini", "daily quota")]
        message = answer_of(ask(client, world).json())
        assert message["status"] == "failed" and message["error_code"] == "llm:quota"
        assert len(llm_factory.clients[-1].calls) == 1  # never retried by the agent

    def test_no_dictionary_yet(self, client: TestClient, world: World) -> None:
        message = answer_of(ask(client, world).json())
        assert message["status"] == "failed" and message["error_code"] == "no_dictionary"

    def test_a_person_who_may_see_no_connection(
        self, client: TestClient, world: World, orders: dict[str, Any], db: DbSession
    ) -> None:
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"
        db.commit()
        message = answer_of(ask(client, world, who="analyst_a").json())
        assert message["status"] == "failed" and message["error_code"] == "no_sources"

    def test_a_removed_member_gets_no_answer(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        db: DbSession,
        llm_factory: FakeLLMFactory,
    ) -> None:
        from app.agent import service as agent_service

        llm_factory.next_script = [kpi_turn(), answer_turn()]
        body = ask(client, world, who="analyst_a").json()
        message = db.get(Message, uuid.UUID(answer_of(body)["id"]))
        assert message is not None
        message.status = "queued"
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.status = "removed"
        db.commit()
        runtime = client.app.state.connector_runtime  # type: ignore[attr-defined]
        status = agent_service.answer_question(
            runtime, llm_factory, agent_service.Asked(world.org_a.id, message.id)
        )
        assert status == "failed"
        db.refresh(message)
        assert message.error_code == "no_access"


class TestHistory:
    def test_history_is_per_person(
        self, client: TestClient, world: World, orders: dict[str, Any], llm_factory: FakeLLMFactory
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        mine = ask(client, world).json()
        assert [
            c["id"]
            for c in client.get("/conversations", headers=world.headers("owner_a")).json()[
                "conversations"
            ]
        ] == [mine["id"]]
        assert (
            client.get("/conversations", headers=world.headers("analyst_a")).json()["conversations"]
            == []
        )
        for who in ("analyst_a", "owner_b"):
            assert (
                client.get(f"/conversations/{mine['id']}", headers=world.headers(who)).status_code
                == 404
            )
            assert (
                client.delete(
                    f"/conversations/{mine['id']}", headers=world.headers(who)
                ).status_code
                == 404
            )

    def test_rename_change_lens_and_delete(
        self,
        client: TestClient,
        world: World,
        orders: dict[str, Any],
        llm_factory: FakeLLMFactory,
        db: DbSession,
    ) -> None:
        llm_factory.next_script = [kpi_turn(), answer_turn()]
        body = ask(client, world).json()
        headers = world.headers("owner_a")
        patched = client.patch(
            f"/conversations/{body['id']}",
            json={"title": "  January   sales ", "lens": "finance"},
            headers=headers,
        )
        assert (
            patched.status_code == 200
            and patched.json()["title"] == "January sales"
            and patched.json()["lens"] == "finance"
        )
        assert (
            client.patch(
                f"/conversations/{body['id']}", json={"lens": "x"}, headers=headers
            ).status_code
            == 422
        )
        assert client.delete(f"/conversations/{body['id']}", headers=headers).status_code == 204
        assert db.scalars(select(Message)).all() == []


class TestSavedQuestions:
    def test_save_list_run_again_and_delete(self, client: TestClient, world: World) -> None:
        headers = world.headers("analyst_a")
        created = client.post(
            "/saved-questions",
            json={"text": " Why did  South fall? ", "lens": "business"},
            headers=headers,
        )
        assert created.status_code == 201 and created.json()["text"] == "Why did South fall?"
        again = client.post(
            "/saved-questions", json={"text": "Why did South fall?"}, headers=headers
        )
        assert again.status_code == 200 and again.json()["id"] == created.json()["id"]
        listed = client.get("/saved-questions", headers=headers).json()["questions"]
        assert [q["text"] for q in listed] == ["Why did South fall?"]
        assert (
            client.get("/saved-questions", headers=world.headers("owner_a")).json()["questions"]
            == []
        )
        qid = created.json()["id"]
        assert (
            client.delete(f"/saved-questions/{qid}", headers=world.headers("owner_a")).status_code
            == 404
        )
        assert client.delete(f"/saved-questions/{qid}", headers=headers).status_code == 204
        assert (
            client.post("/saved-questions", json={"text": ""}, headers=headers).status_code == 422
        )
        assert (
            client.post(
                "/saved-questions", json={"text": "x"}, headers=world.headers("viewer_a")
            ).status_code
            == 403
        )


# ------------------------------------------------------------------------------- per-person id routes
PERSONAL = [
    ("GET", "/conversations/{conversation}"),
    ("PATCH", "/conversations/{conversation}"),
    ("DELETE", "/conversations/{conversation}"),
    ("POST", "/conversations/{conversation}/messages"),
    ("GET", "/conversations/{conversation}/messages/{message}"),
    ("DELETE", "/saved-questions/{saved}"),
]
BODIES = {"PATCH": {"title": "New"}, "POST": {"question": "And then?"}}


@pytest.fixture
def owned(db: DbSession, world: World, clock: FakeClock) -> dict[str, dict[str, str]]:
    """A conversation (with an answer) and a saved question for owner_a, analyst_a and owner_b."""
    from app.models import SavedQuestion

    out: dict[str, dict[str, str]] = {}
    for who, user, org in (
        ("owner_a", world.owner_a, world.org_a),
        ("analyst_a", world.analyst_a, world.org_a),
        ("owner_b", world.owner_b, world.org_b),
    ):
        conversation = Conversation(
            org_id=org.id, user_id=user.id, title="Q", lens="business", last_message_at=clock.now
        )
        db.add(conversation)
        db.flush()
        message = Message(
            org_id=org.id,
            conversation_id=conversation.id,
            user_id=user.id,
            role="assistant",
            content="",
            status="done",
        )
        saved = SavedQuestion(org_id=org.id, user_id=user.id, text="Q", lens="business")
        db.add_all([message, saved])
        db.flush()
        out[who] = {
            "conversation": str(conversation.id),
            "message": str(message.id),
            "saved": str(saved.id),
        }
    db.commit()
    return out


@pytest.mark.parametrize(("method", "path"), PERSONAL)
class TestPersonalRoutes:
    def url(self, path: str, ids: dict[str, str]) -> str:
        return path.format(**ids)

    def test_signed_out(
        self, client: TestClient, owned: dict[str, dict[str, str]], method: str, path: str
    ) -> None:
        assert (
            client.request(
                method, self.url(path, owned["owner_a"]), json=BODIES.get(method)
            ).status_code
            == 401
        )

    def test_viewers(
        self,
        client: TestClient,
        world: World,
        owned: dict[str, dict[str, str]],
        method: str,
        path: str,
    ) -> None:
        response = client.request(
            method,
            self.url(path, owned["owner_a"]),
            json=BODIES.get(method),
            headers=world.headers("viewer_a"),
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("other", ["analyst_a", "owner_b"])
    def test_someone_elses_ids_are_404(
        self,
        client: TestClient,
        world: World,
        owned: dict[str, dict[str, str]],
        method: str,
        path: str,
        other: str,
    ) -> None:
        response = client.request(
            method,
            self.url(path, owned[other]),
            json=BODIES.get(method),
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}

    def test_malformed_ids_are_422(
        self, client: TestClient, world: World, method: str, path: str
    ) -> None:
        url = path.format(conversation="x", message="y", saved="z")
        assert (
            client.request(
                method, url, json=BODIES.get(method), headers=world.headers("owner_a")
            ).status_code
            == 422
        )

    def test_own_ids_work(
        self,
        client: TestClient,
        world: World,
        owned: dict[str, dict[str, str]],
        method: str,
        path: str,
        llm_factory: FakeLLMFactory,
    ) -> None:
        llm_factory.next_script = [LLMError("unavailable", "gemini")]
        response = client.request(
            method,
            self.url(path, owned["owner_a"]),
            json=BODIES.get(method),
            headers=world.headers("owner_a"),
        )
        assert response.status_code in (200, 201, 204), response.text


def test_stale_window_is_long_enough_for_a_question() -> None:
    from app.agent.service import STALE_AFTER
    from app.tasks.jobs import ANSWER_TIME_LIMIT_SECONDS

    assert timedelta(seconds=ANSWER_TIME_LIMIT_SECONDS) < STALE_AFTER
