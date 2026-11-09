"""Device cookie signing and the browser description in "New sign-in" emails."""

from __future__ import annotations

import pytest

from app.auth import devices

SECRET = "test-jwt-secret-0000000000000000000000"


def test_signed_device_id_round_trip_and_forgery() -> None:
    device_id = devices.new_device_id()
    cookie = devices.sign_device_id(device_id, SECRET)
    assert devices.verified_device_id(cookie, SECRET) == device_id
    assert devices.verified_device_id(cookie, SECRET + "x") is None
    assert devices.verified_device_id(f"other{cookie}", SECRET) is None
    assert devices.verified_device_id(device_id, SECRET) is None
    assert devices.verified_device_id(None, SECRET) is None
    assert devices.verified_device_id("", SECRET) is None
    assert devices.device_hash(device_id) != device_id and len(devices.device_hash(device_id)) == 64


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36",
            "Chrome on Windows",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.1 Safari/605.1.15",
            "Safari on macOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 "
            "Mobile Safari/537.36 EdgA/131.0",
            "Edge on Android",
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/131.0 Mobile/15E148 Safari/604.1",
            "Chrome on iPhone",
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
            "Firefox on Linux",
        ),
        ("curl/8.5.0", "Unknown browser"),
        (None, "Unknown browser"),
    ],
)
def test_describe_user_agent(agent: str | None, expected: str) -> None:
    assert devices.describe_user_agent(agent) == expected


def test_mask_email() -> None:
    assert devices.mask_email("asha@urbancart.in") == "as***@urbancart.in"
    assert devices.mask_email("ab@x.in") == "a***@x.in"
