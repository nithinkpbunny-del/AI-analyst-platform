"""Device recognition for "New sign-in" emails (BUILD_SPEC 6.1, R15).

Each browser gets a random device id in the ``analyst_device`` cookie, signed with a key derived from
``JWT_SECRET`` so it cannot be forged or swapped for another person's. The database only keeps a SHA-256 of
the id. The browser description in emails comes from a small user-agent parser: it only needs to say
"Chrome on Windows", not fingerprint anyone.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from app.security.crypto import signing_key

DEVICE_COOKIE = "analyst_device"
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 3600  # the longest lifetime browsers allow


def new_device_id() -> str:
    return secrets.token_urlsafe(24)


def _signature(device_id: str, secret: str) -> str:
    key = signing_key(secret, "device-cookie")
    return hmac.new(key, device_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def sign_device_id(device_id: str, secret: str) -> str:
    return f"{device_id}.{_signature(device_id, secret)}"


def verified_device_id(cookie: str | None, secret: str) -> str | None:
    """The device id from a cookie value, or ``None`` if it is missing or was not signed by us."""
    if not cookie or "." not in cookie:
        return None
    device_id, _, signature = cookie.rpartition(".")
    if not device_id or len(device_id) > 64:
        return None
    if not hmac.compare_digest(signature, _signature(device_id, secret)):
        return None
    return device_id


def device_hash(device_id: str) -> str:
    return hashlib.sha256(device_id.encode("utf-8")).hexdigest()


_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/")),
    ("Safari", re.compile(r"Safari/")),
)
_SYSTEMS = (
    ("Android", re.compile(r"Android")),
    ("iPhone", re.compile(r"iPhone")),
    ("iPad", re.compile(r"iPad")),
    ("Windows", re.compile(r"Windows")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("Linux", re.compile(r"Linux")),
)


def describe_user_agent(user_agent: str | None) -> str:
    """A short, human description such as "Chrome on Windows"."""
    if not user_agent:
        return "Unknown browser"
    browser = next((name for name, pattern in _BROWSERS if pattern.search(user_agent)), None)
    system = next((name for name, pattern in _SYSTEMS if pattern.search(user_agent)), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown browser"


def mask_email(email: str) -> str:
    """``asha@urbancart.in`` -> ``as***@urbancart.in``: enough to recognise, not enough to harvest."""
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"
