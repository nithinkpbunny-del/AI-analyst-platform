"""Security checks for M3: second-factor secrets at rest, fail-closed decryption, no codes in logs, and no way
around the Owner 2FA requirement (BUILD_SPEC 6.1, 6.4)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from app.auth import codes
from app.auth.deps import REFRESH_COOKIE
from app.email.sender import MemoryEmailSender
from app.models import Membership, Organization, TwoFactor, User
from tests.authflow import (
    PASSWORD,
    VERIFICATION_CODE,
    bearer,
    emailed_code,
    login_start,
    refresh,
    signup,
    verify,
)
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"


def dump(db: DbSession, *tables: str) -> str:
    return "\n".join(
        str(row)
        for table in tables
        for row in db.execute(text(f"SELECT * FROM {table}"))  # noqa: S608 (fixed table names)
    )


def owner_with_totp(
    client: TestClient, outbox: MemoryEmailSender, clock: FakeClock, email: str = EMAIL
) -> tuple[str, list[str], str]:
    """Returns (TOTP secret, recovery codes, challenge token used)."""
    signup(client, email)
    verify(client, outbox, email)
    challenge = login_start(client, email)
    token = challenge["challenge_token"]
    secret = client.post(
        "/auth/login/setup", json={"challenge_token": token, "method": "totp"}
    ).json()["secret"]
    done = client.post(
        "/auth/login/setup/confirm",
        json={"challenge_token": token, "code": codes.totp_code_at(secret, clock.now)},
    )
    assert done.status_code == 200
    return secret, done.json()["recovery_codes"], token


class TestSecretsAtRest:
    def test_totp_secret_recovery_codes_and_tokens_are_never_stored_in_clear(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        secret, recovery, token = owner_with_totp(client, outbox, clock)
        stored = dump(
            db, "two_factor", "login_challenges", "audit_log", "known_devices", "sessions"
        )
        assert secret not in stored
        assert secret.encode() not in b"".join(
            row.totp_secret_enc or b"" for row in db.scalars(select(TwoFactor))
        )
        for code in recovery:
            assert code not in stored and code.replace("-", "") not in stored
        assert token not in stored

    def test_email_codes_are_never_stored_in_clear(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        verify(client, outbox, EMAIL)
        challenge = login_start(client)
        client.post(
            "/auth/login/setup",
            json={"challenge_token": challenge["challenge_token"], "method": "email"},
        )
        code = emailed_code(outbox, EMAIL, VERIFICATION_CODE)
        assert code not in dump(db, "two_factor_codes", "two_factor", "audit_log")

    def test_a_secret_copied_from_another_person_does_not_work(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        their_secret, _, _ = owner_with_totp(client, outbox, clock, "kavya@freshbasket.in")
        client.cookies.clear()
        owner_with_totp(client, outbox, clock, EMAIL)
        theirs = db.scalars(
            select(TwoFactor)
            .join(User, User.id == TwoFactor.user_id)
            .where(User.email == "kavya@freshbasket.in")
        ).one()
        mine = db.scalars(
            select(TwoFactor).join(User, User.id == TwoFactor.user_id).where(User.email == EMAIL)
        ).one()
        mine.totp_secret_enc = theirs.totp_secret_enc  # an attacker with database write access
        mine.totp_last_step = None
        db.commit()
        clock.advance(seconds=30)
        challenge = login_start(client, EMAIL)
        response = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": codes.totp_code_at(their_secret, clock.now),
            },
        )
        assert response.status_code == 400  # fails closed, not 500 and not accepted


class TestNoWayAroundOwner2fa:
    def test_password_alone_never_gives_an_owner_a_session(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        verify(client, outbox, EMAIL)
        challenge = login_start(client)
        assert challenge["status"] == "two_factor_setup_required"
        assert not client.cookies.get(REFRESH_COOKIE)
        client.get("/auth/csrf")
        attempt = refresh(client)
        assert attempt.status_code == 401 and attempt.json()["error"]["code"] == "not_signed_in"
        # The challenge cannot be finished without a code, nor turned into a session another way.
        for path in ("/auth/login/verify", "/auth/login/setup/confirm"):
            response = client.post(
                path, json={"challenge_token": challenge["challenge_token"], "code": ""}
            )
            assert response.status_code in (400, 401, 422)
        assert (
            client.get("/auth/me", headers=bearer(challenge["challenge_token"])).status_code == 401
        )

    def test_owner_of_any_workspace_must_use_2fa(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        """Owner in one workspace, Analyst in another: signing in to either still needs 2FA."""
        signup(client)
        verify(client, outbox, EMAIL)
        user = db.scalars(select(User)).one()
        other = Organization(name="FreshBasket", monthly_question_limit=500)
        db.add(other)
        db.flush()
        db.add(Membership(org_id=other.id, user_id=user.id, role="analyst", status="active"))
        db.commit()
        response = client.post(
            "/auth/login",
            json={"email": EMAIL, "password": PASSWORD, "organization_id": str(other.id)},
        )
        assert response.json()["status"] == "two_factor_setup_required"

    def test_disabled_account_cannot_finish_a_started_sign_in(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        secret, _, _ = owner_with_totp(client, outbox, clock)
        clock.advance(seconds=30)
        challenge = login_start(client)
        user = db.scalars(select(User)).one()
        user.is_active = False
        db.commit()
        response = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": codes.totp_code_at(secret, clock.now),
            },
        )
        assert response.status_code == 401


class TestNoLeaksInLogs:
    def test_codes_and_secrets_never_reach_the_log(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        secret, recovery, token = owner_with_totp(client, outbox, clock)
        clock.advance(seconds=30)
        challenge = login_start(client)
        client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": recovery[0]},
        )
        logged = "\n".join(r.getMessage() for r in caplog.records)
        for value in (secret, token, challenge["challenge_token"], *recovery):
            assert value not in logged
