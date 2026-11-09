"""Helpers shared by the auth API and security tests."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from app.auth.deps import CSRF_COOKIE, REFRESH_COOKIE
from app.email.sender import MemoryEmailSender

PASSWORD = "ledger-haldi-neel-42"
_TOKEN = re.compile(r"#token=([A-Za-z0-9_\-]+)")
_CODE = re.compile(r"^\s+(\d{6})\s*$", re.MULTILINE)
SIGN_IN_CODE = "Your sign-in code"
VERIFICATION_CODE = "Your verification code"


def link_token(outbox: MemoryEmailSender, email: str, subject: str) -> str:
    """The token from the newest email with this subject sent to ``email``."""
    messages = [m for m in outbox.to(email) if m.subject == subject]
    assert messages, f"no email '{subject}' to {email}; sent: {[m.subject for m in outbox.sent]}"
    match = _TOKEN.search(messages[-1].text)
    assert match, "email has no link token"
    return match.group(1)


def emailed_code(outbox: MemoryEmailSender, email: str, subject: str = SIGN_IN_CODE) -> str:
    """The 6-digit code from the newest email with this subject."""
    messages = [m for m in outbox.to(email) if m.subject == subject]
    assert messages, f"no email '{subject}' to {email}; sent: {[m.subject for m in outbox.sent]}"
    match = _CODE.search(messages[-1].text)
    assert match, "email has no code"
    return match.group(1)


def _outbox(client: TestClient) -> MemoryEmailSender:
    sender = client.app.state.email_sender  # type: ignore[attr-defined]
    assert isinstance(sender, MemoryEmailSender)
    return sender


def signup(
    client: TestClient,
    email: str = "asha@urbancart.in",
    *,
    password: str = PASSWORD,
    name: str = "Asha Rao",
    organization_name: str = "UrbanCart",
) -> Any:
    response = client.post(
        "/auth/signup",
        json={
            "name": name,
            "email": email,
            "password": password,
            "organization_name": organization_name,
            "accept_terms": True,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def verify(client: TestClient, outbox: MemoryEmailSender, email: str) -> None:
    token = link_token(outbox, email, "Confirm your email address")
    response = client.post("/auth/verify-email", json={"token": token})
    assert response.status_code == 200, response.text


def login_start(
    client: TestClient, email: str = "asha@urbancart.in", password: str = PASSWORD
) -> dict[str, Any]:
    """POST /auth/login and return the body: a session or a 2FA challenge."""
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def finish_with_email_code(
    client: TestClient, email: str, challenge: dict[str, Any]
) -> dict[str, Any]:
    assert challenge["status"] == "two_factor_required", challenge
    assert challenge["method"] == "email", "this helper only handles the email method"
    code = emailed_code(_outbox(client), email)
    response = client.post(
        "/auth/login/verify",
        json={"challenge_token": challenge["challenge_token"], "code": code},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def set_up_email_2fa(client: TestClient, email: str, challenge: dict[str, Any]) -> dict[str, Any]:
    """An Owner's first sign-in: set up the email method and finish. Returns the session + recovery codes."""
    assert challenge["status"] == "two_factor_setup_required", challenge
    token = challenge["challenge_token"]
    started = client.post("/auth/login/setup", json={"challenge_token": token, "method": "email"})
    assert started.status_code == 200, started.text
    code = emailed_code(_outbox(client), email, VERIFICATION_CODE)
    done = client.post("/auth/login/setup/confirm", json={"challenge_token": token, "code": code})
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    return body


def login(
    client: TestClient, email: str = "asha@urbancart.in", password: str = PASSWORD
) -> dict[str, Any]:
    """Sign in all the way, whatever the second step is (email 2FA set-up for a new Owner, email code)."""
    body = login_start(client, email, password)
    if body["status"] == "two_factor_setup_required":
        return set_up_email_2fa(client, email, body)
    if body["status"] == "two_factor_required":
        return finish_with_email_code(client, email, body)
    return body


def registered_user(
    client: TestClient, outbox: MemoryEmailSender, email: str = "asha@urbancart.in", **kwargs: Any
) -> dict[str, Any]:
    """Sign up, confirm the email and sign in (setting up email 2FA, as every Owner must)."""
    signup(client, email, **kwargs)
    verify(client, outbox, email)
    return login(client, email, kwargs.get("password", PASSWORD))


def refresh(client: TestClient, csrf: str | None = None) -> Any:
    token = csrf if csrf is not None else client.cookies.get(CSRF_COOKIE, "")
    return client.post("/auth/refresh", headers={"X-CSRF-Token": token})


def refresh_cookie(client: TestClient) -> str:
    value = client.cookies.get(REFRESH_COOKIE)
    assert value, "no refresh cookie"
    return str(value)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
