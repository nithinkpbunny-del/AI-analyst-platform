"""Helpers shared by the auth API and security tests."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from app.auth.deps import CSRF_COOKIE, REFRESH_COOKIE
from app.email.sender import MemoryEmailSender

PASSWORD = "ledger-haldi-neel-42"
_TOKEN = re.compile(r"#token=([A-Za-z0-9_\-]+)")


def link_token(outbox: MemoryEmailSender, email: str, subject: str) -> str:
    """The token from the newest email with this subject sent to ``email``."""
    messages = [m for m in outbox.to(email) if m.subject == subject]
    assert messages, f"no email '{subject}' to {email}; sent: {[m.subject for m in outbox.sent]}"
    match = _TOKEN.search(messages[-1].text)
    assert match, "email has no link token"
    return match.group(1)


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


def login(
    client: TestClient, email: str = "asha@urbancart.in", password: str = PASSWORD
) -> dict[str, Any]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def registered_user(
    client: TestClient, outbox: MemoryEmailSender, email: str = "asha@urbancart.in", **kwargs: Any
) -> dict[str, Any]:
    """Sign up, confirm the email and sign in. Returns the login response body."""
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
