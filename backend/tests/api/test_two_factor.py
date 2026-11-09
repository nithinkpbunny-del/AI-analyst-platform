"""Two-step verification (BUILD_SPEC 6.1, R15, M3): email codes, authenticator apps (TOTP) and recovery codes,
mandatory for Owners; managing it while signed in.

Route-test rule (BUILD_SPEC 0.6): sign-in challenge routes are public (the challenge token is the credential), so
they get success, validation and 401 (expired/unknown challenge) cases. The signed-in routes act only on the
caller's own account, so they get success, validation, 401 and 403 (Owners cannot turn 2FA off); there is no
other tenant's resource to reach, so 404-cross-tenant does not apply (sessions, which have ids, are tested for
404 in test_devices.py).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth import codes, passwords
from app.auth.deps import REFRESH_COOKIE
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, Membership, Organization, TwoFactor, User
from tests.authflow import (
    PASSWORD,
    SIGN_IN_CODE,
    VERIFICATION_CODE,
    bearer,
    emailed_code,
    login,
    login_start,
    registered_user,
    signup,
    verify,
)
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"
ANALYST = "ravi@urbancart.in"


def actions(db: DbSession) -> list[str]:
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.at, AuditLog.id)))


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def new_owner(client: TestClient, outbox: MemoryEmailSender, email: str = EMAIL) -> dict[str, Any]:
    """A confirmed Owner who has not signed in yet. Returns their first login answer (the set-up challenge)."""
    signup(client, email)
    verify(client, outbox, email)
    return login_start(client, email)


def set_up_totp(client: TestClient, clock: FakeClock, challenge: dict[str, Any]) -> tuple[str, Any]:
    """Set up an authenticator app during sign-in. Returns (secret, confirm response)."""
    token = challenge["challenge_token"]
    started = client.post("/auth/login/setup", json={"challenge_token": token, "method": "totp"})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    done = client.post(
        "/auth/login/setup/confirm",
        json={"challenge_token": token, "code": codes.totp_code_at(secret, clock.now)},
    )
    return secret, done


def add_member(db: DbSession, email: str = ANALYST, role: str = "analyst") -> User:
    """Someone an Owner invited (invitations arrive in M4): confirmed email, password, membership."""
    org = db.scalars(select(Organization)).first()
    assert org is not None
    user = User(
        email=email,
        name="Ravi Kumar",
        password_hash=passwords.hash_password(PASSWORD),
        email_verified_at=org.created_at,
    )
    db.add(user)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role=role, status="active"))
    db.commit()
    return user


# ------------------------------------------------------------------------------------- Owners must set it up
class TestOwnerSetupDuringSignIn:
    def test_owner_without_2fa_gets_no_session_until_set_up(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        challenge = new_owner(client, outbox)
        assert challenge["status"] == "two_factor_setup_required"
        assert challenge["email_hint"] == "as***@urbancart.in"
        assert challenge["expires_in"] == 15 * 60
        assert "access_token" not in challenge
        assert not client.cookies.get(REFRESH_COOKIE)
        # The challenge token is not an access token.
        me = client.get("/auth/me", headers=bearer(challenge["challenge_token"]))
        assert me.status_code == 401

    def test_authenticator_app_set_up(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        challenge = new_owner(client, outbox)
        token = challenge["challenge_token"]
        started = client.post(
            "/auth/login/setup", json={"challenge_token": token, "method": "totp"}
        )
        assert started.status_code == 200
        setup = started.json()
        assert setup["method"] == "totp"
        assert setup["otpauth_uri"].startswith("otpauth://totp/Analyst:")
        assert len(setup["qr"]) == len(setup["qr"][0])
        assert started.headers["cache-control"] == "no-store"

        wrong = client.post(
            "/auth/login/setup/confirm", json={"challenge_token": token, "code": "000000"}
        )
        if codes.totp_code_at(setup["secret"], clock.now) != "000000":
            assert wrong.status_code == 400 and error_code(wrong) == "invalid_code"
        done = client.post(
            "/auth/login/setup/confirm",
            json={"challenge_token": token, "code": codes.totp_code_at(setup["secret"], clock.now)},
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["status"] == "signed_in"
        assert len(body["recovery_codes"]) == 10
        assert body["two_factor"] == {
            "enabled": True,
            "method": "totp",
            "required": True,
            "recovery_codes_remaining": 10,
        }
        assert client.cookies.get(REFRESH_COOKIE)
        assert "auth.2fa.enabled" in actions(db)
        assert "Your two-step verification changed" in [m.subject for m in outbox.to(EMAIL)]
        # The challenge is used up.
        again = client.post(
            "/auth/login/setup/confirm", json={"challenge_token": token, "code": "123456"}
        )
        assert again.status_code == 401 and error_code(again) == "challenge_expired"

    def test_email_code_set_up(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        challenge = new_owner(client, outbox)
        token = challenge["challenge_token"]
        started = client.post(
            "/auth/login/setup", json={"challenge_token": token, "method": "email"}
        )
        assert started.json() == {
            "method": "email",
            "secret": None,
            "otpauth_uri": None,
            "qr": None,
            "email_hint": "as***@urbancart.in",
        }
        resent = client.post("/auth/login/setup/resend-code", json={"challenge_token": token})
        assert resent.status_code == 202
        code = emailed_code(outbox, EMAIL, VERIFICATION_CODE)
        done = client.post(
            "/auth/login/setup/confirm", json={"challenge_token": token, "code": code}
        )
        assert done.status_code == 200
        assert done.json()["two_factor"]["method"] == "email"

    def test_challenge_expires(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        challenge = new_owner(client, outbox)
        clock.advance(minutes=15)
        response = client.post(
            "/auth/login/setup",
            json={"challenge_token": challenge["challenge_token"], "method": "totp"},
        )
        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": "challenge_expired",
            "message": "This sign-in has expired. Sign in again.",
        }

    def test_validation_errors(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        challenge = new_owner(client, outbox)
        bad_method = client.post(
            "/auth/login/setup",
            json={"challenge_token": challenge["challenge_token"], "method": "sms"},
        )
        assert bad_method.status_code == 422
        assert "method" in bad_method.json()["error"]["fields"]
        missing = client.post("/auth/login/verify", json={"challenge_token": "x" * 20})
        assert missing.status_code == 422
        unknown = client.post(
            "/auth/login/verify", json={"challenge_token": "x" * 40, "code": "123456"}
        )
        assert unknown.status_code == 401 and error_code(unknown) == "challenge_expired"

    def test_wrong_kind_of_challenge_is_refused(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        challenge = new_owner(client, outbox)
        response = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": "123456"},
        )
        assert response.status_code == 401


# --------------------------------------------------------------------------------------- signing in with 2FA
class TestSignInWithAuthenticatorApp:
    @pytest.fixture
    def secret(self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock) -> str:
        secret, done = set_up_totp(client, clock, new_owner(client, outbox))
        assert done.status_code == 200
        clock.advance(seconds=30)  # the set-up code's time step is used up
        return secret

    def test_code_finishes_sign_in(
        self, client: TestClient, db: DbSession, clock: FakeClock, secret: str
    ) -> None:
        challenge = login_start(client)
        assert challenge["status"] == "two_factor_required"
        assert (challenge["method"], challenge["code_sent"]) == ("totp", False)
        response = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": codes.totp_code_at(secret, clock.now),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "signed_in"
        success = db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "auth.login.success")
            .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        ).first()
        assert success is not None
        assert success.details["two_factor"] == "totp"

    def test_a_code_works_only_once(
        self, client: TestClient, clock: FakeClock, secret: str
    ) -> None:
        code = codes.totp_code_at(secret, clock.now)
        first = login_start(client)
        ok = client.post(
            "/auth/login/verify", json={"challenge_token": first["challenge_token"], "code": code}
        )
        assert ok.status_code == 200
        second = login_start(client)
        replay = client.post(
            "/auth/login/verify", json={"challenge_token": second["challenge_token"], "code": code}
        )
        assert replay.status_code == 400 and error_code(replay) == "invalid_code"

    def test_five_wrong_codes_end_the_attempt(
        self, client: TestClient, db: DbSession, clock: FakeClock, secret: str
    ) -> None:
        challenge = login_start(client)
        right = codes.totp_code_at(secret, clock.now)
        wrong = "111111" if right != "111111" else "222222"
        statuses = [
            client.post(
                "/auth/login/verify",
                json={"challenge_token": challenge["challenge_token"], "code": wrong},
            ).status_code
            for _ in range(5)
        ]
        assert statuses == [400, 400, 400, 400, 401]
        late = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": right},
        )
        assert late.status_code == 401
        failures = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.failure")).all()
        assert [f.details["reason"] for f in failures] == ["bad_2fa_code"] * 5

    def test_too_many_wrong_codes_lock_code_entry(
        self, client: TestClient, db: DbSession, clock: FakeClock, secret: str
    ) -> None:
        right = codes.totp_code_at(secret, clock.now)
        wrong = "111111" if right != "111111" else "222222"
        for _ in range(2):
            challenge = login_start(client)
            for _ in range(5):
                client.post(
                    "/auth/login/verify",
                    json={"challenge_token": challenge["challenge_token"], "code": wrong},
                )
        challenge = login_start(client)
        locked = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": right},
        )
        assert locked.status_code == 429
        assert error_code(locked) == "too_many_attempts"
        assert int(locked.headers["retry-after"]) > 0
        reasons = [
            a.details["reason"]
            for a in db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.failure"))
        ]
        assert reasons[-1] == "two_factor_locked"
        clock.advance(minutes=16)
        challenge = login_start(client)
        ok = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": codes.totp_code_at(secret, clock.now),
            },
        )
        assert ok.status_code == 200

    def test_recovery_code_works_once(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        clock: FakeClock,
    ) -> None:
        secret, done = set_up_totp(client, clock, new_owner(client, outbox, "meera@urbancart.in"))
        recovery = done.json()["recovery_codes"][0]
        challenge = login_start(client, "meera@urbancart.in")
        used = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": recovery.upper()},
        )
        assert used.status_code == 200
        assert used.json()["two_factor"]["recovery_codes_remaining"] == 9
        assert "auth.2fa.recovery_code_used" in actions(db)
        notice = outbox.to("meera@urbancart.in")[-1]
        assert notice.subject == "Your two-step verification changed" and "9 left" in notice.text
        challenge = login_start(client, "meera@urbancart.in")
        again = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": recovery},
        )
        assert again.status_code == 400
        assert secret  # the app keeps working alongside the recovery codes


class TestSignInWithEmailCode:
    @pytest.fixture(autouse=True)
    def owner(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        registered_user(client, outbox)  # sets up the email method

    def test_code_is_emailed_and_finishes_sign_in(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        challenge = login_start(client)
        assert (challenge["method"], challenge["code_sent"]) == ("email", True)
        assert challenge["email_hint"] == "as***@urbancart.in"
        message = outbox.to(EMAIL)[-1]
        assert message.subject == SIGN_IN_CODE
        assert "10 minutes" in message.text
        code = emailed_code(outbox, EMAIL)
        assert code in message.html
        response = client.post(
            "/auth/login/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": f"{code[:3]} {code[3:]}",
            },
        )
        assert response.status_code == 200

    def test_resend_waits_30_seconds_and_replaces_the_code(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        challenge = login_start(client)
        token = challenge["challenge_token"]
        first = emailed_code(outbox, EMAIL)
        too_soon = client.post("/auth/login/resend-code", json={"challenge_token": token})
        assert (
            too_soon.status_code == 429 and "Wait 30 seconds" in too_soon.json()["error"]["message"]
        )
        clock.advance(seconds=31)
        resent = client.post("/auth/login/resend-code", json={"challenge_token": token})
        assert resent.status_code == 200 and resent.json()["code_sent"] is True
        second = emailed_code(outbox, EMAIL)
        if first != second:
            old = client.post("/auth/login/verify", json={"challenge_token": token, "code": first})
            assert old.status_code == 400
        ok = client.post("/auth/login/verify", json={"challenge_token": token, "code": second})
        assert ok.status_code == 200

    def test_code_expires_after_ten_minutes(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        challenge = login_start(client)
        code = emailed_code(outbox, EMAIL)
        clock.advance(minutes=10)
        response = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": code},
        )
        assert response.status_code == 400

    def test_authenticator_resend_is_refused(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        secret, _ = set_up_totp(client, clock, new_owner(client, outbox, "meera@urbancart.in"))
        challenge = login_start(client, "meera@urbancart.in")
        response = client.post(
            "/auth/login/resend-code", json={"challenge_token": challenge["challenge_token"]}
        )
        assert response.status_code == 400 and error_code(response) == "no_email_code"
        assert secret


# ------------------------------------------------------------------------------------- people who are not Owners
class TestOptionalForOthers:
    def test_analyst_signs_in_without_2fa_and_may_turn_it_on_and_off(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        add_member(db)
        body = login_start(client, ANALYST)
        assert body["status"] == "signed_in"
        assert body["two_factor"] == {
            "enabled": False,
            "method": None,
            "required": False,
            "recovery_codes_remaining": 0,
        }
        headers = bearer(body["access_token"])
        started = client.post("/auth/2fa/setup", json={"method": "totp"}, headers=headers)
        assert started.status_code == 200
        secret = started.json()["secret"]
        confirmed = client.post(
            "/auth/2fa/setup/confirm",
            json={"code": codes.totp_code_at(secret, clock.now)},
            headers=headers,
        )
        assert confirmed.status_code == 200 and len(confirmed.json()["recovery_codes"]) == 10
        me = client.get("/auth/me", headers=headers).json()
        assert me["two_factor"]["enabled"] is True

        no_code = client.post("/auth/2fa/disable", json={}, headers=headers)
        assert no_code.status_code == 400 and error_code(no_code) == "code_required"
        clock.advance(seconds=30)
        off = client.post(
            "/auth/2fa/disable",
            json={"code": codes.totp_code_at(secret, clock.now)},
            headers=headers,
        )
        assert off.status_code == 200
        assert db.get(TwoFactor, me["user"]["id"]) is None
        assert {"auth.2fa.enabled", "auth.2fa.disabled"} <= set(actions(db))
        assert login_start(client, ANALYST)["status"] == "signed_in"

    def test_owner_cannot_turn_it_off(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        body = registered_user(client, outbox)
        response = client.post(
            "/auth/2fa/disable", json={"code": "123456"}, headers=bearer(body["access_token"])
        )
        assert response.status_code == 403
        assert error_code(response) == "two_factor_required"


# ---------------------------------------------------------------------------------- signed-in changes (step-up)
class TestChangesWhileSignedIn:
    def test_changing_method_needs_a_current_code(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        body = registered_user(client, outbox)  # email method
        old_recovery = body["recovery_codes"]
        headers = bearer(body["access_token"])
        no_code = client.post("/auth/2fa/setup", json={"method": "totp"}, headers=headers)
        assert no_code.status_code == 400 and error_code(no_code) == "code_required"
        wrong = client.post(
            "/auth/2fa/setup", json={"method": "totp", "code": "000000"}, headers=headers
        )
        assert wrong.status_code == 400 and error_code(wrong) == "invalid_code"
        assert "auth.2fa.code_failed" in actions(db)

        sent = client.post("/auth/2fa/send-code", headers=headers)
        assert sent.status_code == 202
        step_up = emailed_code(outbox, EMAIL, VERIFICATION_CODE)
        started = client.post(
            "/auth/2fa/setup", json={"method": "totp", "code": step_up}, headers=headers
        )
        assert started.status_code == 200
        secret = started.json()["secret"]
        confirmed = client.post(
            "/auth/2fa/setup/confirm",
            json={"code": codes.totp_code_at(secret, clock.now)},
            headers=headers,
        )
        assert confirmed.status_code == 200
        assert set(confirmed.json()["recovery_codes"]).isdisjoint(old_recovery)
        assert "auth.2fa.method_changed" in actions(db)
        assert client.get("/auth/me", headers=headers).json()["two_factor"]["method"] == "totp"
        # Old recovery codes stopped working.
        clock.advance(seconds=30)
        challenge = login_start(client)
        stale = client.post(
            "/auth/login/verify",
            json={"challenge_token": challenge["challenge_token"], "code": old_recovery[0]},
        )
        assert stale.status_code == 400

    def test_new_recovery_codes(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        body = registered_user(client, outbox)
        headers = bearer(body["access_token"])
        # A recovery code can confirm the change too.
        response = client.post(
            "/auth/2fa/recovery-codes", json={"code": body["recovery_codes"][0]}, headers=headers
        )
        assert response.status_code == 200
        fresh = response.json()["recovery_codes"]
        assert len(fresh) == 10 and set(fresh).isdisjoint(body["recovery_codes"])
        assert "auth.2fa.recovery_codes_regenerated" in actions(db)
        assert (
            client.get("/auth/me", headers=headers).json()["two_factor"]["recovery_codes_remaining"]
            == 10
        )

    def test_send_code_needs_the_email_method(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        add_member(db)
        body = login_start(client, ANALYST)
        response = client.post("/auth/2fa/send-code", headers=bearer(body["access_token"]))
        assert response.status_code == 400

    def test_confirm_without_setup(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        body = registered_user(client, outbox)
        response = client.post(
            "/auth/2fa/setup/confirm", json={"code": "123456"}, headers=bearer(body["access_token"])
        )
        assert response.status_code == 400 and error_code(response) == "no_setup"

    def test_owner_promoted_without_2fa_can_set_it_up_without_a_code(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        user = add_member(db)
        body = login_start(client, ANALYST)  # still an Analyst: signed in without 2FA
        membership = db.scalars(select(Membership).where(Membership.user_id == user.id)).one()
        membership.role = "owner"  # as the Team screen does (tests/api/test_team.py)
        db.commit()
        headers = bearer(body["access_token"])
        me = client.get("/auth/me", headers=headers).json()
        assert me["two_factor"] == {
            "enabled": False,
            "method": None,
            "required": True,
            "recovery_codes_remaining": 0,
        }
        started = client.post("/auth/2fa/setup", json={"method": "email"}, headers=headers)
        assert started.status_code == 200
        code = emailed_code(outbox, ANALYST, VERIFICATION_CODE)
        done = client.post("/auth/2fa/setup/confirm", json={"code": code}, headers=headers)
        assert done.status_code == 200

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/auth/2fa/setup"),
            ("POST", "/auth/2fa/setup/confirm"),
            ("POST", "/auth/2fa/setup/resend-code"),
            ("POST", "/auth/2fa/send-code"),
            ("POST", "/auth/2fa/recovery-codes"),
            ("POST", "/auth/2fa/disable"),
            ("GET", "/auth/sessions"),
            ("POST", "/auth/sessions/revoke-others"),
            ("DELETE", "/auth/sessions/0196f000-0000-7000-8000-000000000001"),
        ],
    )
    def test_signed_in_routes_need_a_session(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, path, json={"method": "totp", "code": "123456"})
        assert response.status_code == 401
        assert error_code(response) == "not_signed_in"

    def test_signed_in_validation(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        body = registered_user(client, outbox)
        headers = bearer(body["access_token"])
        response = client.post("/auth/2fa/setup", json={"method": "sms"}, headers=headers)
        assert response.status_code == 422
        response = client.post("/auth/2fa/setup/confirm", json={}, headers=headers)
        assert response.status_code == 422 and "code" in response.json()["error"]["fields"]


def test_login_helper_still_works_for_the_email_method(
    client: TestClient, outbox: MemoryEmailSender
) -> None:
    registered_user(client, outbox)
    assert login(client)["status"] == "signed_in"
