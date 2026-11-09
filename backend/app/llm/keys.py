"""Which AI key a workspace uses (R7, BUILD_SPEC 7.2 "Key resolution").

* ``plan == managed``: the platform's key from the environment (``GEMINI_API_KEY`` or ``ANTHROPIC_API_KEY`` for
  ``LLM_DEFAULT_PROVIDER``). The monthly question limit is checked **first**, before any provider call.
* ``plan == byok``: the workspace's own key, decrypted from ``secrets``. No platform limit.

The workspace's own key is also set, replaced and removed here (``store_key`` / ``remove_key``). Keys are
checked with the provider before they are stored (a model list call: no tokens used).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.llm.base import LLMClient, LLMFactory, close_client
from app.llm.claude import ClaudeClient
from app.llm.gemini import GeminiClient
from app.llm.models import choose_model
from app.llm.usage import ensure_within_limit
from app.models import LlmKey, Organization
from app.security.secrets import SecretStore, SecretUnavailable

KEY_PURPOSE = "llm_key"


class AiUnavailable(Exception):
    """AI cannot be used right now for a reason the workspace can fix. ``code`` is stable for the API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedLLM:
    client: LLMClient
    provider: str
    key_source: str  # "managed" | "byok"


@dataclass(frozen=True)
class ReplacedKey:
    provider: str
    last4: str


def default_factory(settings: Settings) -> LLMFactory:
    """Real providers, configured from settings."""

    def build(provider: str, api_key: str) -> LLMClient:
        if provider == "claude":
            return ClaudeClient(
                api_key,
                preferred_model=settings.claude_default_model,
                timeout_seconds=settings.llm_request_timeout_seconds,
                budget_seconds=settings.llm_call_budget_seconds,
            )
        return GeminiClient(
            api_key,
            preferred_model=settings.gemini_default_model,
            api_version=settings.gemini_api_version,
            timeout_seconds=settings.llm_request_timeout_seconds,
            budget_seconds=settings.llm_call_budget_seconds,
        )

    return build


def platform_key(settings: Settings) -> str:
    secret = (
        settings.anthropic_api_key
        if settings.llm_default_provider == "claude"
        else settings.gemini_api_key
    )
    return secret.get_secret_value()


def own_key(db: DbSession, org_id: uuid.UUID) -> LlmKey | None:
    return db.scalar(select(LlmKey).where(LlmKey.org_id == org_id))


def resolve(
    db: DbSession,
    org: Organization,
    *,
    settings: Settings,
    factory: LLMFactory,
    secrets: SecretStore,
    now: datetime,
    check_limit: bool = True,
) -> ResolvedLLM:
    if org.plan == "byok":
        row = own_key(db, org.id)
        if row is None:
            raise AiUnavailable("ai_key_missing")
        try:
            api_key = secrets.reveal(
                db, org_id=org.id, secret_id=row.secret_ref, purpose=KEY_PURPOSE
            )
        except SecretUnavailable as error:
            raise AiUnavailable("ai_key_unreadable") from error
        return ResolvedLLM(factory(row.provider, api_key), row.provider, "byok")

    if check_limit:
        ensure_within_limit(db, org, now)  # raises UsageLimitReached
    api_key = platform_key(settings)
    if not api_key:
        raise AiUnavailable("ai_not_configured")
    provider = settings.llm_default_provider
    return ResolvedLLM(factory(provider, api_key), provider, "managed")


def check_key(factory: LLMFactory, provider: str, api_key: str, preferred: str | None) -> str:
    """Ask the provider for the key's models; return the model we would use. Raises ``LLMError``."""
    client = factory(provider, api_key)
    try:
        return choose_model(provider, client.list_models(), preferred)
    finally:
        close_client(client)


def store_key(
    db: DbSession,
    *,
    org_id: uuid.UUID,
    provider: str,
    api_key: str,
    user_id: uuid.UUID,
    secrets: SecretStore,
) -> tuple[LlmKey, ReplacedKey | None]:
    """Save the workspace's key, replacing any earlier one. Returns the new row and what it replaced."""
    old = own_key(db, org_id)
    replaced = None
    if old is not None:
        replaced = ReplacedKey(provider=old.provider, last4=old.last4)
        old_secret = old.secret_ref
        db.delete(old)
        db.flush()
        secrets.delete(db, org_id=org_id, secret_id=old_secret)
    secret = secrets.put(db, org_id=org_id, purpose=KEY_PURPOSE, value=api_key)
    row = LlmKey(
        org_id=org_id,
        provider=provider,
        secret_ref=secret.id,
        last4=api_key[-4:],
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    return row, replaced


def remove_key(db: DbSession, *, org_id: uuid.UUID, secrets: SecretStore) -> LlmKey | None:
    row = own_key(db, org_id)
    if row is None:
        return None
    secret_id = row.secret_ref
    db.delete(row)
    db.flush()
    secrets.delete(db, org_id=org_id, secret_id=secret_id)
    return row
