"""Key resolution (BUILD_SPEC 7.2): Managed uses the platform key after checking the limit; BYOK decrypts the
workspace's own key."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.llm import keys
from app.llm.base import Usage
from app.llm.fake import FakeLLMFactory
from app.llm.usage import UsageLimitReached, record
from app.models import LlmKey, Organization, Secret, User
from app.security.secrets import SecretStore, key_wrapper
from tests.tenancy import make_org

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
OWN_KEY = "sk-ant-own-key-for-tests-000000000000"


@pytest.fixture
def org(db: DbSession) -> Organization:
    return make_org(db, "UrbanCart", "asha@urbancart.in")


@pytest.fixture
def store(settings: Settings) -> SecretStore:
    return SecretStore(key_wrapper(settings))


def _resolve(
    db: DbSession,
    org: Organization,
    settings: Settings,
    factory: FakeLLMFactory,
    store: SecretStore,
) -> keys.ResolvedLLM:
    return keys.resolve(db, org, settings=settings, factory=factory, secrets=store, now=NOW)


def test_managed_uses_the_platform_key(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    factory = FakeLLMFactory()
    resolved = _resolve(db, org, settings, factory, store)
    assert (resolved.provider, resolved.key_source) == ("gemini", "managed")
    assert factory.created == [("gemini", "test-gemini-key-not-real")]


def test_managed_with_claude_as_the_default_provider(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    claude_settings = settings.model_copy(
        update={"llm_default_provider": "claude", "anthropic_api_key": settings.gemini_api_key}
    )
    factory = FakeLLMFactory()
    assert _resolve(db, org, claude_settings, factory, store).provider == "claude"
    assert factory.created[0][0] == "claude"


def test_the_limit_is_checked_before_any_provider_is_contacted(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    org.monthly_question_limit = 1
    record(
        db,
        org_id=org.id,
        user_id=None,
        run_id=uuid.uuid4(),
        kind="question",
        provider="gemini",
        model="gemini-2.5-flash",
        usage=Usage(1, 1),
        key_source="managed",
        at=NOW,
    )
    db.commit()
    factory = FakeLLMFactory()
    with pytest.raises(UsageLimitReached):
        _resolve(db, org, settings, factory, store)
    assert factory.created == []
    # A connection check skips the limit.
    keys.resolve(
        db, org, settings=settings, factory=factory, secrets=store, now=NOW, check_limit=False
    )


def test_managed_without_a_platform_key(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    from pydantic import SecretStr

    empty = settings.model_copy(update={"gemini_api_key": SecretStr("")})
    with pytest.raises(keys.AiUnavailable) as caught:
        _resolve(db, org, empty, FakeLLMFactory(), store)
    assert caught.value.code == "ai_not_configured"


def test_byok_decrypts_the_workspaces_own_key(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    user = db.scalars(select(User)).one()
    keys.store_key(
        db, org_id=org.id, provider="claude", api_key=OWN_KEY, user_id=user.id, secrets=store
    )
    org.plan = "byok"
    org.monthly_question_limit = 0  # no platform limit applies
    db.commit()
    factory = FakeLLMFactory()
    resolved = _resolve(db, org, settings, factory, store)
    assert (resolved.provider, resolved.key_source) == ("claude", "byok")
    assert factory.created == [("claude", OWN_KEY)]


def test_byok_without_a_key_or_with_an_unreadable_one(
    db: DbSession, org: Organization, settings: Settings, store: SecretStore
) -> None:
    org.plan = "byok"
    db.commit()
    with pytest.raises(keys.AiUnavailable) as caught:
        _resolve(db, org, settings, FakeLLMFactory(), store)
    assert caught.value.code == "ai_key_missing"

    user = db.scalars(select(User)).one()
    row, _ = keys.store_key(
        db, org_id=org.id, provider="gemini", api_key=OWN_KEY, user_id=user.id, secrets=store
    )
    secret = db.get(Secret, row.secret_ref)
    assert secret is not None
    secret.ciphertext = secret.ciphertext[:-1] + bytes([secret.ciphertext[-1] ^ 1])  # tampered
    db.commit()
    with pytest.raises(keys.AiUnavailable) as caught:
        _resolve(db, org, settings, FakeLLMFactory(), store)
    assert caught.value.code == "ai_key_unreadable"


def test_replacing_and_removing_a_key_removes_the_old_secret(
    db: DbSession, org: Organization, store: SecretStore
) -> None:
    user = db.scalars(select(User)).one()
    keys.store_key(
        db, org_id=org.id, provider="gemini", api_key=OWN_KEY, user_id=user.id, secrets=store
    )
    new, replaced = keys.store_key(
        db,
        org_id=org.id,
        provider="claude",
        api_key="sk-ant-second-key-000000000000000",
        user_id=user.id,
        secrets=store,
    )
    assert replaced == keys.ReplacedKey(provider="gemini", last4=OWN_KEY[-4:])
    assert new.last4 == "0000"
    assert len(db.scalars(select(Secret)).all()) == 1
    removed = keys.remove_key(db, org_id=org.id, secrets=store)
    assert removed is not None and removed.provider == "claude"
    assert keys.remove_key(db, org_id=org.id, secrets=store) is None
    db.commit()
    assert db.scalars(select(Secret)).all() == []
    assert db.scalars(select(LlmKey)).all() == []


def test_checking_a_key_lists_models_without_using_tokens() -> None:
    factory = FakeLLMFactory(rejected_keys=["bad-key"])
    assert keys.check_key(factory, "gemini", "good-key", "gemini-2.5-flash") == "gemini-2.5-flash"
    assert factory.clients[0].calls == []  # no generate call
    from app.llm.base import LLMError

    with pytest.raises(LLMError):
        keys.check_key(factory, "gemini", "bad-key", None)
