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


# ----------------------------------------------------------------------------- envelopes and key wrappers (M5)
class FakeKms:
    """Stands in for a boto3 KMS client: 'encrypts' by tagging, and checks the encryption context."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def encrypt(
        self, *, KeyId: str, Plaintext: bytes, EncryptionContext: dict[str, str]
    ) -> dict[str, bytes]:
        self.calls.append("encrypt")
        return {
            "CiphertextBlob": f"{KeyId}|{EncryptionContext['context']}|".encode() + Plaintext[::-1]
        }

    def decrypt(
        self, *, KeyId: str, CiphertextBlob: bytes, EncryptionContext: dict[str, str]
    ) -> dict[str, bytes]:
        self.calls.append("decrypt")
        prefix = f"{KeyId}|{EncryptionContext['context']}|".encode()
        if not CiphertextBlob.startswith(prefix):
            raise RuntimeError("InvalidCiphertextException")
        return {"Plaintext": CiphertextBlob[len(prefix) :][::-1]}


def test_envelope_round_trip_with_the_master_key() -> None:
    wrapper = crypto.MasterKeyWrapper(KEY)
    envelope = crypto.encrypt_envelope(
        b"AIzaSecret", wrapper=wrapper, context="secret:o1:s1:llm_key"
    )
    assert b"AIzaSecret" not in envelope.ciphertext + envelope.wrapped_dek
    assert envelope.key_version == crypto.KEY_VERSION
    assert len(envelope.wrapped_dek) == 12 + 48
    assert (
        crypto.decrypt_envelope(envelope, wrapper=wrapper, context="secret:o1:s1:llm_key")
        == b"AIzaSecret"
    )


def _flip(data: bytes, index: int) -> bytes:
    """``data`` with one bit of one byte flipped: always a change (writing a fixed byte was a no-op whenever the
    random byte already had that value, about 1 run in 256)."""
    changed = bytearray(data)
    changed[index] ^= 0x01
    return bytes(changed)


@pytest.mark.parametrize(
    "change",
    ["context", "ciphertext", "wrapped_dek", "key", "version"],
)
def test_envelopes_fail_closed(change: str) -> None:
    wrapper = crypto.MasterKeyWrapper(KEY)
    envelope = crypto.encrypt_envelope(b"value", wrapper=wrapper, context="ctx-a")
    context = "ctx-a"
    if change == "context":
        context = "ctx-b"
    elif change == "ciphertext":
        envelope = crypto.Envelope(
            _flip(envelope.ciphertext, -1), envelope.wrapped_dek, envelope.key_version
        )
    elif change == "wrapped_dek":
        envelope = crypto.Envelope(
            envelope.ciphertext, _flip(envelope.wrapped_dek, 0), envelope.key_version
        )
    elif change == "key":
        wrapper = crypto.MasterKeyWrapper("another-master-key-00000000000000000000")
    else:
        envelope = crypto.Envelope(
            envelope.ciphertext, envelope.wrapped_dek, crypto.KMS_KEY_VERSION
        )
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_envelope(envelope, wrapper=wrapper, context=context)


def test_kms_wraps_data_keys_with_the_encryption_context() -> None:
    kms = FakeKms()
    wrapper = crypto.KmsKeyWrapper(kms, "arn:aws:kms:ap-south-1:1:key/k")
    envelope = crypto.encrypt_envelope(b"shpat_secret", wrapper=wrapper, context="secret:o1:s1:x")
    assert envelope.key_version == crypto.KMS_KEY_VERSION
    assert (
        crypto.decrypt_envelope(envelope, wrapper=wrapper, context="secret:o1:s1:x")
        == b"shpat_secret"
    )
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_envelope(envelope, wrapper=wrapper, context="secret:o2:s1:x")
    assert kms.calls == ["encrypt", "decrypt", "decrypt"]


def test_a_kms_envelope_does_not_open_with_the_master_key_and_back() -> None:
    kms_env = crypto.encrypt_envelope(
        b"v", wrapper=crypto.KmsKeyWrapper(FakeKms(), "k"), context="c"
    )
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_envelope(kms_env, wrapper=crypto.MasterKeyWrapper(KEY), context="c")
    local_env = crypto.encrypt_envelope(b"v", wrapper=crypto.MasterKeyWrapper(KEY), context="c")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_envelope(
            local_env, wrapper=crypto.KmsKeyWrapper(FakeKms(), "k"), context="c"
        )
