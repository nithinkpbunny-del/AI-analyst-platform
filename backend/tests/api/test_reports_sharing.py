"""Exports and sharing (BUILD_SPEC X3, 6.2; M10): PDF/Excel of answers and reports, sharing inside the workspace
with the recipient's permissions, Viewers exporting only what was shared with them, 404 for everyone else."""

from __future__ import annotations

import io
from typing import Any, ClassVar

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.llm.fake import FakeLLMFactory
from app.models import AuditLog, Membership, Report, Share
from tests.api.test_chat import answer_turn, ask, kpi_turn, orders_csv
from tests.conftest import FakeClock
from tests.datasources import upload
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def answer(client: TestClient, world: World, llm_factory: FakeLLMFactory) -> dict[str, str]:
    """A finished answer of owner_a: ``{"conversation": ..., "message": ...}``."""
    assert upload(client, world.headers("owner_a"), "orders.csv", orders_csv()).status_code == 201
    llm_factory.next_script = [kpi_turn(), answer_turn()]
    body = ask(client, world).json()
    message = body["messages"][-1]
    assert message["status"] == "done", message
    return {"conversation": body["id"], "message": message["id"]}


def export_url(ids: dict[str, str], fmt: str = "pdf") -> str:
    return f"/conversations/{ids['conversation']}/messages/{ids['message']}/export?format={fmt}"


def share(
    client: TestClient, world: World, ids: dict[str, str], *users: Any, who: str = "owner_a"
) -> Any:
    return client.post(
        "/shares",
        json={
            "object_type": "message",
            "object_id": ids["message"],
            "user_ids": [str(u) for u in users],
        },
        headers=world.headers(who),
    )


class TestExports:
    def test_pdf_and_excel_of_an_answer(
        self, client: TestClient, world: World, answer: dict[str, str], db: DbSession
    ) -> None:
        pdf = client.get(export_url(answer), headers=world.headers("owner_a"))
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert pdf.headers["content-type"] == "application/pdf"
        assert (
            'filename="how-much-did-we-sell-in-january.pdf"' in pdf.headers["content-disposition"]
        )
        xlsx = client.get(export_url(answer, "xlsx"), headers=world.headers("owner_a"))
        book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
        assert book.sheetnames[:2] == ["Summary", "Sources"]
        actions = list(
            db.scalars(select(AuditLog.details).where(AuditLog.action == "export.created"))
        )
        assert [a["format"] for a in actions] == ["pdf", "xlsx"]

    def test_bad_format_is_422(
        self, client: TestClient, world: World, answer: dict[str, str]
    ) -> None:
        assert (
            client.get(export_url(answer, "docx"), headers=world.headers("owner_a")).status_code
            == 422
        )

    def test_someone_elses_answer_is_not_found(
        self, client: TestClient, world: World, answer: dict[str, str]
    ) -> None:
        for who in ("analyst_a", "owner_b"):
            assert client.get(export_url(answer), headers=world.headers(who)).status_code == 404

    def test_viewers_cannot_export_what_was_not_shared(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        report = Report(
            org_id=world.org_a.id,
            user_id=world.viewer_a.id,
            title="Morning briefing",
            source="briefing",
            body={"kind": "briefing", "headline": "Quiet.", "sections": []},
            provenance={},
            quality_warnings=[],
        )
        db.add(report)
        db.commit()
        assert (
            client.get(f"/reports/{report.id}", headers=world.headers("viewer_a")).status_code
            == 200
        )
        response = client.get(
            f"/reports/{report.id}/export?format=pdf", headers=world.headers("viewer_a")
        )
        assert response.status_code == 403

    def test_report_files_are_made_once(
        self, client: TestClient, world: World, db: DbSession, object_store: Any
    ) -> None:
        report = Report(
            org_id=world.org_a.id,
            user_id=world.owner_a.id,
            title="Q3 review",
            source="task",
            body={"kind": "answer", "question": "Q3?", "answer": {"lead": "Fine.", "evidence": []}},
            provenance={},
            quality_warnings=[],
        )
        db.add(report)
        db.commit()
        first = client.get(
            f"/reports/{report.id}/export?format=xlsx", headers=world.headers("owner_a")
        )
        assert first.status_code == 200
        db.expire_all()
        stored = db.get(Report, report.id)
        assert stored is not None and stored.xlsx_key and object_store.exists(stored.xlsx_key)
        second = client.get(
            f"/reports/{report.id}/export?format=xlsx", headers=world.headers("owner_a")
        )
        assert second.content == first.content
        assert (
            client.get(f"/reports/{report.id}", headers=world.headers("owner_b")).status_code == 404
        )


class TestSharing:
    def test_share_with_an_analyst_and_a_viewer(
        self,
        client: TestClient,
        world: World,
        answer: dict[str, str],
        outbox: MemoryEmailSender,
        db: DbSession,
    ) -> None:
        listed = client.get(
            f"/shares?object_type=message&object_id={answer['message']}",
            headers=world.headers("owner_a"),
        ).json()
        emails = {c["email"]: c for c in listed["candidates"]}
        assert set(emails) == {
            "ravi@urbancart.in",
            "lata@urbancart.in",
        }  # never another workspace's people
        assert all(c["can_see"] for c in emails.values())
        response = share(client, world, answer, world.analyst_a.id, world.viewer_a.id)
        assert response.status_code == 201, response.text
        assert {s["email"] for s in response.json()["shares"]} == {
            "ravi@urbancart.in",
            "lata@urbancart.in",
        }
        mail = outbox.to("lata@urbancart.in")[-1]
        assert "shared an answer with you" in mail.subject and "/shared/" in mail.text
        # Sharing again changes nothing.
        assert len(share(client, world, answer, world.viewer_a.id).json()["shares"]) == 2
        assert (
            len(db.scalars(select(AuditLog).where(AuditLog.action == "share.created")).all()) == 2
        )

        items = client.get("/shared", headers=world.headers("viewer_a")).json()["items"]
        assert len(items) == 1 and items[0]["shared_by"] == "Asha Rao"
        share_id = items[0]["id"]
        shared = client.get(f"/shared/{share_id}", headers=world.headers("viewer_a")).json()
        assert shared["answer"]["lead"].startswith("Sales in January") and shared["question"]
        # A Viewer may export what was shared with them (BUILD_SPEC 6.2).
        pdf = client.get(f"/shared/{share_id}/export?format=pdf", headers=world.headers("viewer_a"))
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        # Only the recipient sees it through that share.
        for who in ("analyst_a", "owner_b"):
            assert client.get(f"/shared/{share_id}", headers=world.headers(who)).status_code == 404

    def test_recipients_who_cannot_see_the_sources_are_refused(
        self, client: TestClient, world: World, answer: dict[str, str], db: DbSession
    ) -> None:
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"  # and no grants
        db.commit()
        listed = client.get(
            f"/shares?object_type=message&object_id={answer['message']}",
            headers=world.headers("owner_a"),
        ).json()
        ravi = next(c for c in listed["candidates"] if c["email"] == "ravi@urbancart.in")
        assert ravi["can_see"] is False
        response = share(client, world, answer, world.analyst_a.id)
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "cannot_see_sources"
        )

    def test_shrinking_access_hides_a_shared_item(
        self, client: TestClient, world: World, answer: dict[str, str], db: DbSession
    ) -> None:
        share(client, world, answer, world.analyst_a.id)
        share_id = client.get("/shared", headers=world.headers("analyst_a")).json()["items"][0][
            "id"
        ]
        membership = db.scalars(
            select(Membership).where(Membership.user_id == world.analyst_a.id)
        ).one()
        membership.connection_access = "selected"
        db.commit()
        assert client.get("/shared", headers=world.headers("analyst_a")).json()["items"] == []
        assert (
            client.get(f"/shared/{share_id}", headers=world.headers("analyst_a")).status_code == 404
        )

    def test_stop_sharing(
        self, client: TestClient, world: World, answer: dict[str, str], db: DbSession
    ) -> None:
        shares = share(client, world, answer, world.analyst_a.id).json()["shares"]
        share_id = shares[0]["id"]
        # The recipient cannot take it back; another workspace cannot see it at all.
        assert (
            client.delete(f"/shares/{share_id}", headers=world.headers("analyst_a")).status_code
            == 404
        )
        assert (
            client.delete(f"/shares/{share_id}", headers=world.headers("owner_b")).status_code
            == 404
        )
        assert (
            client.delete(f"/shares/{share_id}", headers=world.headers("owner_a")).status_code
            == 204
        )
        assert db.scalars(select(Share)).all() == []
        assert db.scalars(select(AuditLog).where(AuditLog.action == "share.removed")).one()

    def test_only_my_own_items_and_my_workspace_members(
        self, client: TestClient, world: World, answer: dict[str, str]
    ) -> None:
        # Someone else cannot share owner_a's answer.
        assert share(client, world, answer, world.viewer_a.id, who="analyst_a").status_code == 404
        # A person of another workspace is not a valid recipient.
        assert share(client, world, answer, world.analyst_b.id).status_code == 404
        # Viewers cannot share at all.
        assert share(client, world, answer, world.analyst_a.id, who="viewer_a").status_code == 403

    @pytest.mark.parametrize(
        "body",
        [
            {
                "object_type": "chart",
                "object_id": "0190c0de-0000-7000-8000-000000000000",
                "user_ids": [],
            },
            {"object_type": "message", "object_id": "nope", "user_ids": ["x"]},
            {
                "object_type": "message",
                "object_id": "0190c0de-0000-7000-8000-000000000000",
                "user_ids": [],
            },
        ],
    )
    def test_validation(self, client: TestClient, world: World, body: dict[str, Any]) -> None:
        assert (
            client.post("/shares", json=body, headers=world.headers("owner_a")).status_code == 422
        )

    def test_signed_out(self, client: TestClient) -> None:
        assert client.get("/shared").status_code == 401
        assert client.post("/shares", json={}).status_code == 401


class TestPersonalRoutes:
    """The M10 routes listed under PERSONAL in test_route_matrix.py: 401, 403 by role, 404, 422."""

    ROUTES: ClassVar[list[tuple[str, str]]] = [
        ("GET", "/reports/{id}"),
        ("GET", "/reports/{id}/export?format=pdf"),
        ("GET", "/conversations/{id}/messages/{id}/export?format=pdf"),
        ("GET", "/shared/{id}"),
        ("GET", "/shared/{id}/export?format=pdf"),
        ("DELETE", "/shares/{id}"),
        ("GET", "/shares?object_type=message&object_id={id}"),
        ("POST", "/shares"),
    ]
    MISSING = "0190c0de-0000-7000-8000-000000000000"

    def _call(
        self, client: TestClient, method: str, path: str, ident: str, headers: dict[str, str] | None
    ) -> Any:
        body = (
            {"object_type": "message", "object_id": ident, "user_ids": [self.MISSING]}
            if method == "POST"
            else None
        )
        return client.request(method, path.replace("{id}", ident), headers=headers or {}, json=body)

    @pytest.mark.parametrize(("method", "path"), ROUTES)
    def test_signed_out_is_401(self, client: TestClient, method: str, path: str) -> None:
        assert self._call(client, method, path, self.MISSING, None).status_code == 401

    @pytest.mark.parametrize(("method", "path"), ROUTES)
    def test_missing_is_404_and_malformed_is_422(
        self, client: TestClient, world: World, method: str, path: str
    ) -> None:
        headers = world.headers("owner_a")
        assert self._call(client, method, path, self.MISSING, headers).status_code == 404
        assert self._call(client, method, path, "not-a-uuid", headers).status_code == 422

    def test_viewers_cannot_share(self, client: TestClient, world: World) -> None:
        for method, path in (
            ("GET", "/shares?object_type=message&object_id={id}"),
            ("POST", "/shares"),
            ("DELETE", "/shares/{id}"),
        ):
            assert (
                self._call(
                    client, method, path, self.MISSING, world.headers("viewer_a")
                ).status_code
                == 403
            )

    def test_another_workspaces_items_are_404(
        self, client: TestClient, world: World, answer: dict[str, str]
    ) -> None:
        share(client, world, answer, world.analyst_a.id)
        share_id = client.get("/shared", headers=world.headers("analyst_a")).json()["items"][0][
            "id"
        ]
        for who in ("owner_b", "analyst_b"):
            headers = world.headers(who)
            assert client.get(f"/shared/{share_id}", headers=headers).status_code == 404
            assert (
                client.get(f"/shared/{share_id}/export?format=xlsx", headers=headers).status_code
                == 404
            )
            assert client.delete(f"/shares/{share_id}", headers=headers).status_code == 404
            assert client.get(export_url(answer), headers=headers).status_code == 404
