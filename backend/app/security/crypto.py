"""Envelope encryption and keyed hashes (BUILD_SPEC 6.4).

* ``seal`` / ``unseal``: every value gets its own random 256-bit data key (DEK). The value is encrypted with
  AES-256-GCM under the DEK, and the DEK is wrapped (AES-256-GCM again) by the key-encryption key (KEK). The KEK
  is derived from ``MASTER_KEY`` with HKDF-SHA256; in production it moves behind KMS (M5), which is why the
  blob records a ``key_version``. ``context`` is bound as associated data, so a ciphertext copied into another
  row (another user, another purpose) does not decrypt.
* ``keyed_hash``: HMAC-SHA256 with a key derived from ``MASTER_KEY``. Used for short secrets that must be
  compared but never read back (recovery codes, email codes). Unlike a plain hash, a database leak alone does
  not let anyone brute-force them offline.

Blob layout (bytes): ``version (1) | key_version (1) | dek_nonce (12) | wrapped_dek (48) | nonce (12) | ct``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_FORMAT_VERSION = 1
KEY_VERSION = 1
_NONCE = 12
_WRAPPED_DEK = 32 + 16  # key + GCM tag


class CryptoError(Exception):
    """Wrong key, tampered blob or wrong context."""


class MissingMasterKey(CryptoError):
    """``MASTER_KEY`` is empty: encryption is unavailable."""


@lru_cache(maxsize=8)
def _derive(master_key: str, info: bytes) -> bytes:
    if not master_key:
        raise MissingMasterKey("MASTER_KEY is not set. Generate one with: openssl rand -base64 32")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        master_key.encode("utf-8")
    )


def _kek(master_key: str, key_version: int) -> bytes:
    return _derive(master_key, f"analyst/kek/v{key_version}".encode())


def seal(plaintext: bytes, *, master_key: str, context: str) -> bytes:
    aad = context.encode("utf-8")
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(_NONCE)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
    dek_nonce = os.urandom(_NONCE)
    wrapped = AESGCM(_kek(master_key, KEY_VERSION)).encrypt(dek_nonce, dek, aad)
    return bytes([_FORMAT_VERSION, KEY_VERSION]) + dek_nonce + wrapped + nonce + ciphertext


def unseal(blob: bytes, *, master_key: str, context: str) -> bytes:
    header = 2 + _NONCE + _WRAPPED_DEK + _NONCE
    if len(blob) < header or blob[0] != _FORMAT_VERSION:
        raise CryptoError("unknown ciphertext format")
    aad = context.encode("utf-8")
    key_version = blob[1]
    dek_nonce = blob[2 : 2 + _NONCE]
    wrapped = blob[2 + _NONCE : 2 + _NONCE + _WRAPPED_DEK]
    nonce = blob[2 + _NONCE + _WRAPPED_DEK : header]
    try:
        dek = AESGCM(_kek(master_key, key_version)).decrypt(dek_nonce, wrapped, aad)
        return AESGCM(dek).decrypt(nonce, blob[header:], aad)
    except InvalidTag as error:
        raise CryptoError("ciphertext could not be decrypted") from error


def keyed_hash(value: str, *, master_key: str, purpose: str) -> str:
    key = _derive(master_key, f"analyst/hmac/{purpose}".encode())
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def signing_key(secret: str, purpose: str) -> bytes:
    """A purpose-specific key derived from any server secret (e.g. ``JWT_SECRET`` for the device cookie)."""
    return _derive(secret, f"analyst/sign/{purpose}".encode())


def hmac_equal(a: str, b: str) -> bool:
    """Constant-time comparison of two hex digests."""
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))
