"""Sign in with Google (OpenID Connect), behind a small interface (BUILD_SPEC 6.1, R15).

* ``AuthlibGoogleProvider``: the real thing. Authorization-code flow with PKCE (S256), ``state`` and ``nonce``.
  Authlib builds the authorization request and the PKCE challenge; the code is exchanged with one HTTPS POST;
  the ID token is checked with ``joserfc`` (Authlib's JOSE library) against Google's published keys:
  signature (RS256), ``iss``, ``aud``, ``exp``, ``iat``, ``nonce``, ``azp`` and ``email_verified``.
* ``StandInGoogleProvider``: for local development without Google credentials and for tests. Its "Google" is a
  small page served by the API itself, where you type any email address. It refuses to run in production.

Which one is used: ``GOOGLE_OAUTH_CLIENT_ID`` set -> real Google; otherwise in ``APP_ENV=development`` the
stand-in; otherwise Google sign-in is switched off (the button is hidden). See ``docs/SETUP_GOOGLE.md``.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlencode

import httpx
import redis
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an endpoint, not a secret)
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
_JWKS_TTL_SECONDS = 3600
_CLOCK_SKEW_SECONDS = 60


class GoogleSignInError(Exception):
    """Google sign-in failed. ``code`` is stable and shown to the web app; the message goes to the log."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str


class GoogleProvider(Protocol):
    kind: Literal["google", "stand_in"]

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_verifier: str
    ) -> str: ...

    def exchange(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str, now: datetime
    ) -> GoogleIdentity: ...


def validate_id_token(
    id_token: str, *, jwks: dict[str, Any], client_id: str, nonce: str, now: datetime
) -> GoogleIdentity:
    """Check a Google ID token and return the identity in it. Raises ``GoogleSignInError``."""
    try:
        token = jwt.decode(id_token, KeySet.import_key_set(jwks), algorithms=["RS256"])  # type: ignore[arg-type]
        registry = jwt.JWTClaimsRegistry(
            now=int(now.timestamp()),
            leeway=_CLOCK_SKEW_SECONDS,
            iss={"essential": True, "values": list(ISSUERS)},
            aud={"essential": True, "value": client_id},
            exp={"essential": True},
            iat={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        registry.validate(token.claims)
    except (JoseError, ValueError, TypeError) as error:
        raise GoogleSignInError("google_failed", f"ID token rejected: {error}") from error
    claims = token.claims
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id:
        raise GoogleSignInError("google_failed", "ID token azp does not match this client")
    if int(claims.get("iat", 0)) > now.timestamp() + _CLOCK_SKEW_SECONDS:
        raise GoogleSignInError("google_failed", "ID token issued in the future")
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise GoogleSignInError("google_failed", "ID token has no email address")
    verified = claims.get("email_verified")
    return GoogleIdentity(
        sub=str(claims["sub"]),
        email=email,
        email_verified=verified is True or verified == "true",
        name=str(claims.get("name") or email.partition("@")[0])[:120],
    )


class AuthlibGoogleProvider:
    kind: Literal["google", "stand_in"] = "google"

    def __init__(self, *, client_id: str, client_secret: str, http: httpx.Client) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched = 0.0
        self._lock = threading.Lock()

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_verifier: str
    ) -> str:
        url: str = prepare_grant_uri(
            AUTHORIZE_URL,
            client_id=self._client_id,
            response_type="code",
            redirect_uri=redirect_uri,
            scope=SCOPES,
            state=state,
            nonce=nonce,
            code_challenge=create_s256_code_challenge(code_verifier),
            code_challenge_method="S256",
            prompt="select_account",
        )
        return url

    def _keys(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            stale = time.monotonic() - self._jwks_fetched > _JWKS_TTL_SECONDS
            if self._jwks is None or stale or refresh:
                response = self._http.get(JWKS_URL, timeout=10)
                response.raise_for_status()
                self._jwks = response.json()
                self._jwks_fetched = time.monotonic()
            return self._jwks

    def exchange(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str, now: datetime
    ) -> GoogleIdentity:
        try:
            response = self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=10,
            )
        except httpx.HTTPError as error:
            raise GoogleSignInError(
                "google_unavailable", f"token request failed: {error}"
            ) from error
        if response.status_code != 200:
            raise GoogleSignInError(
                "google_failed", f"token endpoint answered {response.status_code}"
            )
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise GoogleSignInError("google_failed", "token response has no id_token")
        try:
            keys = self._keys()
            try:
                return validate_id_token(
                    id_token, jwks=keys, client_id=self._client_id, nonce=nonce, now=now
                )
            except GoogleSignInError:
                # Google rotates its keys; try once more with fresh ones before giving up.
                return validate_id_token(
                    id_token,
                    jwks=self._keys(refresh=True),
                    client_id=self._client_id,
                    nonce=nonce,
                    now=now,
                )
        except httpx.HTTPError as error:
            raise GoogleSignInError("google_unavailable", f"key fetch failed: {error}") from error


_STAND_IN_PREFIX = "google-stand-in:"
_STAND_IN_TTL = 300


class StandInGoogleProvider:
    """A local imitation of Google's consent screen. Never available in production."""

    kind: Literal["google", "stand_in"] = "stand_in"

    def __init__(
        self, redis_client: redis.Redis[bytes], *, api_base_url: str, app_env: str
    ) -> None:
        if app_env == "production":
            raise RuntimeError("The Google stand-in must never run in production")
        self._redis = redis_client
        self._api_base_url = api_base_url.rstrip("/")

    def authorization_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_verifier: str
    ) -> str:
        query = urlencode({"state": state, "nonce": nonce, "redirect_uri": redirect_uri})
        return f"{self._api_base_url}/auth/google/stand-in?{query}"

    def issue_code(self, identity: GoogleIdentity, *, nonce: str) -> str:
        """What "Google" does when the person picks an account: remember who it was, hand out a code."""
        code = secrets.token_urlsafe(24)
        payload = json.dumps({"nonce": nonce, **identity.__dict__})
        self._redis.set(f"{_STAND_IN_PREFIX}{code}", payload, ex=_STAND_IN_TTL)
        return code

    def exchange(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str, now: datetime
    ) -> GoogleIdentity:
        raw = self._redis.getdel(f"{_STAND_IN_PREFIX}{code}")
        if raw is None:
            raise GoogleSignInError("google_failed", "unknown or used stand-in code")
        data = json.loads(raw)
        if data.pop("nonce") != nonce:
            raise GoogleSignInError("google_failed", "stand-in nonce mismatch")
        return GoogleIdentity(**data)
