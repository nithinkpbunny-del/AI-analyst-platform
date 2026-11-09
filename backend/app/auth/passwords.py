"""Password hashing and the password policy (BUILD_SPEC 6.1).

* argon2id (``argon2-cffi`` defaults: t=3, m=64 MiB, p=4). Hashes are upgraded on login when parameters change.
* Policy: at least 10 characters, at most 128, not on the bundled list of the 100k most common passwords
  (UK NCSC list, lower-cased), not the account's own email address. No composition rules.
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 10
MAX_LENGTH = 128  # bounds hashing cost per request
_COMMON_PASSWORDS = Path(__file__).parent / "data" / "common_passwords.txt.gz"

_hasher = PasswordHasher()
# Verifying against this dummy hash when the user does not exist keeps response times similar.
_DUMMY_HASH: str | None = None


def use_hasher(hasher: PasswordHasher) -> None:
    """Swap the hasher (tests use cheap parameters so the suite stays fast)."""
    global _hasher, _DUMMY_HASH
    _hasher = hasher
    _DUMMY_HASH = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def burn_verification_time(password: str) -> None:
    """Spend the same time as a real check, for unknown emails (no account-existence timing signal)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hasher.hash("not-a-real-password-just-for-timing")
    verify_password(_DUMMY_HASH, password)


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    with gzip.open(_COMMON_PASSWORDS, "rt", encoding="utf-8") as handle:
        return frozenset(line.strip() for line in handle if line.strip())


def password_problem(password: str, *, email: str | None = None) -> str | None:
    """Return a plain-language reason the password is not allowed, or ``None`` when it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    lowered = password.strip().lower()
    if lowered in _common_passwords():
        return "This password is too common. Choose one that is harder to guess."
    if email and lowered in {email.lower(), email.split("@", 1)[0].lower()}:
        return "Do not use your email address as your password."
    if len(set(password)) < 3:
        return "This password is too easy to guess. Choose a less repetitive one."
    return None
