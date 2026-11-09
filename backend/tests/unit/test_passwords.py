"""Password hashing and policy (BUILD_SPEC 6.1)."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher

from app.auth import passwords


def test_hash_is_argon2id_and_verifies() -> None:
    hashed = passwords.hash_password("ledger-haldi-neel-42")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password(hashed, "ledger-haldi-neel-42")
    assert not passwords.verify_password(hashed, "ledger-haldi-neel-43")


def test_verify_handles_garbage_hashes() -> None:
    assert not passwords.verify_password("not-a-hash", "anything")


def test_production_parameters_trigger_a_rehash_of_cheap_hashes() -> None:
    cheap = passwords.hash_password("ledger-haldi-neel-42")  # tests use cheap parameters
    assert not passwords.needs_rehash(cheap)
    assert PasswordHasher().check_needs_rehash(cheap)


def test_default_production_hasher_uses_argon2id_defaults() -> None:
    hasher = PasswordHasher()
    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (3, 65536, 4)


def test_burn_verification_time_runs() -> None:
    passwords.burn_verification_time("whatever")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("123456789", "at least 10"),
        ("x" * 129, "at most 128"),
        ("qwertyuiop", "too common"),
        ("Password123", "too common"),  # the check ignores case
        ("asha@urbancart.in", "email"),
        ("ashaurbancart", None),
        ("abcabcabcabc", None),
        ("zzzzzzzzzzz1", "repetitive"),
        ("ababababab", "repetitive"),
    ],
)
def test_policy(password: str, fragment: str | None) -> None:
    problem = passwords.password_problem(password, email="asha@urbancart.in")
    if fragment is None:
        assert problem is None
    else:
        assert problem is not None and fragment in problem


def test_local_part_of_the_email_is_not_allowed() -> None:
    assert passwords.password_problem("ashaurbancart", email="ashaurbancart@x.in") is not None


def test_common_list_is_bundled_and_large() -> None:
    common = passwords._common_passwords()
    assert len(common) > 90_000
    assert "password" in common


def test_long_passphrases_without_composition_rules_are_fine() -> None:
    assert passwords.password_problem("monsoon chai at six") is None
