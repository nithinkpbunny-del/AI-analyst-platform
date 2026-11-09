"""Envelope encryption and keyed hashes (BUILD_SPEC 6.4).

* ``encrypt_envelope`` / ``decrypt_envelope``: every value gets its own random 256-bit data key (DEK). The value
  is encrypted with AES-256-GCM under the DEK, and the DEK is wrapped by a ``KeyWrapper``: ``MasterKeyWrapper``
  (AES-256-GCM under a key-encryption key derived from ``MASTER_KEY`` with HKDF-SHA256; development and tests) or
  ``KmsKeyWrapper`` (AWS KMS, production). The envelope records a ``key_version`` so keys can be rotated.
  ``context`` is bound as associated data (and as the KMS encryption context), so a ciphertext copied into
  another row (another workspace, another purpose) does not decrypt. The ``secrets`` table stores envelopes.
* ``seal`` / ``unseal``: the same envelope with the master key, packed into one blob (TOTP secrets, M3).
* ``keyed_hash``: HMAC-SHA256 with a key derived from ``MASTER_KEY``. Used for short secrets that must be
  compared but never read back (recovery codes, email codes). Unlike a plain hash, a database leak alone does
  not let anyone brute-force them offline.

Blob layout (bytes): ``version (1) | key_version (1) | dek_nonce (12) | wrapped_dek (48) | nonce (12) | ct``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

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


# ------------------------------------------------------------------------------------------- key wrapping
class KeyWrapper(Protocol):
    """Wraps (encrypts) and unwraps data keys. Implementations never see the protected value itself."""

    def wrap(self, dek: bytes, context: str) -> tuple[bytes, int]:
        """Return ``(wrapped_dek, key_version)``."""
        ...

    def unwrap(self, wrapped_dek: bytes, key_version: int, context: str) -> bytes: ...


class MasterKeyWrapper:
    """Wraps data keys with AES-256-GCM under a key derived from ``MASTER_KEY``. ``wrapped = nonce | ct+tag``."""

    def __init__(self, master_key: str) -> None:
        self._master_key = master_key

    def wrap(self, dek: bytes, context: str) -> tuple[bytes, int]:
        nonce = os.urandom(_NONCE)
        wrapped = AESGCM(_kek(self._master_key, KEY_VERSION)).encrypt(
            nonce, dek, context.encode("utf-8")
        )
        return nonce + wrapped, KEY_VERSION

    def unwrap(self, wrapped_dek: bytes, key_version: int, context: str) -> bytes:
        if key_version >= KMS_KEY_VERSION or len(wrapped_dek) != _NONCE + _WRAPPED_DEK:
            raise CryptoError("this data key was not wrapped with the master key")
        try:
            return AESGCM(_kek(self._master_key, key_version)).decrypt(
                wrapped_dek[:_NONCE], wrapped_dek[_NONCE:], context.encode("utf-8")
            )
        except InvalidTag as error:
            raise CryptoError("ciphertext could not be decrypted") from error


# Envelopes wrapped by KMS record this version. KMS keeps its own key versions inside the ciphertext blob.
KMS_KEY_VERSION = 100


class KmsKeyWrapper:
    """Wraps data keys with AWS KMS (``Encrypt``/``Decrypt`` on ``KMS_KEY_ID``); the context is the KMS
    encryption context, so CloudTrail shows which secret was opened. ``client`` is a boto3 ``kms`` client."""

    def __init__(self, client: Any, key_id: str) -> None:
        self._client = client
        self._key_id = key_id

    def wrap(self, dek: bytes, context: str) -> tuple[bytes, int]:
        response = self._client.encrypt(
            KeyId=self._key_id, Plaintext=dek, EncryptionContext={"context": context}
        )
        return bytes(response["CiphertextBlob"]), KMS_KEY_VERSION

    def unwrap(self, wrapped_dek: bytes, key_version: int, context: str) -> bytes:
        if key_version != KMS_KEY_VERSION:
            raise CryptoError("this data key was not wrapped by KMS")
        try:
            response = self._client.decrypt(
                KeyId=self._key_id,
                CiphertextBlob=wrapped_dek,
                EncryptionContext={"context": context},
            )
        except (
            Exception
        ) as error:  # botocore raises service-specific classes; all mean "cannot open"
            raise CryptoError("KMS could not decrypt the data key") from error
        return bytes(response["Plaintext"])


# ------------------------------------------------------------------------------------------------ envelopes
@dataclass(frozen=True)
class Envelope:
    """One encrypted value: ``ciphertext = nonce | AES-256-GCM(value)`` and its wrapped data key."""

    ciphertext: bytes
    wrapped_dek: bytes
    key_version: int


def encrypt_envelope(plaintext: bytes, *, wrapper: KeyWrapper, context: str) -> Envelope:
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(_NONCE)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, context.encode("utf-8"))
    wrapped, key_version = wrapper.wrap(dek, context)
    return Envelope(ciphertext=nonce + ciphertext, wrapped_dek=wrapped, key_version=key_version)


def decrypt_envelope(envelope: Envelope, *, wrapper: KeyWrapper, context: str) -> bytes:
    """The value, or ``CryptoError`` for a wrong key, a wrong context or any tampering (fail closed)."""
    if len(envelope.ciphertext) < _NONCE + 16:
        raise CryptoError("unknown ciphertext format")
    dek = wrapper.unwrap(envelope.wrapped_dek, envelope.key_version, context)
    try:
        return AESGCM(dek).decrypt(
            envelope.ciphertext[:_NONCE], envelope.ciphertext[_NONCE:], context.encode("utf-8")
        )
    except InvalidTag as error:
        raise CryptoError("ciphertext could not be decrypted") from error


# ------------------------------------------------------------------------------------- single-blob form
def seal(plaintext: bytes, *, master_key: str, context: str) -> bytes:
    envelope = encrypt_envelope(plaintext, wrapper=MasterKeyWrapper(master_key), context=context)
    return (
        bytes([_FORMAT_VERSION, envelope.key_version]) + envelope.wrapped_dek + envelope.ciphertext
    )


def unseal(blob: bytes, *, master_key: str, context: str) -> bytes:
    header = 2 + _NONCE + _WRAPPED_DEK + _NONCE
    if len(blob) < header or blob[0] != _FORMAT_VERSION:
        raise CryptoError("unknown ciphertext format")
    wrapped_end = 2 + _NONCE + _WRAPPED_DEK
    envelope = Envelope(
        ciphertext=blob[wrapped_end:], wrapped_dek=blob[2:wrapped_end], key_version=blob[1]
    )
    return decrypt_envelope(envelope, wrapper=MasterKeyWrapper(master_key), context=context)


def keyed_hash(value: str, *, master_key: str, purpose: str) -> str:
    key = _derive(master_key, f"analyst/hmac/{purpose}".encode())
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def signing_key(secret: str, purpose: str) -> bytes:
    """A purpose-specific key derived from any server secret (e.g. ``JWT_SECRET`` for the device cookie)."""
    return _derive(secret, f"analyst/sign/{purpose}".encode())


def hmac_equal(a: str, b: str) -> bool:
    """Constant-time comparison of two hex digests."""
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))
