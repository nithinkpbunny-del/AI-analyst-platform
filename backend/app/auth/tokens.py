"""Access tokens (JWT) and opaque random tokens (refresh tokens, email links, CSRF).

Access JWT: HS256, ``ACCESS_TOKEN_MINUTES`` lifetime, claims ``sub, org, role, sid`` (BUILD_SPEC 6.1) plus
``iat``, ``exp``, ``iss``, ``aud`` and ``typ``. Expiry is checked against the injected clock so tests can move
time; the signature, algorithm, issuer and audience are checked by PyJWT.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

ALGORITHM = "HS256"
ISSUER = "analyst-api"
AUDIENCE = "analyst-app"
TOKEN_TYPE = "access"  # noqa: S105 (a claim value, not a secret)


class InvalidAccessToken(Exception):
    """Bad signature, wrong claims or malformed token."""


class ExpiredAccessToken(InvalidAccessToken):
    """Well-formed and correctly signed, but past ``exp``: the client should refresh."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    session_id: uuid.UUID
    expires_at: datetime


def new_opaque_token() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are stored as SHA-256 hex. They are high-entropy, so a fast hash is the right tool."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def issue_access_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID,
    now: datetime,
    lifetime: timedelta,
) -> tuple[str, datetime]:
    expires_at = now + lifetime
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "typ": TOKEN_TYPE,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires_at


def decode_access_token(token: str, *, secret: str, now: datetime) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                # Time claims are checked below against the injected clock, not the system clock.
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "require": ["sub", "org", "role", "sid", "exp", "iat", "iss", "aud"],
            },
        )
        if payload.get("typ") != TOKEN_TYPE:
            raise InvalidAccessToken("wrong token type")
        claims = AccessClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            org_id=uuid.UUID(str(payload["org"])),
            role=str(payload["role"]),
            session_id=uuid.UUID(str(payload["sid"])),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=now.tzinfo),
        )
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        raise InvalidAccessToken(str(error)) from error
    if int(payload["iat"]) > now.timestamp() + 60:  # issued in the future (60 s clock skew allowed)
        raise InvalidAccessToken("token issued in the future")
    if claims.expires_at <= now:
        raise ExpiredAccessToken("token expired")
    return claims
