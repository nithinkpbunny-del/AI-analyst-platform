"""Pairing a Tally PC with a workspace (DECISIONS D108).

* An Owner gets a **one-time code** like ``K7QM-3XRP`` (8 characters from 31 that cannot be confused: no I, L, O,
  0, 1; about 40 bits). Only its SHA-256 hash is kept, in Redis, for ``TALLY_PAIRING_CODE_MINUTES`` (10). A new
  code for the same connection cancels the previous one; redeeming deletes it (``GETDEL``), so it works once.
* Redeeming gives the PC a **device token** (``tlyd_`` + 256 random bits). Only its SHA-256 hash is stored
  (``tally_devices.token_hash``); the token is shown to the bridge once and never again.
* Guessing is limited per IP address (``PAIR_ATTEMPTS`` per 15 minutes) on top of the short life of a code.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import redis

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
TOKEN_PREFIX = "tlyd_"  # noqa: S105 - a prefix that marks device tokens, not a secret
PAIR_ATTEMPTS = 10
_CODE = "tally:pair:"
_CODE_OF = "tally:pair-of:"
_REFRESH = "tally:refresh:"
_SEPARATORS = re.compile(r"[\s-]")


def normalize(code: str) -> str | None:
    value = _SEPARATORS.sub("", code.upper())
    if len(value) != CODE_LENGTH or any(ch not in ALPHABET for ch in value):
        return None
    return value


def display(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return _hash(token)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def looks_like_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX) and 30 <= len(value) <= 100 and value.isascii()


@dataclass(frozen=True)
class PairingCode:
    code: str  # as shown: "K7QM-3XRP"
    expires_at: datetime


def issue_code(
    client: redis.Redis[bytes],
    *,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    minutes: int,
) -> PairingCode:
    cancel_code(client, connection_id)
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    digest = _hash(code)
    expires = now + timedelta(minutes=minutes)
    payload = json.dumps(
        {
            "org_id": str(org_id),
            "connection_id": str(connection_id),
            "user_id": str(user_id),
            "expires_at": expires.isoformat(),
        }
    )
    pipe = client.pipeline()
    pipe.set(f"{_CODE}{digest}", payload, ex=minutes * 60)
    pipe.set(f"{_CODE_OF}{connection_id}", digest, ex=minutes * 60)
    pipe.execute()
    return PairingCode(display(code), expires)


def cancel_code(client: redis.Redis[bytes], connection_id: uuid.UUID) -> None:
    previous = client.getdel(f"{_CODE_OF}{connection_id}")
    if previous:
        client.delete(f"{_CODE}{previous.decode()}")


def redeem_code(client: redis.Redis[bytes], code: str, now: datetime) -> dict[str, Any] | None:
    """The code's workspace, connection and person, once; ``None`` for anything wrong, used or expired."""
    value = normalize(code)
    if value is None:
        return None
    digest = _hash(value)
    raw = client.getdel(f"{_CODE}{digest}")
    if raw is None:
        return None
    data: dict[str, Any] = json.loads(raw)
    current = client.get(f"{_CODE_OF}{data['connection_id']}")
    if current is not None and current.decode() == digest:
        client.delete(f"{_CODE_OF}{data['connection_id']}")
    if datetime.fromisoformat(str(data["expires_at"])) <= now:
        return None
    return data


# ------------------------------------------------------------------------------- "read again now" requests
def request_refresh(
    client: redis.Redis[bytes], connection_id: uuid.UUID, *, full: bool = False
) -> None:
    """The only thing the platform can ask the bridge (DECISIONS D109): read Tally again. Kept a day, so a PC
    that is offline now does it when it comes back."""
    client.set(f"{_REFRESH}{connection_id}", "full" if full else "changes", ex=24 * 3600)


def take_refresh(client: redis.Redis[bytes], connection_id: uuid.UUID) -> str | None:
    raw = client.getdel(f"{_REFRESH}{connection_id}")
    return raw.decode() if raw else None


def refresh_pending(client: redis.Redis[bytes], connection_id: uuid.UUID) -> bool:
    return bool(client.exists(f"{_REFRESH}{connection_id}"))


def clear_refresh(client: redis.Redis[bytes], connection_id: uuid.UUID) -> None:
    client.delete(f"{_REFRESH}{connection_id}")
