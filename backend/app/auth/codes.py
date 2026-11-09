"""The second factor's building blocks (BUILD_SPEC 6.1): TOTP, 6-digit email codes and recovery codes.

* TOTP: RFC 6238 via ``pyotp`` (SHA-1, 6 digits, 30 s), ±1 time step. A code is accepted only for a step
  later than the last one used, so an observed code cannot be replayed.
* Email codes: 6 random digits.
* Recovery codes: 10 per person, ``xxxxx-xxxxx`` from an alphabet without look-alike characters (~49 bits each).
"""

from __future__ import annotations

import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pyotp
import segno

ISSUER = "Analyst"
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_RECOVERY_LENGTH = 10
_SEPARATORS = re.compile(r"[\s\-]")


@dataclass(frozen=True)
class TotpProvisioning:
    secret: str
    uri: str
    # QR code modules, one string of "0"/"1" per row (no quiet zone). The web app draws it, so no image library
    # is needed in the browser and the secret never goes to a third-party QR service.
    qr: list[str]


def new_totp_secret() -> str:
    return pyotp.random_base32()  # 32 base32 characters = 160 bits


def totp_provisioning(secret: str, email: str) -> TotpProvisioning:
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    qr = segno.make(uri, error="m", micro=False)
    rows = ["".join("1" if cell else "0" for cell in row) for row in qr.matrix_iter(border=0)]
    return TotpProvisioning(secret=secret, uri=uri, qr=rows)


def totp_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The time step ``code`` belongs to (now ±1 step), or ``None``. Steps up to ``last_step`` are refused."""
    totp = pyotp.TOTP(secret)
    current = totp.timecode(now)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def totp_code_at(secret: str, now: datetime) -> str:
    """The current code (used by tests and the development helpers only)."""
    return pyotp.TOTP(secret).at(now)


def new_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_LENGTH))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


CodeKind = Literal["otp", "recovery"]


def classify(code: str) -> tuple[CodeKind, str] | None:
    """Normalise what the person typed: a 6-digit code, a recovery code, or neither."""
    cleaned = _SEPARATORS.sub("", code).lower()
    if len(cleaned) == 6 and cleaned.isdigit():
        return "otp", cleaned
    if len(cleaned) == _RECOVERY_LENGTH and all(c in _RECOVERY_ALPHABET for c in cleaned):
        return "recovery", cleaned
    return None
