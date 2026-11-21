"""Log redaction (BUILD_SPEC 6.4): never log secrets, tokens, full API keys or emails in error payloads.

Two layers:
1. exact-match scrubbing of every secret value loaded from configuration;
2. pattern scrubbing for secrets we do not know in advance (JWTs, bearer tokens, vendor API keys,
   passwords in URLs, ``password=...`` style pairs, email addresses).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"
REDACTED_EMAIL = "[REDACTED-EMAIL]"

_MIN_EXACT_LENGTH = 6  # avoid scrubbing trivially short values such as "1" from every message

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JSON web tokens: three base64url segments, the first two starting with "eyJ".
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    # Authorization headers (Zoho's own scheme too: "Zoho-oauthtoken <token>", M19).
    (
        re.compile(r"(?i)\b(bearer|basic|zoho-oauthtoken)\s+[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 " + REDACTED,
    ),
    # Zoho access and refresh tokens: "1000." and two long hexadecimal parts.
    (re.compile(r"\b1000\.[0-9a-f]{16,}\.[0-9a-f]{16,}\b"), REDACTED),
    # Vendor keys: Google (AIza...), Anthropic/OpenAI style (sk-...), Razorpay (rzp_live_/rzp_test_),
    # Shopify (shpat_/shpss_), AWS access key ids.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), REDACTED),
    # Google's newer API key format: "AQ." followed by a long token.
    (re.compile(r"\bAQ\.[0-9A-Za-z_.-]{20,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\brzp_(?:live|test)_[A-Za-z0-9]{8,}\b"), REDACTED),
    (re.compile(r"\bshp(?:at|ss|ca|pa)_[A-Za-z0-9]{16,}\b"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    # Google OAuth: access tokens (ya29.), refresh tokens (1//) and authorization codes (4/).
    (re.compile(r"\bya29\.[0-9A-Za-z_.-]{10,}"), REDACTED),
    (re.compile(r"\b1//[0-9A-Za-z_-]{20,}"), REDACTED),
    (re.compile(r"\b4/[0-9A-Za-z_-]{20,}"), REDACTED),
    # Credentials embedded in URLs: scheme://user:password@host
    (
        re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^/\s:@]+):(?P<pw>[^/\s@]+)@"),
        r"\g<scheme>\g<user>:" + REDACTED + "@",
    ),
    # key=value / "key": "value" for sensitive key names.
    (
        re.compile(
            r"""(?ix)
            (?P<key>\b[\w-]*(?:password|passwd|secret|token|api[_-]?key|apikey|authorization|master[_-]?key)[\w-]*)
            (?P<sep>["']?\s*[:=]\s*["']?)
            (?P<val>[^\s"',;&]+)
            """
        ),
        r"\g<key>\g<sep>" + REDACTED,
    ),
    # Email addresses.
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED_EMAIL),
]


class Redactor:
    """Scrub known secret values and secret-looking patterns from text."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first, so a secret that contains another is replaced whole.
        self._secrets = sorted(
            {s for s in secrets if len(s) >= _MIN_EXACT_LENGTH}, key=len, reverse=True
        )

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text
