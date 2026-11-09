"""Access tokens and opaque tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth import tokens

SECRET = "s" * 40
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _issue(now: datetime = NOW) -> tuple[str, uuid.UUID]:
    sid = uuid.uuid4()
    token, _ = tokens.issue_access_token(
        secret=SECRET,
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        role="analyst",
        session_id=sid,
        now=now,
        lifetime=timedelta(minutes=10),
    )
    return token, sid


def test_round_trip() -> None:
    token, sid = _issue()
    claims = tokens.decode_access_token(token, secret=SECRET, now=NOW + timedelta(minutes=9))
    assert claims.session_id == sid
    assert claims.role == "analyst"
    assert claims.expires_at == NOW + timedelta(minutes=10)


def test_expired() -> None:
    token, _ = _issue()
    with pytest.raises(tokens.ExpiredAccessToken):
        tokens.decode_access_token(token, secret=SECRET, now=NOW + timedelta(minutes=10))


def test_issued_in_the_future() -> None:
    token, _ = _issue(NOW + timedelta(hours=1))
    with pytest.raises(tokens.InvalidAccessToken):
        tokens.decode_access_token(token, secret=SECRET, now=NOW)


def test_wrong_secret_and_garbage() -> None:
    token, _ = _issue()
    for bad in (token[:-2] + "xx", "abc", ""):
        with pytest.raises(tokens.InvalidAccessToken):
            tokens.decode_access_token(bad, secret=SECRET, now=NOW)
    with pytest.raises(tokens.InvalidAccessToken):
        tokens.decode_access_token(token, secret="t" * 40, now=NOW)


def test_missing_claims() -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iss": tokens.ISSUER, "aud": tokens.AUDIENCE},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(tokens.InvalidAccessToken):
        tokens.decode_access_token(token, secret=SECRET, now=NOW)


def test_opaque_tokens_are_random_and_hashed() -> None:
    a, b = tokens.new_opaque_token(), tokens.new_opaque_token()
    assert a != b and len(a) >= 43
    assert tokens.hash_token(a) == tokens.hash_token(a)
    assert len(tokens.hash_token(a)) == 64
    assert tokens.tokens_match(a, a) and not tokens.tokens_match(a, b)
