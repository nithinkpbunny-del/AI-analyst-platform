from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_the_spec() -> None:
    s = Settings(_env_file=None)
    assert s.access_token_minutes == 10
    assert s.session_idle_minutes == 30
    assert s.session_max_hours == 12
    assert s.cache_ttl_seconds == 300
    assert s.snapshot_max_age_hours == 24
    assert s.managed_monthly_question_limit == 500
    # Safety limits, BUILD_SPEC 6.5
    assert s.agent_max_steps == 12
    assert s.agent_max_steps_long_task == 40
    assert s.llm_max_output_tokens == 3000
    assert s.token_budget_per_question == 150_000
    assert s.rows_to_llm_per_tool_call == 200
    assert s.source_query_timeout_seconds == 30
    assert s.source_query_timeout_long_task_seconds == 120
    assert s.max_rows_per_dataset == 1_000_000
    assert s.questions_per_user_per_minute == 10
    assert s.concurrent_long_tasks_per_org == 2
    assert s.upload_max_mb == 50
    assert s.gemini_api_version == "v1beta"
    assert s.llm_default_provider == "gemini"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("REDIS_URL", "redis://example:6380/2")
    s = Settings(_env_file=None)
    assert s.cache_ttl_seconds == 60
    assert s.redis_url == "redis://example:6380/2"


def test_unknown_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "something")
    Settings(_env_file=None)  # must not raise


def test_secrets_never_appear_in_repr() -> None:
    s = Settings(_env_file=None, jwt_secret="super-secret-value-123456")
    assert "super-secret-value-123456" not in repr(s)
    assert "super-secret-value-123456" not in str(s)


def test_secret_values_lists_only_non_empty_secrets() -> None:
    s = Settings(
        _env_file=None,
        jwt_secret="jwt-secret-value",
        gemini_api_key="gemini-key-value",
    )
    assert set(s.secret_values()) == {"jwt-secret-value", "gemini-key-value"}


def test_production_requires_strong_keys() -> None:
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        Settings(_env_file=None, app_env="production", jwt_secret="x" * 40)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, app_env="production", master_key="x" * 40)


def test_production_accepts_strong_keys() -> None:
    s = Settings(
        _env_file=None,
        app_env="production",
        master_key="m" * 40,
        jwt_secret="j" * 40,
    )
    assert s.app_env == "production"


def test_invalid_numbers_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_MINUTES", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_auth_defaults_match_the_spec() -> None:
    s = Settings(_env_file=None)
    assert (s.login_max_failures, s.login_window_minutes) == (5, 15)
    assert s.email_verification_hours == 24
    assert s.password_reset_minutes == 60


@pytest.mark.parametrize(
    ("api_base_url", "cookie_secure", "expected"),
    [
        ("http://localhost:8000", "auto", False),
        ("http://127.0.0.1:8000", "auto", False),
        ("https://api.analyst.in", "auto", True),
        ("http://localhost:8000", "true", True),
        ("https://api.analyst.in", "false", False),
    ],
)
def test_cookie_secure_flag(api_base_url: str, cookie_secure: str, expected: bool) -> None:
    s = Settings(_env_file=None, api_base_url=api_base_url, cookie_secure=cookie_secure)
    assert s.cookies_secure is expected
