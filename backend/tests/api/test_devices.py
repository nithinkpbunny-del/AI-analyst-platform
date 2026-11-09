"""New-device alerts, "This wasn't me" and your own sessions (BUILD_SPEC 6.1, R15, M3).

Route-test rule: ``/auth/secure-account`` is public (the emailed link is the credential): success, validation and
invalid link. ``/auth/sessions``: success, 401 (in test_two_factor.py) and 404 for someone else's session, which
must look exactly like a session that does not exist.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.auth.devices import DEVICE_COOKIE
from app.email.sender import MemoryEmailSender
from app.models import AuditLog, KnownDevice, Session
from tests.authflow import bearer, link_token, login, refresh, registered_user
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"
NEW_SIGN_IN = "New sign-in to your account"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"


def subjects(outbox: MemoryEmailSender, email: str = EMAIL) -> list[str]:
    return [m.subject for m in outbox.to(email)]


def other_browser(client: TestClient) -> None:
    """Forget every cookie, as a different browser would have none of ours."""
    client.cookies.clear()


class TestNewDeviceAlert:
    def test_first_device_is_not_announced_but_a_second_is(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        assert NEW_SIGN_IN not in subjects(outbox)
        assert db.scalar(select(func.count()).select_from(KnownDevice)) == 1
        login(client)  # same browser, same device cookie
        assert NEW_SIGN_IN not in subjects(outbox)

        other_browser(client)
        client.headers["user-agent"] = FIREFOX
        login(client)
        [alert] = [m for m in outbox.to(EMAIL) if m.subject == NEW_SIGN_IN]
        assert "Firefox on Linux" in alert.text
        assert "testclient" in alert.text  # the IP address as the API saw it
        assert "Mon 9 Nov 2026, 2:30 pm IST" in alert.text  # 09:00 UTC in Asia/Kolkata
        assert "UrbanCart" in alert.html
        assert "http://localhost:5173/secure-account#token=" in alert.text
        assert db.scalar(select(func.count()).select_from(KnownDevice)) == 2
        new_device = db.scalars(select(AuditLog).where(AuditLog.action == "auth.device.new")).one()
        assert new_device.details == {"browser": "Firefox on Linux"}
        success = db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "auth.login.success")
            .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        ).first()
        assert success is not None and success.details["new_device"] is True
        sessions = db.scalars(select(Session)).all()
        assert all(s.device_id and len(s.device_id) == 64 for s in sessions)

    def test_a_forged_device_cookie_counts_as_new(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        cookie = client.cookies.get(DEVICE_COOKIE)
        assert cookie
        device_id, _, _ = str(cookie).rpartition(".")
        other_browser(client)
        client.cookies.set(DEVICE_COOKIE, f"{device_id}.{'0' * 32}", path="/auth")
        login(client)
        assert NEW_SIGN_IN in subjects(outbox)


class TestThisWasntMe:
    def test_link_signs_out_everywhere_and_forgets_devices(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        first = registered_user(client, outbox)
        other_browser(client)
        second = login(client)
        token = link_token(outbox, EMAIL, NEW_SIGN_IN)

        response = client.post("/auth/secure-account", json={"token": token})
        assert response.status_code == 200
        assert response.json()["message"].startswith("Every device is signed out.")
        for body in (first, second):
            me = client.get("/auth/me", headers=bearer(body["access_token"]))
            assert me.status_code == 401
        assert refresh(client).status_code == 401
        assert db.scalar(select(func.count()).select_from(KnownDevice)) == 0
        reported = db.scalars(
            select(AuditLog).where(AuditLog.action == "auth.device.reported")
        ).one()
        assert reported.details == {"sessions_revoked": 2}
        again = client.post("/auth/secure-account", json={"token": token})
        assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_link"

    def test_link_expires_after_seven_days(
        self, client: TestClient, outbox: MemoryEmailSender, clock: FakeClock
    ) -> None:
        registered_user(client, outbox)
        other_browser(client)
        login(client)
        token = link_token(outbox, EMAIL, NEW_SIGN_IN)
        clock.advance(days=7)
        assert client.post("/auth/secure-account", json={"token": token}).status_code == 400

    def test_every_alert_link_keeps_working(
        self, client: TestClient, outbox: MemoryEmailSender
    ) -> None:
        registered_user(client, outbox)
        other_browser(client)
        login(client)
        other_browser(client)
        login(client)
        alerts = [m for m in outbox.to(EMAIL) if m.subject == NEW_SIGN_IN]
        assert len(alerts) == 2
        first_token = re.search(r"#token=([A-Za-z0-9_\-]+)", alerts[0].text)
        assert first_token
        response = client.post("/auth/secure-account", json={"token": first_token.group(1)})
        assert response.status_code == 200

    def test_validation(self, client: TestClient) -> None:
        assert client.post("/auth/secure-account", json={}).status_code == 422
        bad = client.post("/auth/secure-account", json={"token": "x" * 40})
        assert bad.status_code == 400


class TestMySessions:
    def test_list_and_sign_out_another_device(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        laptop = registered_user(client, outbox)
        other_browser(client)
        client.headers["user-agent"] = FIREFOX
        phone = login(client)
        listed = client.get("/auth/sessions", headers=bearer(phone["access_token"]))
        assert listed.status_code == 200
        items = listed.json()["sessions"]
        assert len(items) == 2
        current = next(i for i in items if i["current"])
        other = next(i for i in items if not i["current"])
        assert current["browser"] == "Firefox on Linux"
        assert other["ip"] == "testclient"

        response = client.delete(
            f"/auth/sessions/{other['id']}", headers=bearer(phone["access_token"])
        )
        assert response.status_code == 204
        assert client.get("/auth/me", headers=bearer(laptop["access_token"])).status_code == 401
        assert client.get("/auth/me", headers=bearer(phone["access_token"])).status_code == 200
        revoked = db.get(Session, other["id"])
        assert revoked is not None and revoked.revoke_reason == "signed_out_remotely"

    def test_sign_out_all_other_devices(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        first = registered_user(client, outbox)
        other_browser(client)
        login(client)
        other_browser(client)
        current = login(client)
        response = client.post(
            "/auth/sessions/revoke-others", headers=bearer(current["access_token"])
        )
        assert response.json() == {"status": "ok", "sessions_revoked": 2}
        assert client.get("/auth/me", headers=bearer(first["access_token"])).status_code == 401
        assert client.get("/auth/me", headers=bearer(current["access_token"])).status_code == 200
        assert "auth.session.revoked_others" in set(db.scalars(select(AuditLog.action)))

    def test_someone_elses_session_is_not_found(
        self, client: TestClient, db: DbSession, outbox: MemoryEmailSender
    ) -> None:
        mine = registered_user(client, outbox)
        other_browser(client)
        theirs = registered_user(
            client, outbox, "kavya@freshbasket.in", organization_name="FreshBasket"
        )
        their_session = db.scalars(
            select(Session).where(Session.org_id == theirs["organization"]["id"])
        ).one()
        response = client.delete(
            f"/auth/sessions/{their_session.id}", headers=bearer(mine["access_token"])
        )
        assert response.status_code == 404
        assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}
        missing = client.delete(
            "/auth/sessions/0196f000-0000-7000-8000-00000000dead",
            headers=bearer(mine["access_token"]),
        )
        assert missing.status_code == 404 and missing.json() == response.json()
        assert client.get("/auth/me", headers=bearer(theirs["access_token"])).status_code == 200
        listed = client.get("/auth/sessions", headers=bearer(mine["access_token"])).json()
        assert all(s["id"] != str(their_session.id) for s in listed["sessions"])

    def test_invalid_session_id(self, client: TestClient, outbox: MemoryEmailSender) -> None:
        body = registered_user(client, outbox)
        response = client.delete("/auth/sessions/not-a-uuid", headers=bearer(body["access_token"]))
        assert response.status_code == 422
