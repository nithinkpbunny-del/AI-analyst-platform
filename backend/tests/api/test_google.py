"""Sign in with Google (BUILD_SPEC 6.1, R15, M3), driven end to end through the stand-in provider.

The stand-in plays Google's part (no network). ID-token checks of the real provider are unit-tested in
tests/unit/test_google_oidc.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.auth.google_oidc import GoogleIdentity, StandInGoogleProvider
from app.config import Settings
from app.email.sender import MemoryEmailSender
from app.main import create_app
from app.models import AuditLog, Membership, TwoFactor, User
from tests.authflow import PASSWORD, login, registered_user, set_up_email_2fa, signup
from tests.conftest import FakeClock

EMAIL = "asha@urbancart.in"
SUB = "110169484474386276334"


@pytest.fixture
def provider(fake_redis: fakeredis.FakeRedis, settings: Settings) -> StandInGoogleProvider:
    return StandInGoogleProvider(
        fake_redis, api_base_url=settings.api_base_url, app_env=settings.app_env
    )


@pytest.fixture
def client(
    settings: Settings,
    session_factory: sessionmaker[DbSession],
    fake_redis: fakeredis.FakeRedis,
    outbox: MemoryEmailSender,
    clock: FakeClock,
    provider: StandInGoogleProvider,
) -> Iterator[TestClient]:
    app = create_app(
        settings,
        readiness_checks={},
        session_factory=session_factory,
        redis_client=fake_redis,
        email_sender=outbox,
        clock=clock,
        google_provider=provider,
    )
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client


def google_redirect(
    client: TestClient,
    provider: StandInGoogleProvider,
    identity: GoogleIdentity,
) -> str:
    """Click "Continue with Google", pick an account, come back. Returns where the browser lands."""
    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = provider.issue_code(identity, nonce=query["nonce"][0])
    back = client.get(
        "/auth/google/callback",
        params={"code": code, "state": query["state"][0]},
        follow_redirects=False,
    )
    assert back.status_code == 302
    return str(back.headers["location"])


def token_from(location: str) -> str:
    match = re.search(r"#token=([A-Za-z0-9_\-]+)$", location)
    assert match, location
    return match.group(1)


def asha(**overrides: Any) -> GoogleIdentity:
    values: dict[str, Any] = {
        "sub": SUB,
        "email": EMAIL,
        "email_verified": True,
        "name": "Asha Rao",
    }
    values.update(overrides)
    return GoogleIdentity(**values)


class TestStart:
    def test_providers_and_start_redirect(self, client: TestClient) -> None:
        assert client.get("/auth/providers").json() == {"google": True, "google_stand_in": True}
        start = client.get("/auth/google/start", follow_redirects=False)
        assert start.status_code == 302
        location = urlparse(start.headers["location"])
        assert location.path == "/auth/google/stand-in"
        query = parse_qs(location.query)
        assert query["state"][0] and query["nonce"][0]
        cookie = next(
            c for c in start.headers.get_list("set-cookie") if c.startswith("analyst_google_state=")
        ).lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/auth/google" in cookie
        assert query["state"][0].lower() in cookie

    def test_stand_in_page_round_trip(self, client: TestClient) -> None:
        start = client.get("/auth/google/start", follow_redirects=False)
        page = client.get(start.headers["location"])
        assert page.status_code == 200 and "Google sign-in stand-in" in page.text
        query = parse_qs(urlparse(start.headers["location"]).query)
        submitted = client.post(
            "/auth/google/stand-in",
            content=(
                f"state={query['state'][0]}&nonce={query['nonce'][0]}"
                "&email=new%40shop.in&name=New+Person&email_verified=1"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert submitted.status_code == 303
        back = client.get(submitted.headers["location"], follow_redirects=False)
        assert back.headers["location"].startswith("http://localhost:5173/signup/google#token=")

    def test_no_provider_means_no_google(
        self,
        settings: Settings,
        session_factory: sessionmaker[DbSession],
        fake_redis: fakeredis.FakeRedis,
        outbox: MemoryEmailSender,
    ) -> None:
        app = create_app(
            settings,
            readiness_checks={},
            session_factory=session_factory,
            redis_client=fake_redis,
            email_sender=outbox,
        )
        assert app.state.google_provider is None  # APP_ENV=test without a client id
        with TestClient(app, base_url="http://localhost:8000") as plain:
            assert plain.get("/auth/providers").json() == {
                "google": False,
                "google_stand_in": False,
            }
            start = plain.get("/auth/google/start", follow_redirects=False)
            assert (
                start.headers["location"]
                == "http://localhost:5173/signin?google=google_unavailable"
            )
            assert plain.get("/auth/google/stand-in").status_code == 404


class TestCallbackChecks:
    def test_state_must_match_the_browser(
        self, client: TestClient, provider: StandInGoogleProvider
    ) -> None:
        start = client.get("/auth/google/start", follow_redirects=False)
        query = parse_qs(urlparse(start.headers["location"]).query)
        code = provider.issue_code(asha(), nonce=query["nonce"][0])
        client.cookies.clear()  # a different browser (login CSRF attempt)
        back = client.get(
            "/auth/google/callback",
            params={"code": code, "state": query["state"][0]},
            follow_redirects=False,
        )
        assert back.headers["location"] == "http://localhost:5173/signin?google=google_failed"

    def test_state_is_single_use(self, client: TestClient, provider: StandInGoogleProvider) -> None:
        start = client.get("/auth/google/start", follow_redirects=False)
        query = parse_qs(urlparse(start.headers["location"]).query)
        state = query["state"][0]
        cookie = client.cookies.get("analyst_google_state")
        code = provider.issue_code(asha(), nonce=query["nonce"][0])
        client.get("/auth/google/callback", params={"code": code, "state": state})
        client.cookies.set("analyst_google_state", str(cookie), path="/auth/google")
        again = client.get(
            "/auth/google/callback",
            params={"code": code, "state": state},
            follow_redirects=False,
        )
        assert again.headers["location"].endswith("?google=google_expired")

    @pytest.mark.parametrize(
        ("error", "expected"),
        [("access_denied", "google_cancelled"), ("server_error", "google_failed")],
    )
    def test_google_errors(self, client: TestClient, error: str, expected: str) -> None:
        back = client.get("/auth/google/callback", params={"error": error}, follow_redirects=False)
        assert back.headers["location"] == f"http://localhost:5173/signin?google={expected}"

    def test_unverified_google_email_is_refused(
        self, client: TestClient, db: DbSession, provider: StandInGoogleProvider
    ) -> None:
        location = google_redirect(client, provider, asha(email_verified=False))
        assert location.endswith("?google=google_email_unverified")
        assert db.scalars(select(User)).first() is None


class TestNewPerson:
    def test_sign_up_with_google_then_set_up_2fa(
        self, client: TestClient, db: DbSession, provider: StandInGoogleProvider
    ) -> None:
        location = google_redirect(client, provider, asha())
        assert location.startswith("http://localhost:5173/signup/google#token=")
        token = token_from(location)
        info = client.post("/auth/google/signup-info", json={"token": token})
        assert info.json() == {"email": EMAIL, "name": "Asha Rao"}

        no_consent = client.post(
            "/auth/google/signup",
            json={
                "token": token,
                "name": "Asha",
                "organization_name": "UrbanCart",
                "accept_terms": False,
            },
        )
        assert no_consent.status_code == 422
        response = client.post(
            "/auth/google/signup",
            json={
                "token": token,
                "name": "Asha Rao",
                "organization_name": "UrbanCart",
                "accept_terms": True,
            },
        )
        assert response.status_code == 200
        challenge = response.json()
        assert challenge["status"] == "two_factor_setup_required"  # a new Owner
        session = set_up_email_2fa(client, EMAIL, challenge)
        assert session["user"]["has_password"] is False
        assert session["user"]["google_linked"] is True
        user = db.scalars(select(User)).one()
        assert (user.google_sub, user.password_hash) == (SUB, None)
        assert user.email_verified_at is not None
        membership = db.scalars(select(Membership)).one()
        assert membership.role == "owner"
        signup_entry = db.scalars(select(AuditLog).where(AuditLog.action == "auth.signup")).one()
        assert signup_entry.details == {"method": "google"}
        success = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login.success")).one()
        assert success.details["method"] == "google"
        again = client.post(
            "/auth/google/signup",
            json={
                "token": token,
                "name": "Asha",
                "organization_name": "Other",
                "accept_terms": True,
            },
        )
        assert again.status_code == 401

    def test_password_login_is_not_possible_for_a_google_only_account(
        self, client: TestClient, provider: StandInGoogleProvider
    ) -> None:
        token = token_from(google_redirect(client, provider, asha()))
        client.post(
            "/auth/google/signup",
            json={
                "token": token,
                "name": "Asha",
                "organization_name": "UrbanCart",
                "accept_terms": True,
            },
        )
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 401

    def test_email_taken_in_the_meantime(
        self, client: TestClient, outbox: MemoryEmailSender, provider: StandInGoogleProvider
    ) -> None:
        token = token_from(google_redirect(client, provider, asha()))
        signup(client, EMAIL)  # someone signs up with a password before the form is sent
        response = client.post(
            "/auth/google/signup",
            json={
                "token": token,
                "name": "Asha",
                "organization_name": "UrbanCart",
                "accept_terms": True,
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "account_exists"


class TestExistingAccount:
    def test_verified_password_account_is_linked(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        provider: StandInGoogleProvider,
    ) -> None:
        registered_user(client, outbox)  # Owner with email 2FA
        location = google_redirect(client, provider, asha())
        assert location.startswith("http://localhost:5173/signin/google#token=")
        finish = client.post("/auth/google/finish", json={"token": token_from(location)})
        assert finish.status_code == 200
        challenge = finish.json()
        assert challenge["status"] == "two_factor_required"  # Google does not skip our 2FA
        user = db.scalars(select(User)).one()
        assert user.google_sub == SUB and user.password_hash is not None
        assert "auth.google.linked" in set(db.scalars(select(AuditLog.action)))
        replay = client.post("/auth/google/finish", json={"token": token_from(location)})
        assert replay.status_code == 401
        assert login(client)["status"] == "signed_in"  # the password still works

    def test_unconfirmed_password_is_removed_when_google_proves_the_email(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        provider: StandInGoogleProvider,
    ) -> None:
        signup(client, EMAIL)  # never confirmed: maybe not even the owner of the address
        location = google_redirect(client, provider, asha())
        assert location.startswith("http://localhost:5173/signin/google#token=")
        user = db.scalars(select(User)).one()
        assert user.password_hash is None
        assert user.email_verified_at is not None
        response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 401

    def test_email_linked_to_another_google_account(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        provider: StandInGoogleProvider,
    ) -> None:
        registered_user(client, outbox)
        google_redirect(client, provider, asha())
        location = google_redirect(client, provider, asha(sub="another-sub"))
        assert location.endswith("?google=google_other_account")

    def test_analyst_without_2fa_gets_a_session_straight_away(
        self,
        client: TestClient,
        db: DbSession,
        outbox: MemoryEmailSender,
        provider: StandInGoogleProvider,
    ) -> None:
        registered_user(client, outbox)
        membership = db.scalars(select(Membership)).one()
        membership.role = "analyst"  # an Analyst who never turned 2FA on
        two_factor = db.get(TwoFactor, membership.user_id)
        assert two_factor is not None
        db.delete(two_factor)
        db.commit()
        location = google_redirect(client, provider, asha())
        finish = client.post("/auth/google/finish", json={"token": token_from(location)})
        assert finish.json()["status"] == "signed_in"
        assert client.cookies.get("analyst_refresh")

    def test_finish_validation(self, client: TestClient) -> None:
        assert client.post("/auth/google/finish", json={}).status_code == 422
        response = client.post("/auth/google/finish", json={"token": "x" * 40})
        assert response.status_code == 401
