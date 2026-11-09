"""Write-only secret storage (BUILD_SPEC 6.4): envelope-encrypted values in the ``secrets`` table.

Each value is bound to ``(org_id, secret id, purpose)`` through the encryption context, so a row copied into
another workspace, or used for another purpose, does not decrypt. ``reveal`` is only for server-side use (calling
a provider); no API ever returns a secret. Callers pass a tenant-scoped session, so RLS applies as well.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.db.ids import uuid7
from app.models import Secret
from app.security import crypto

logger = logging.getLogger(__name__)


class SecretUnavailable(Exception):
    """The secret is missing or cannot be decrypted (wrong key, tampering, copied row). Fail closed."""


def _context(org_id: uuid.UUID, secret_id: uuid.UUID, purpose: str) -> str:
    return f"secret:{org_id}:{secret_id}:{purpose}"


@lru_cache(maxsize=1)
def _kms_client(region: str) -> Any:  # pragma: no cover - production only
    import boto3

    return boto3.client("kms", region_name=region)


def key_wrapper(settings: Settings) -> crypto.KeyWrapper:
    """AWS KMS when ``KMS_KEY_ID`` is set (production), otherwise the local ``MASTER_KEY``."""
    if settings.kms_key_id:  # pragma: no cover - exercised against real KMS in deployment checks
        return crypto.KmsKeyWrapper(_kms_client(settings.s3_region), settings.kms_key_id)
    return crypto.MasterKeyWrapper(settings.master_key.get_secret_value())


class SecretStore:
    def __init__(self, wrapper: crypto.KeyWrapper) -> None:
        self._wrapper = wrapper

    def put(self, db: DbSession, *, org_id: uuid.UUID, purpose: str, value: str) -> Secret:
        secret_id = uuid7()
        envelope = crypto.encrypt_envelope(
            value.encode("utf-8"),
            wrapper=self._wrapper,
            context=_context(org_id, secret_id, purpose),
        )
        row = Secret(
            id=secret_id,
            org_id=org_id,
            purpose=purpose,
            ciphertext=envelope.ciphertext,
            wrapped_dek=envelope.wrapped_dek,
            key_version=envelope.key_version,
        )
        db.add(row)
        db.flush()
        return row

    def reveal(
        self, db: DbSession, *, org_id: uuid.UUID, secret_id: uuid.UUID, purpose: str
    ) -> str:
        row = db.scalar(select(Secret).where(Secret.id == secret_id, Secret.org_id == org_id))
        if row is None or row.purpose != purpose:
            raise SecretUnavailable("secret not found")
        envelope = crypto.Envelope(
            ciphertext=row.ciphertext, wrapped_dek=row.wrapped_dek, key_version=row.key_version
        )
        try:
            plaintext = crypto.decrypt_envelope(
                envelope, wrapper=self._wrapper, context=_context(org_id, row.id, purpose)
            )
        except crypto.CryptoError as error:
            logger.error("a stored secret could not be decrypted (purpose %s)", purpose)
            raise SecretUnavailable("secret could not be decrypted") from error
        return plaintext.decode("utf-8")

    def delete(self, db: DbSession, *, org_id: uuid.UUID, secret_id: uuid.UUID) -> None:
        row = db.scalar(select(Secret).where(Secret.id == secret_id, Secret.org_id == org_id))
        if row is not None:
            db.delete(row)
            db.flush()
