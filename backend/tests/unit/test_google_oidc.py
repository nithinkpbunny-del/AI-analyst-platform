"""Google ID-token checks and the providers, without any network (BUILD_SPEC 6.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import fakeredis
import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from app.auth.google_oidc import (
    JWKS_URL,
    TOKEN_URL,
    AuthlibGoogleProvider,
    GoogleIdentity,
    GoogleSignInError,
    StandInGoogleProvider,
    validate_id_token,
)

CLIENT_ID = "1234-test.apps.googleusercontent.com"
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
KEY = RSAKey.generate_key(2048, parameters={"kid": "key-1", "use": "sig", "alg": "RS256"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "key-1", "use": "sig", "alg": "RS256"})


def jwks(*keys: RSAKey) -> dict[str, Any]:
    return dict(KeySet(list(keys)).as_dict(private=False))


def id_token(key: RSAKey = KEY, kid: str = "key-1", **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": "110169484474386276334",
        "email": "asha@urbancart.in",
        "email_verified": True,
        "name": "Asha Rao",
        "nonce": "nonce-1",
        "iat": int(NOW.timestamp()) - 10,
        "exp": int((NOW + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode({"alg": "RS256", "kid": kid}, claims, key)


def check(token: str, nonce: str = "nonce-1") -> GoogleIdentity:
    return validate_id_token(token, jwks=jwks(KEY), client_id=CLIENT_ID, nonce=nonce, now=NOW)


def test_valid_token_gives_the_identity() -> None:
    identity = check(id_token())
    assert identity == GoogleIdentity(
        sub="110169484474386276334",
        email="asha@urbancart.in",
        email_verified=True,
        name="Asha Rao",
    )
    assert check(id_token(iss="accounts.google.com")).sub == identity.sub


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
        {"exp": int((NOW - timedelta(minutes=5)).timestamp())},
        {"nonce": "another-nonce"},
        {"nonce": None},
        {"sub": None},
        {"iat": int((NOW + timedelta(minutes=10)).timestamp())},
        {"aud": [CLIENT_ID, "other"], "azp": "other"},
        {"email": None},
    ],
)
def test_bad_claims_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(GoogleSignInError) as caught:
        check(id_token(**overrides))
    assert caught.value.code == "google_failed"


def test_signature_from_another_key_is_rejected() -> None:
    with pytest.raises(GoogleSignInError):
        check(id_token(OTHER_KEY))
    with pytest.raises(GoogleSignInError):
        check("not-a-jwt")


@pytest.mark.parametrize("value", [False, "false", None])
def test_unverified_email_is_reported(value: Any) -> None:
    assert check(id_token(email_verified=value)).email_verified is False


class FakeGoogle:
    """Google's token and key endpoints, in memory."""

    def __init__(self) -> None:
        self.keys = jwks(KEY)
        self.token_body: dict[str, Any] = {"id_token": id_token(), "access_token": "ya29.x"}
        self.token_status = 200
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(self.token_status, json=self.token_body)
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json=self.keys)
        return httpx.Response(404)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def provider(google: FakeGoogle) -> AuthlibGoogleProvider:
    return AuthlibGoogleProvider(
        client_id=CLIENT_ID,
        client_secret="test-client-secret",
        http=httpx.Client(transport=httpx.MockTransport(google.handler)),
    )


def test_authorization_url_uses_pkce_state_and_nonce(provider: AuthlibGoogleProvider) -> None:
    url = provider.authorization_url(
        redirect_uri="http://localhost:8000/auth/google/callback",
        state="state-1",
        nonce="nonce-1",
        code_verifier="v" * 50,
    )
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["client_id"] == CLIENT_ID
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email profile"
    assert (query["state"], query["nonce"]) == ("state-1", "nonce-1")
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] != "v" * 50  # the hash, never the verifier itself
    assert "client_secret" not in query


def test_exchange_posts_the_code_and_checks_the_token(
    provider: AuthlibGoogleProvider, google: FakeGoogle
) -> None:
    identity = provider.exchange(
        code="code-1",
        redirect_uri="http://localhost:8000/auth/google/callback",
        code_verifier="v" * 50,
        nonce="nonce-1",
        now=NOW,
    )
    assert identity.email == "asha@urbancart.in"
    token_request = next(r for r in google.requests if str(r.url) == TOKEN_URL)
    form = parse_qs(token_request.content.decode())
    assert form["code"] == ["code-1"] and form["code_verifier"] == ["v" * 50]
    assert form["grant_type"] == ["authorization_code"]


def test_exchange_refetches_rotated_keys(
    provider: AuthlibGoogleProvider, google: FakeGoogle
) -> None:
    provider.exchange(code="c", redirect_uri="r", code_verifier="v" * 50, nonce="nonce-1", now=NOW)
    new_key = RSAKey.generate_key(2048, parameters={"kid": "key-2", "use": "sig", "alg": "RS256"})
    google.keys = jwks(new_key)
    google.token_body = {"id_token": id_token(new_key, kid="key-2")}
    identity = provider.exchange(
        code="c", redirect_uri="r", code_verifier="v" * 50, nonce="nonce-1", now=NOW
    )
    assert identity.sub == "110169484474386276334"
    assert sum(str(r.url) == JWKS_URL for r in google.requests) == 2


@pytest.mark.parametrize(("status", "body"), [(400, {"error": "invalid_grant"}), (200, {})])
def test_exchange_failures(
    provider: AuthlibGoogleProvider, google: FakeGoogle, status: int, body: dict[str, Any]
) -> None:
    google.token_status, google.token_body = status, body
    with pytest.raises(GoogleSignInError) as caught:
        provider.exchange(code="c", redirect_uri="r", code_verifier="v" * 50, nonce="n", now=NOW)
    assert caught.value.code == "google_failed"


def test_network_failure_is_reported_as_unavailable() -> None:
    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    provider = AuthlibGoogleProvider(
        client_id=CLIENT_ID,
        client_secret="s",
        http=httpx.Client(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(GoogleSignInError) as caught:
        provider.exchange(code="c", redirect_uri="r", code_verifier="v" * 50, nonce="n", now=NOW)
    assert caught.value.code == "google_unavailable"


def test_stand_in_codes_are_single_use_and_nonce_bound() -> None:
    stand_in = StandInGoogleProvider(
        fakeredis.FakeRedis(), api_base_url="http://localhost:8000", app_env="development"
    )
    who = GoogleIdentity(sub="s1", email="asha@urbancart.in", email_verified=True, name="Asha")
    code = stand_in.issue_code(who, nonce="n1")
    assert (
        stand_in.exchange(code=code, redirect_uri="r", code_verifier="v", nonce="n1", now=NOW)
        == who
    )
    with pytest.raises(GoogleSignInError):
        stand_in.exchange(code=code, redirect_uri="r", code_verifier="v", nonce="n1", now=NOW)
    other = stand_in.issue_code(who, nonce="n1")
    with pytest.raises(GoogleSignInError):
        stand_in.exchange(code=other, redirect_uri="r", code_verifier="v", nonce="n2", now=NOW)
    assert stand_in.authorization_url(
        redirect_uri="r", state="s", nonce="n", code_verifier="v"
    ).startswith("http://localhost:8000/auth/google/stand-in?")


def test_stand_in_refuses_production() -> None:
    with pytest.raises(RuntimeError):
        StandInGoogleProvider(fakeredis.FakeRedis(), api_base_url="https://x", app_env="production")
