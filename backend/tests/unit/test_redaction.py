from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.logging_config import configure_logging, request_id_var
from app.security.redaction import REDACTED, REDACTED_EMAIL, Redactor

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwib3JnIjoiYWJjIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        (f"token was {JWT} today", JWT),
        ("Authorization: Bearer abcdefghijklmnop1234", "abcdefghijklmnop1234"),
        (
            "key AIzaSyA1234567890abcdefghijklmnopqrstuvw end",
            "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
        ),
        ("key sk-ant-api03-abcdefghijklmnopqrstuv end", "sk-ant-api03-abcdefghijklmnopqrstuv"),
        ("razorpay rzp_live_AbCdEfGh12345678", "rzp_live_AbCdEfGh12345678"),
        ("connect postgresql://app:hunter2hunter2@db:5432/x failed", "hunter2hunter2"),
        ("password=correct-horse-battery", "correct-horse-battery"),
        ('{"api_key": "abc123def456"}', "abc123def456"),
        ("MASTER_KEY=Zm9vYmFyYmF6", "Zm9vYmFyYmF6"),
        ("failed for owner@urbancart.in", "owner@urbancart.in"),
    ],
)
def test_patterns_are_redacted(raw: str, must_not_contain: str) -> None:
    assert must_not_contain not in Redactor().redact(raw)


def test_exact_secret_values_are_redacted_even_without_a_pattern() -> None:
    redactor = Redactor(["plain-looking-secret-9z"])
    assert redactor.redact("value is plain-looking-secret-9z!") == f"value is {REDACTED}!"


def test_longest_secret_wins_when_one_contains_another() -> None:
    redactor = Redactor(["abcdef", "abcdef-extended-part"])
    assert redactor.redact("x abcdef-extended-part y") == f"x {REDACTED} y"


def test_very_short_secrets_are_not_used_for_exact_matching() -> None:
    assert Redactor(["1"]).redact("order 1 of 10") == "order 1 of 10"


def test_ordinary_text_is_untouched() -> None:
    text = "GET /health (3.2 ms) status=200 rows=1500 org=Urban Cart"
    assert Redactor().redact(text) == text


def test_email_marker() -> None:
    assert Redactor().redact("to a.b+c@example.co.in now") == f"to {REDACTED_EMAIL} now"


def test_logging_pipeline_redacts_messages_arguments_and_tracebacks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(_env_file=None, app_env="test", gemini_api_key="gemini-secret-value-xyz")
    configure_logging(settings)
    logger = logging.getLogger("tests.redaction")

    logger.warning("calling with %s", "gemini-secret-value-xyz")
    try:
        raise RuntimeError("could not reach postgresql://app:dbpass12345@db/x for ceo@example.com")
    except RuntimeError:
        logger.exception("boom")

    out = capsys.readouterr().out
    assert "gemini-secret-value-xyz" not in out
    assert "dbpass12345" not in out
    assert "ceo@example.com" not in out
    assert "calling with" in out and "boom" in out  # logging itself still works


def test_json_logs_in_production_include_request_id(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        master_key="m" * 40,
        jwt_secret="j" * 40,
    )
    configure_logging(settings)
    token = request_id_var.set("req-123")
    try:
        logging.getLogger("tests.json").info("hello")
    finally:
        request_id_var.reset(token)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out.startswith("{") and '"request_id": "req-123"' in out and '"message": "hello"' in out
