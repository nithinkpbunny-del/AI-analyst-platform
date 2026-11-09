"""Application settings, read from environment variables (BUILD_SPEC sections 6.5 and 16).

Every setting has a documented entry in ``.env.example``. Secrets are ``SecretStr`` so they never appear in
``repr()`` or accidental log lines.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> repository root is two levels above ``backend``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """All configuration. Field ``foo_bar`` is read from env var ``FOO_BAR`` (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"
    site_base_url: str = "http://localhost:4321"
    log_level: str = "INFO"

    # --- Services ---
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/analyst"
    redis_url: str = "redis://localhost:6379/0"

    # --- Object storage (S3-compatible) ---
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "analyst-dev"
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "ap-south-1"

    # --- Security ---
    master_key: SecretStr = SecretStr("")
    kms_key_id: str = ""
    jwt_secret: SecretStr = SecretStr("")
    access_token_minutes: int = Field(default=10, ge=1)
    session_idle_minutes: int = Field(default=30, ge=1)
    session_max_hours: int = Field(default=12, ge=1)
    # Auth details (BUILD_SPEC 6.1). Defaults follow the spec; change only with a reason.
    login_max_failures: int = Field(default=5, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    email_verification_hours: int = Field(default=24, ge=1)
    password_reset_minutes: int = Field(default=60, ge=5)
    # Refresh/CSRF cookies get the "Secure" flag. "auto": on unless API_BASE_URL is plain http://localhost.
    cookie_secure: Literal["auto", "true", "false"] = "auto"
    # Version of the privacy policy + terms a new user accepts at sign-up (stored in ``consents``).
    policy_version: str = "2026-11-09"

    # --- Email ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_from: str = "Analyst Agent <no-reply@localhost>"

    # --- Google (filled in when the Google connector milestone needs it) ---
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")
    google_picker_api_key: SecretStr = SecretStr("")

    # --- AI ---
    llm_default_provider: Literal["gemini", "claude"] = "gemini"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_api_version: str = "v1beta"
    gemini_default_model: str = ""
    anthropic_api_key: SecretStr = SecretStr("")

    # --- Connectors (later milestones) ---
    shopify_api_key: str = ""
    shopify_api_secret: SecretStr = SecretStr("")
    zoho_client_id: str = ""
    zoho_client_secret: SecretStr = SecretStr("")

    # --- Cache and retention ---
    cache_ttl_seconds: int = Field(default=300, ge=0)
    snapshot_max_age_hours: int = Field(default=24, ge=1)

    # --- Safety limits (section 6.5) ---
    managed_monthly_question_limit: int = Field(default=500, ge=0)
    agent_max_steps: int = Field(default=12, ge=1)
    agent_max_steps_long_task: int = Field(default=40, ge=1)
    llm_max_output_tokens: int = Field(default=3000, ge=1)
    token_budget_per_question: int = Field(default=150_000, ge=1)
    rows_to_llm_per_tool_call: int = Field(default=200, ge=1)
    source_query_timeout_seconds: int = Field(default=30, ge=1)
    source_query_timeout_long_task_seconds: int = Field(default=120, ge=1)
    max_rows_per_dataset: int = Field(default=1_000_000, ge=1)
    questions_per_user_per_minute: int = Field(default=10, ge=1)
    concurrent_long_tasks_per_org: int = Field(default=2, ge=1)
    upload_max_mb: int = Field(default=50, ge=1)

    # --- Health checks ---
    health_check_timeout_seconds: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _require_strong_secrets_outside_development(self) -> Settings:
        """Production and test-like environments must not start with missing or weak keys."""
        if self.app_env == "production":
            for name in ("master_key", "jwt_secret"):
                value: SecretStr = getattr(self, name)
                if len(value.get_secret_value()) < _MIN_SECRET_LENGTH:
                    raise ValueError(
                        f"{name.upper()} must be set to a random value of at least "
                        f"{_MIN_SECRET_LENGTH} characters when APP_ENV=production"
                    )
        return self

    @property
    def cookies_secure(self) -> bool:
        if self.cookie_secure != "auto":
            return self.cookie_secure == "true"
        return not self.api_base_url.startswith(("http://localhost", "http://127.0.0.1"))

    def secret_values(self) -> list[str]:
        """Every non-empty secret value, used by the log redactor to scrub exact matches."""
        values = [
            self.s3_secret_key,
            self.master_key,
            self.jwt_secret,
            self.smtp_password,
            self.google_oauth_client_secret,
            self.google_picker_api_key,
            self.gemini_api_key,
            self.anthropic_api_key,
            self.shopify_api_secret,
            self.zoho_client_secret,
        ]
        return [v.get_secret_value() for v in values if v.get_secret_value()]


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings. Tests build their own ``Settings(_env_file=None, ...)`` instead."""
    return Settings()
