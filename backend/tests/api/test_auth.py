"""``/auth`` routes: sign-up, email verification, login, refresh, logout, timeouts, rate limits, password
reset, consent and activity-log entries (BUILD_SPEC 6.1, R15, M2 acceptance criteria).

Route-test rule (BUILD_SPEC 0.6): these routes are public except ``/auth/me``, refresh and logout, so they get
success, validation-error and 401 cases. There are no role-restricted or tenant-owned resources here, so 403
(wrong role) and 404 (cross-tenant) start with the first such routes in M4.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import CSRF_COOKIE, REFRESH_COOKIE
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Consent, Membership, Organization, Session, User
from tests.authflow import (
    PASSWORD,
    bearer,
    emailed_code,
    link_token,
    login,
    login_start,
    refresh,
    registered_user,
    signup,
    verify,
)
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"


def actions(db: DbSession) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.at, AuditLog.id)))


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# ----------------------------------------------------------------------------------------------- sign-up
class TestSignup:
    def test_creates_org_owner_consent_and_sends_confirmation(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = signup(client)
        assert body == {
            "status": "accepted",
            "message": "Check your email for a link to confirm your address.",
        }
        user = db.scalars(select(User)).one()
        assert user.email == EMAIL
        assert user.email_verified_at is None
        assert user.password_hash and user.password_hash.startswith("$argon2id$")
        org = db.scalars(select(Organization)).one()
        assert (org.name, org.timezone, org.currency, org.plan) == (
            "UrbanCart",
            "Asia/Kolkata",
            "INR",
            "managed",
        )
        membership = db.scalars(select(Membership)).one()
        assert (membership.org_id, membership.user_id, membership.role, membership.status) == (
            org.id,
            user.id,
            "owner",
            "active",
        )
        consent = db.scalars(select(Consent)).one()
        assert (consent.user_id, consent.org_id, consent.policy_version) == (
            user.id,
            org.id,
            "2026-11-09",
        )
        assert sorted(actions(db)) == ["auth.signup", "consent.accepted"]
        [email] = outbox.to(EMAIL)
        assert email.subject == "Confirm your email address"
        assert "http://localhost:5173/verify-email#token=" in email.text
        assert "UrbanCart" in email.html

    def test_existing_email_gets_the_same_answer_and_a_heads_up_email(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        second = signup(client, "ASHA@urbancart.in", organization_name="Another shop")
        assert second["status"] == "accepted"
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert [m.subject for m in outbox.to(EMAIL)] == [
            "Confirm your email address",
            "You already have an account",
        ]

    @pytest.mark.parametrize(
        ("password", "reason"),
        [
            ("short1", "at least 10"),
            ("password123", "too common"),
            ("asha@urbancart.in", "email address"),
            ("aaaaaaaaaaaa", "too common"),
            ("abababababab", "repetitive"),
        ],
    )
    def test_rejects_weak_passwords(
        self, client: TestClient, db: DbSession, password: str, reason: str
    ) -> None:
        response = client.post(
            "/auth/signup",
            json={
                "name": "Asha",
                "email": EMAIL,
                "password": password,
                "organization_name": "UrbanCart",
                "accept_terms": True,
            },
        )
        assert response.status_code == 422
        assert error_code(response) == "weak_password"
        assert reason in response.json()["error"]["fields"]["password"]
        assert db.scalar(select(func.count()).select_from(User)) == 0

    def test_validation_errors_name_the_fields(self, client: TestClient) -> None:
        response = client.post(
            "/auth/signup",
            json={
                "name": " ",
                "email": "not-an-email",
                "password": PASSWORD,
                "accept_terms": False,
            },
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert set(error["fields"]) == {"name", "email", "organization_name", "accept_terms"}
        assert error["fields"]["accept_terms"].startswith("Accept the privacy policy")

    def test_unknown_fields_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/auth/signup",
            json={
                "name": "Asha",
                "email": EMAIL,
                "password": PASSWORD,
                "organization_name": "UrbanCart",
                "accept_terms": True,
                "role": "owner",
            },
        )
        assert response.status_code == 422
        assert "role" in response.json()["error"]["fields"]


# ------------------------------------------------------------------------------------ email verification
class TestEmailVerification:
    def test_login_is_refused_until_the_email_is_confirmed(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 403
        assert error_code(response) == "email_not_verified"
        verify(client, outbox, EMAIL)
        assert db.scalars(select(User)).one().email_verified_at is not None
        login(client)
        assert "auth.email_verified" in actions(db)

    def test_link_works_once(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        signup(client)
        token = link_token(outbox, EMAIL, "Confirm your email address")
        assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
        again = client.post("/auth/verify-email", json={"token": token})
        assert again.status_code == 400
        assert error_code(again) == "invalid_link"

    def test_link_expires(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        signup(client)
        token = link_token(outbox, EMAIL, "Confirm your email address")
        clock.advance(hours=24, seconds=1)
        response = client.post("/auth/verify-email", json={"token": token})
        assert response.status_code == 400

    def test_unknown_token(self, client: TestClient) -> None:
        response = client.post("/auth/verify-email", json={"token": "x" * 43})
        assert response.status_code == 400
        assert client.post("/auth/verify-email", json={}).status_code == 422

    def test_resend_replaces_the_old_link(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        first = link_token(outbox, EMAIL, "Confirm your email address")
        response = client.post("/auth/resend-verification", json={"email": EMAIL})
        assert response.status_code == 202
        second = link_token(outbox, EMAIL, "Confirm your email address")
        assert first != second
        assert client.post("/auth/verify-email", json={"token": first}).status_code == 400
        assert client.post("/auth/verify-email", json={"token": second}).status_code == 200

    def test_resend_reveals_nothing_for_unknown_or_confirmed_addresses(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        sent_before = len(outbox.sent)
        for address in (EMAIL, "nobody@example.com"):
            response = client.post("/auth/resend-verification", json={"email": address})
            assert response.status_code == 202
            assert response.json()["message"].startswith("If this address has an account")
        assert len(outbox.sent) == sent_before


# --------------------------------------------------------------------------------------------- login
class TestLogin:
    def test_success_returns_tokens_cookies_and_profile(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 600
        assert body["role"] == "owner"
        assert body["user"]["email"] == EMAIL
        assert body["organization"]["name"] == "UrbanCart"
        assert body["memberships"][0]["role"] == "owner"
        assert body["idle_timeout_minutes"] == 30
        assert client.cookies.get(REFRESH_COOKIE)
        assert body["csrf_token"] == client.cookies.get(CSRF_COOKIE)
        session = db.scalars(select(Session)).one()
        assert session.ip == "testclient"
        assert session.user_agent
        success = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.success")).one()
        assert success.org_id == session.org_id
        # M3: every Owner signs in with a second step; the first device is never announced.
        assert success.details == {"method": "password", "two_factor": "email", "new_device": False}

    def test_refresh_cookie_flags(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        registered_user(client, outbox, "ravi@urbancart.in")
        client.cookies.clear()
        challenge = login_start(client, "ravi@urbancart.in")
        # No session cookie until the second step is done.
        assert not client.cookies.get(REFRESH_COOKIE)
        response = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": emailed_code(outbox, "ravi@urbancart.in"),
            },
        )
        cookies = response.headers.get_list("set-cookie")
        refresh_cookie = next(c for c in cookies if c.startswith(f"{REFRESH_COOKIE}="))
        lowered = refresh_cookie.lower()
        assert "httponly" in lowered
        assert "samesite=strict" in lowered
        assert "path=/auth" in lowered
        assert "max-age=43200" in lowered
        csrf_cookie = next(c for c in cookies if c.startswith(f"{CSRF_COOKIE}="))
        assert "httponly" not in csrf_cookie.lower()
        device_cookie = next(c for c in cookies if c.startswith("analyst_device=")).lower()
        assert "httponly" in device_cookie and "samesite=strict" in device_cookie
        assert "path=/auth" in device_cookie
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        ("email", "password"),
        [(EMAIL, "wrong-password-123"), ("nobody@example.com", PASSWORD)],
    )
    def test_wrong_password_and_unknown_email_look_the_same(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        email: str,
        password: str,
    ) -> None:
        registered_user(client, outbox)
        response = client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401
        assert response.json() == {
            "error": {"code": "invalid_credentials", "message": "Invalid email or password."}
        }
        failure = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.failure")).one()
        if email == EMAIL:
            assert failure.details == {"reason": "bad_password"}
            assert failure.org_id is not None
        else:
            assert failure.details["reason"] == "unknown_email"
            assert failure.org_id is None and failure.user_id is None
            assert "nobody" not in str(failure.details)  # only a hash of the address is kept

    def test_email_is_case_insensitive(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        registered_user(client, outbox)
        login(client, "Asha@UrbanCart.IN")

    def test_inactive_user_cannot_sign_in(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        user = db.scalars(select(User)).one()
        user.is_active = False
        db.commit()
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 401
        assert error_code(response) == "invalid_credentials"

    def test_user_without_any_workspace(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        db.scalars(select(Membership)).one().status = "removed"
        db.commit()
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 403
        assert error_code(response) == "no_organization"

    def test_validation_error(self, client: TestClient) -> None:
        response = client.post("/auth/login", json={"email": "asha", "password": ""})
        assert response.status_code == 422
        assert set(response.json()["error"]["fields"]) == {"email", "password"}


class TestLoginRateLimit:
    def test_five_failures_lock_the_email_and_ip(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        for _ in range(5):
            response = client.post(
                "/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"}
            )
            assert response.status_code == 401
        # Locked now, even with the right password.
        locked = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert locked.status_code == 429
        assert error_code(locked) == "too_many_attempts"
        assert locked.headers["retry-after"] == "900"
        assert "15 minutes" in locked.json()["error"]["message"]
        reasons = [
            a.details["reason"]
            for a in db.scalars(
                select(AuditLog)
                .where(AuditLog.action == "auth.login.failure")
                .order_by(AuditLog.at)
            )
        ]
        assert reasons[-1] == "rate_limited"
        clock.advance(minutes=15, seconds=1)
        login(client)

    def test_repeated_lockouts_back_off_exponentially(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)

        def fail_five() -> None:
            for _ in range(5):
                client.post("/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"})

        fail_five()
        clock.advance(minutes=16)
        fail_five()
        second = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert second.headers["retry-after"] == "1800"
        clock.advance(minutes=31)
        fail_five()
        third = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert third.headers["retry-after"] == "3600"

    def test_failures_outside_the_window_do_not_count(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        for _ in range(4):
            client.post("/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"})
        clock.advance(minutes=16)
        for _ in range(4):
            client.post("/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"})
        login(client)

    def test_success_clears_the_failure_count(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        for _ in range(4):
            client.post("/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"})
        login(client)
        for _ in range(4):
            client.post("/auth/login", json={"email": EMAIL, "password": "nope-nope-nope"})
        login(client)

    def test_signup_emails_are_limited_per_ip(self, client: TestClient) -> None:
        statuses = []
        for i in range(31):
            response = client.post("/auth/resend-verification", json={"email": f"p{i}@example.com"})
            statuses.append(response.status_code)
        # Unknown addresses send nothing and do not count; the IP limit counts sending requests only.
        assert set(statuses) == {202}
        for i in range(31):
            response = client.post(
                "/auth/signup",
                json={
                    "name": "Spam",
                    "email": f"s{i}@example.com",
                    "password": PASSWORD,
                    "organization_name": "Spam",
                    "accept_terms": True,
                },
            )
        assert response.status_code == 429
        assert error_code(response) == "too_many_requests"

    def test_emails_to_one_address_are_capped_silently(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        for _ in range(10):
            assert (
                client.post("/auth/resend-verification", json={"email": EMAIL}).status_code == 202
            )
        assert len(outbox.to(EMAIL)) == 5


# ------------------------------------------------------------------------------------------- /auth/me
class TestMe:
    def test_returns_the_signed_in_user(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        response = client.get("/auth/me", headers=bearer(body["access_token"]))
        assert response.status_code == 200
        me = response.json()
        assert me["user"]["email"] == EMAIL
        assert me["role"] == "owner"
        assert "access_token" not in me

    @pytest.mark.parametrize(
        "header",
        [None, "Bearer", "Basic abc", "Bearer not.a.jwt", "Token abc"],
    )
    def test_unauthenticated(self, client: TestClient, header: str | None) -> None:
        headers = {"Authorization": header} if header else {}
        response = client.get("/auth/me", headers=headers)
        assert response.status_code == 401
        assert error_code(response) in {"not_signed_in", "invalid_token"}

    def test_expired_access_token_asks_for_refresh(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        body = registered_user(client, outbox)
        clock.advance(minutes=10)
        response = client.get("/auth/me", headers=bearer(body["access_token"]))
        assert response.status_code == 401
        assert error_code(response) == "token_expired"

    def test_role_comes_from_the_database(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        db.scalars(select(Membership)).one().role = "viewer"
        db.commit()
        me = client.get("/auth/me", headers=bearer(body["access_token"])).json()
        assert me["role"] == "viewer"

    def test_removed_member_loses_access_immediately(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        db.scalars(select(Membership)).one().status = "removed"
        db.commit()
        response = client.get("/auth/me", headers=bearer(body["access_token"]))
        assert response.status_code == 401
        db.expire_all()
        assert db.scalars(select(Session)).one().revoke_reason == "access_removed"
        assert refresh(client).status_code == 401


# ------------------------------------------------------------------------------- refresh and timeouts
class TestRefresh:
    def test_rotates_the_refresh_token(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        body = registered_user(client, outbox)
        old_cookie = client.cookies.get(REFRESH_COOKIE)
        clock.advance(minutes=5)
        response = refresh(client)
        assert response.status_code == 200, response.text
        new_body = response.json()
        assert new_body["access_token"] != body["access_token"]
        assert client.cookies.get(REFRESH_COOKIE) != old_cookie
        assert client.get("/auth/me", headers=bearer(new_body["access_token"])).status_code == 200

    def test_needs_the_csrf_header(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        registered_user(client, outbox)
        for header in ("", "wrong-value"):
            response = refresh(client, csrf=header)
            assert response.status_code == 403
            assert error_code(response) == "csrf_failed"
        assert refresh(client).status_code == 200

    def test_without_a_cookie(self, client: TestClient) -> None:
        client.get("/auth/csrf")
        response = refresh(client)
        assert response.status_code == 401
        assert error_code(response) == "not_signed_in"

    def test_idle_timeout(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        clock.advance(minutes=30)
        response = refresh(client)
        assert response.status_code == 401
        assert error_code(response) == "session_idle"
        assert "30 minutes" in response.json()["error"]["message"]
        assert db.scalars(select(Session)).one().revoke_reason == "idle"
        assert "auth.session.idle" in actions(db)

    def test_activity_keeps_the_session_alive(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        body = registered_user(client, outbox)
        token = body["access_token"]
        for _ in range(4):  # 4 x 9 minutes of activity = 36 minutes, beyond the idle limit
            clock.advance(minutes=9)
            assert client.get("/auth/me", headers=bearer(token)).status_code == 200
            token = refresh(client).json()["access_token"]

    def test_refreshing_alone_does_not_count_as_activity(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        """An open tab that only refreshes its token must still be signed out after 30 idle minutes."""
        registered_user(client, outbox)
        for _ in range(3):
            clock.advance(minutes=9)
            assert refresh(client).status_code == 200
        clock.advance(minutes=4)
        assert error_code(refresh(client)) == "session_idle"

    def test_absolute_timeout_after_12_hours(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        body = registered_user(client, outbox)
        token = body["access_token"]
        started = clock.now
        response = None
        while clock.now - started < timedelta(hours=13):
            clock.advance(minutes=9)
            response = client.get("/auth/me", headers=bearer(token))
            if response.status_code != 200:
                break
            token = refresh(client).json()["access_token"]
        assert response is not None and response.status_code == 401
        assert error_code(response) == "session_expired"
        assert clock.now - started >= timedelta(hours=12)
        assert clock.now - started < timedelta(hours=12, minutes=9)
        assert error_code(refresh(client)) == "session_revoked"
        assert db.scalars(select(Session)).one().revoke_reason == "expired"

    def test_failed_refresh_clears_the_cookies(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        clock.advance(minutes=31)
        response = refresh(client)
        cleared = response.headers.get_list("set-cookie")
        assert any(c.startswith(f'{REFRESH_COOKIE}=""') for c in cleared)
        assert client.cookies.get(REFRESH_COOKIE) is None


# ------------------------------------------------------------------------------------------- logout
class TestLogout:
    def test_revokes_the_session_and_clears_cookies(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        csrf = client.cookies.get(CSRF_COOKIE, "")
        response = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
        assert response.status_code == 204
        assert client.cookies.get(REFRESH_COOKIE) is None
        assert db.scalars(select(Session)).one().revoke_reason == "logout"
        assert client.get("/auth/me", headers=bearer(body["access_token"])).status_code == 401
        assert "auth.logout" in actions(db)

    def test_needs_csrf(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        registered_user(client, outbox)
        assert client.post("/auth/logout").status_code == 403

    def test_without_a_session_is_harmless(self, client: TestClient) -> None:
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204


# ------------------------------------------------------------------------------------- password reset
class TestPasswordReset:
    def test_full_flow_revokes_every_session(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        response = client.post("/auth/forgot-password", json={"email": EMAIL})
        assert response.status_code == 202
        token = link_token(outbox, EMAIL, "Reset your password")
        new_password = "a-brand-new-ledger-77"
        done = client.post("/auth/reset-password", json={"token": token, "password": new_password})
        assert done.status_code == 200, done.text
        assert client.get("/auth/me", headers=bearer(body["access_token"])).status_code == 401
        assert refresh(client).status_code == 401
        assert (
            client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code
            == 401
        )
        assert "Your password was changed" in [m.subject for m in outbox.to(EMAIL)]
        login(client, EMAIL, new_password)
        assert {"auth.password_reset.requested", "auth.password_reset.completed"} <= set(
            actions(db)
        )

    def test_unknown_email_gets_the_same_answer(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        response = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
        assert response.status_code == 202
        assert response.json()["message"].startswith("If this address has an account")
        assert outbox.sent == []

    def test_link_is_single_use_and_expires(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        client.post("/auth/forgot-password", json={"email": EMAIL})
        token = link_token(outbox, EMAIL, "Reset your password")
        ok = client.post(
            "/auth/reset-password", json={"token": token, "password": "new-ledger-pass-1"}
        )
        assert ok.status_code == 200
        again = client.post(
            "/auth/reset-password", json={"token": token, "password": "new-ledger-pass-2"}
        )
        assert again.status_code == 400
        client.post("/auth/forgot-password", json={"email": EMAIL})
        late = link_token(outbox, EMAIL, "Reset your password")
        clock.advance(minutes=61)
        expired = client.post(
            "/auth/reset-password", json={"token": late, "password": "new-ledger-pass-3"}
        )
        assert expired.status_code == 400
        assert error_code(expired) == "invalid_link"

    def test_a_new_link_replaces_the_old_one(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        client.post("/auth/forgot-password", json={"email": EMAIL})
        first = link_token(outbox, EMAIL, "Reset your password")
        client.post("/auth/forgot-password", json={"email": EMAIL})
        response = client.post(
            "/auth/reset-password", json={"token": first, "password": "new-ledger-pass-1"}
        )
        assert response.status_code == 400

    def test_weak_new_password_keeps_the_link_usable(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        client.post("/auth/forgot-password", json={"email": EMAIL})
        token = link_token(outbox, EMAIL, "Reset your password")
        weak = client.post("/auth/reset-password", json={"token": token, "password": "password1"})
        assert weak.status_code == 422
        assert error_code(weak) == "weak_password"
        good = client.post(
            "/auth/reset-password", json={"token": token, "password": "new-ledger-pass-1"}
        )
        assert good.status_code == 200

    def test_reset_confirms_an_unverified_email(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        signup(client)
        client.post("/auth/forgot-password", json={"email": EMAIL})
        token = link_token(outbox, EMAIL, "Reset your password")
        client.post("/auth/reset-password", json={"token": token, "password": "new-ledger-pass-1"})
        assert db.scalars(select(User)).one().email_verified_at is not None
        login(client, EMAIL, "new-ledger-pass-1")

    def test_validation(self, client: TestClient) -> None:
        assert client.post("/auth/forgot-password", json={"email": "x"}).status_code == 422
        assert client.post("/auth/reset-password", json={"token": "short"}).status_code == 422


def test_csrf_endpoint_keeps_an_existing_value(client: TestClient) -> None:
    first = client.get("/auth/csrf")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    second = client.get("/auth/csrf")
    assert first.json()["csrf_token"] == second.json()["csrf_token"]


def test_unknown_route_uses_the_error_shape(client: TestClient) -> None:
    response = client.get("/auth/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}
