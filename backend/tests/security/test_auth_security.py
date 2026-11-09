"""Attacks on the auth layer must fail (BUILD_SPEC 6.1, 15: security tests for every new surface)."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import CSRF_COOKIE, REFRESH_COOKIE
from app.auth.tokens import AUDIENCE, ISSUER, issue_access_token
from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, RefreshToken, Session, UserToken
from tests.authflow import (
    PASSWORD,
    bearer,
    link_token,
    login,
    refresh,
    refresh_cookie,
    registered_user,
    signup,
)
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"


class TestRefreshTokenReuse:
    """A stolen refresh token used after the real browser rotated it must end the whole session."""

    def test_replaying_a_rotated_token_revokes_the_session(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        stolen = refresh_cookie(client)
        csrf = client.cookies.get(CSRF_COOKIE, "")

        # The real user refreshes: token rotates.
        legit = refresh(client)
        assert legit.status_code == 200
        legit_access = legit.json()["access_token"]
        current = refresh_cookie(client)
        assert current != stolen

        # The attacker replays the stolen (already rotated) token from their own browser.
        attacker = TestClient(client.app, base_url="http://localhost:8000")
        attacker.cookies.set(REFRESH_COOKIE, stolen, path="/auth")
        attacker.cookies.set(CSRF_COOKIE, csrf)
        replay = attacker.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "session_revoked"

        # Everything from that session is dead now: the user's newest refresh token and access token too.
        assert refresh(client).status_code == 401
        assert client.get("/auth/me", headers=bearer(legit_access)).status_code == 401

        session = db.scalars(select(Session)).one()
        assert session.revoke_reason == "refresh_reuse"
        assert all(t.revoked_at is not None for t in db.scalars(select(RefreshToken)))
        event = db.scalars(
            select(AuditLog).where(AuditLog.action == "auth.refresh_token_reused")
        ).one()
        assert event.details == {"session_revoked": True}
        assert event.target_id == str(session.id)

    def test_reuse_only_affects_that_session(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        other_browser = TestClient(client.app, base_url="http://localhost:8000")
        other = login(other_browser)
        stolen = refresh_cookie(client)
        refresh(client)
        attacker = TestClient(client.app, base_url="http://localhost:8000")
        csrf = attacker.get("/auth/csrf").json()["csrf_token"]
        attacker.cookies.set(REFRESH_COOKIE, stolen, path="/auth")
        assert attacker.post("/auth/refresh", headers={"X-CSRF-Token": csrf}).status_code == 401
        assert (
            other_browser.get("/auth/me", headers=bearer(other["access_token"])).status_code == 200
        )

    def test_a_made_up_refresh_token_is_rejected(self, client: TestClient) -> None:
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        client.cookies.set(
            REFRESH_COOKIE, "made-up-token-value-0000000000000000000000", path="/auth"
        )
        response = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 401


class TestAccessTokenForgery:
    @pytest.fixture
    def signed_in(self, client: TestClient, outbox: MemoryEmailSender) -> dict[str, str]:
        body = registered_user(client, outbox)
        return {"token": body["access_token"], "user": body["user"]["id"]}

    def _claims(self, token: str) -> dict[str, object]:
        claims: dict[str, object] = jwt.decode(token, options={"verify_signature": False})
        return claims

    def test_wrong_secret(self, client: TestClient, signed_in: dict[str, str]) -> None:
        forged = jwt.encode(self._claims(signed_in["token"]), "x" * 40, algorithm="HS256")
        assert client.get("/auth/me", headers=bearer(forged)).status_code == 401

    def test_alg_none(self, client: TestClient, signed_in: dict[str, str]) -> None:
        forged = jwt.encode(self._claims(signed_in["token"]), key="", algorithm="none")
        assert client.get("/auth/me", headers=bearer(forged)).status_code == 401

    def test_tampered_role(
        self, client: TestClient, signed_in: dict[str, str], settings: Settings
    ) -> None:
        header, _payload, signature = signed_in["token"].split(".")
        claims = self._claims(signed_in["token"]) | {"role": "superadmin"}
        other_payload = jwt.encode(claims, "irrelevant-key-" * 4, algorithm="HS256").split(".")[1]
        tampered = ".".join([header, other_payload, signature])
        assert client.get("/auth/me", headers=bearer(tampered)).status_code == 401

    @pytest.mark.parametrize(
        "change",
        [{"aud": "someone-else"}, {"iss": "someone-else"}, {"typ": "refresh"}],
    )
    def test_wrong_audience_issuer_or_type(
        self,
        client: TestClient,
        signed_in: dict[str, str],
        settings: Settings,
        change: dict[str, str],
    ) -> None:
        claims = self._claims(signed_in["token"]) | change
        forged = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")
        assert client.get("/auth/me", headers=bearer(forged)).status_code == 401

    def test_validly_signed_token_for_an_unknown_session(
        self, client: TestClient, signed_in: dict[str, str], settings: Settings, clock: FakeClock
    ) -> None:
        claims = self._claims(signed_in["token"])
        token, _ = issue_access_token(
            secret=settings.jwt_secret.get_secret_value(),
            user_id=uuid.UUID(str(claims["sub"])),
            org_id=uuid.UUID(str(claims["org"])),
            role="owner",
            session_id=uuid.uuid4(),
            now=clock.now,
            lifetime=timedelta(minutes=10),
        )
        assert client.get("/auth/me", headers=bearer(token)).status_code == 401

    def test_session_of_another_user(
        self,
        client: TestClient,
        signed_in: dict[str, str],
        settings: Settings,
        clock: FakeClock,
        outbox: MemoryEmailSender,
    ) -> None:
        """A token naming user A with user B's session id is rejected."""
        other = TestClient(client.app, base_url="http://localhost:8000")
        bob = registered_user(other, outbox, "bob@shop.in", organization_name="Bob's shop")
        bob_claims = self._claims(bob["access_token"])
        token, _ = issue_access_token(
            secret=settings.jwt_secret.get_secret_value(),
            user_id=uuid.UUID(signed_in["user"]),
            org_id=uuid.UUID(str(bob_claims["org"])),
            role="owner",
            session_id=uuid.UUID(str(bob_claims["sid"])),
            now=clock.now,
            lifetime=timedelta(minutes=10),
        )
        assert client.get("/auth/me", headers=bearer(token)).status_code == 401

    def test_issuer_and_audience_are_set(self, signed_in: dict[str, str]) -> None:
        claims = self._claims(signed_in["token"])
        assert (claims["iss"], claims["aud"]) == (ISSUER, AUDIENCE)
        assert set(claims) >= {"sub", "org", "role", "sid", "exp", "iat"}

    def test_refresh_token_is_not_a_bearer_token(
        self, client: TestClient, signed_in: dict[str, str]
    ) -> None:
        assert client.get("/auth/me", headers=bearer(refresh_cookie(client))).status_code == 401


class TestStoredSecrets:
    def test_no_raw_tokens_or_passwords_in_the_database(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        verify_token = link_token(outbox, EMAIL, "Confirm your email address")
        client.post("/auth/verify-email", json={"token": verify_token})
        body = login(client)
        client.post("/auth/forgot-password", json={"email": EMAIL})
        reset_token = link_token(outbox, EMAIL, "Reset your password")
        secrets = [
            PASSWORD,
            verify_token,
            reset_token,
            refresh_cookie(client),
            body["access_token"],
        ]
        dump = "\n".join(
            str(row)
            for table in (
                "users",
                "sessions",
                "refresh_tokens",
                "user_tokens",
                "audit_log",
                "consents",
            )
            for row in db.execute(text(f"SELECT * FROM {table}"))  # noqa: S608 (fixed table names)
        )
        for secret in secrets:
            assert secret not in dump
        assert db.scalar(select(func.count()).select_from(UserToken)) == 2


class TestAuditLogIsAppendOnly:
    def _one_entry(self, client: TestClient, db: DbSession) -> None:
        signup(client)
        assert (db.scalar(select(func.count()).select_from(AuditLog)) or 0) > 0

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE audit_log SET action = 'tampered'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ],
    )
    def test_changes_are_rejected(self, client: TestClient, db: DbSession, statement: str) -> None:
        self._one_entry(client, db)
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()
        assert "tampered" not in [a.action for a in db.scalars(select(AuditLog))]


class TestNoLeaksInLogs:
    def test_passwords_and_tokens_never_reach_the_log(
        self,
        client: TestClient,
        outbox: MemoryEmailSender,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        body = registered_user(client, outbox)
        refresh(client)
        client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password-here"})
        logged = "\n".join(r.getMessage() for r in caplog.records)
        for secret in (
            PASSWORD,
            "wrong-password-here",
            body["access_token"],
            refresh_cookie(client),
        ):
            assert secret not in logged


class TestCookiesAndOrigins:
    def test_cookies_are_secure_when_the_api_uses_https(
        self, settings: Settings, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        app: FastAPI = client.app  # type: ignore[assignment]
        app.state.settings = settings.model_copy(update={"api_base_url": "https://api.example.in"})
        client.cookies.clear()
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        cookies = response.headers.get_list("set-cookie")
        assert all("secure" in c.lower() for c in cookies)

    def test_credentialed_requests_from_other_sites_are_not_allowed(
        self, client: TestClient
    ) -> None:
        response = client.options(
            "/auth/refresh",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in response.headers
