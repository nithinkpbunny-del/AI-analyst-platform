"""TOTP, email codes and recovery codes (BUILD_SPEC 6.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp

from app.auth import codes

NOW = datetime(2026, 11, 9, 9, 0, 15, tzinfo=UTC)


def test_totp_accepts_plus_minus_one_step_and_reports_it() -> None:
    secret = codes.new_totp_secret()
    totp = pyotp.TOTP(secret)
    current = totp.timecode(NOW)
    for offset in (-1, 0, 1):
        code = totp.generate_otp(current + offset)
        assert codes.totp_step(secret, code, NOW, None) == current + offset
    too_old = totp.generate_otp(current - 2)
    assert codes.totp_step(secret, too_old, NOW, None) is None


def test_totp_code_cannot_be_replayed() -> None:
    secret = codes.new_totp_secret()
    code = codes.totp_code_at(secret, NOW)
    step = codes.totp_step(secret, code, NOW, None)
    assert step is not None
    assert codes.totp_step(secret, code, NOW, step) is None  # same step again
    later = NOW + timedelta(seconds=30)
    assert codes.totp_step(secret, codes.totp_code_at(secret, later), later, step) == step + 1


def test_provisioning_uri_and_qr_matrix() -> None:
    secret = codes.new_totp_secret()
    provisioning = codes.totp_provisioning(secret, "asha@urbancart.in")
    assert provisioning.uri.startswith("otpauth://totp/Analyst:asha%40urbancart.in?")
    assert f"secret={secret}" in provisioning.uri and "issuer=Analyst" in provisioning.uri
    size = len(provisioning.qr)
    assert size >= 21 and all(
        len(row) == size and set(row) <= {"0", "1"} for row in provisioning.qr
    )
    assert len(secret) == 32  # 160 bits


def test_email_codes_are_six_digits() -> None:
    samples = {codes.new_email_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in samples)
    assert len(samples) > 150


def test_recovery_codes_format_and_uniqueness() -> None:
    fresh = codes.new_recovery_codes()
    assert len(fresh) == 10 and len(set(fresh)) == 10
    for code in fresh:
        assert len(code) == 11 and code[5] == "-"
        assert not set(code.replace("-", "")) & set("ilo01")


def test_classify_normalises_what_people_type() -> None:
    assert codes.classify(" 123 456 ") == ("otp", "123456")
    assert codes.classify("ABCDE-FGHJK") == ("recovery", "abcdefghjk")
    assert codes.classify("abcdefghjk") == ("recovery", "abcdefghjk")
    assert codes.classify("12345") is None
    assert codes.classify("abcde-fghi1") is None  # "1" is not in the alphabet
    assert codes.classify("") is None
