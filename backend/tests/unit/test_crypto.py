"""Envelope encryption and keyed hashes (BUILD_SPEC 6.4)."""

from __future__ import annotations

import pytest

from app.security import crypto

KEY = "test-master-key-0000000000000000000000"


def test_round_trip_and_fresh_randomness() -> None:
    a = crypto.seal(b"JBSWY3DPEHPK3PXP", master_key=KEY, context="two_factor.totp:u1")
    b = crypto.seal(b"JBSWY3DPEHPK3PXP", master_key=KEY, context="two_factor.totp:u1")
    assert a != b  # new data key and nonces every time
    assert b"JBSWY3DPEHPK3PXP" not in a
    assert a[:2] == bytes([1, crypto.KEY_VERSION])
    assert crypto.unseal(a, master_key=KEY, context="two_factor.totp:u1") == b"JBSWY3DPEHPK3PXP"


@pytest.mark.parametrize(
    ("key", "context"),
    [
        ("another-master-key-00000000000000000000", "two_factor.totp:u1"),  # wrong key
        (KEY, "two_factor.totp:u2"),  # copied into another person's row
    ],
)
def test_wrong_key_or_context_fails(key: str, context: str) -> None:
    blob = crypto.seal(b"secret", master_key=KEY, context="two_factor.totp:u1")
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(blob, master_key=key, context=context)


def test_tampering_is_detected() -> None:
    blob = bytearray(crypto.seal(b"secret", master_key=KEY, context="c"))
    blob[-1] ^= 0x01
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(bytes(blob), master_key=KEY, context="c")
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(b"\x09short", master_key=KEY, context="c")


def test_missing_master_key_is_a_clear_error() -> None:
    with pytest.raises(crypto.MissingMasterKey, match="openssl rand"):
        crypto.seal(b"x", master_key="", context="c")


def test_keyed_hash_depends_on_key_and_purpose() -> None:
    first = crypto.keyed_hash("abc", master_key=KEY, purpose="recovery-code")
    assert first == crypto.keyed_hash("abc", master_key=KEY, purpose="recovery-code")
    assert first != crypto.keyed_hash("abc", master_key=KEY, purpose="email-code")
    assert first != crypto.keyed_hash("abc", master_key=KEY + "x", purpose="recovery-code")
    assert crypto.hmac_equal(first, first) and not crypto.hmac_equal(first, "0" * 64)
