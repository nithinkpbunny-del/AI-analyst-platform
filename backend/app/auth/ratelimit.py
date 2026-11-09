"""Rate limits kept in Redis (BUILD_SPEC 6.1 and 6.5).

``LoginThrottle``: at most ``LOGIN_MAX_FAILURES`` failed sign-ins per (email, IP) within
``LOGIN_WINDOW_MINUTES``. Reaching the limit locks that pair; each further lockout within 24 hours doubles in
length (15 min, 30 min, 1 h ... capped at 24 h). A successful sign-in clears the record.

``FixedWindowLimiter``: a simple counter per key and window, used to stop email flooding (sign-up, resend,
forgot-password).

Times come from the injected clock, so tests can move time. Redis TTLs only clean up old keys.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import redis

_STRIKE_MEMORY = timedelta(hours=24)
_MAX_LOCK = timedelta(hours=24)


def _key(prefix: str, *parts: str) -> str:
    # Hash the parts: keys should not contain email addresses in clear text.
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:40]
    return f"rl:{prefix}:{digest}"


@dataclass(frozen=True)
class Lockout:
    retry_after_seconds: int


class LoginThrottle:
    def __init__(
        self,
        client: redis.Redis[bytes],
        *,
        max_failures: int,
        window: timedelta,
        prefix: str = "login",
    ) -> None:
        self._redis = client
        self._max_failures = max_failures
        self._window = window
        self._prefix = prefix

    def _keys(self, email: str, ip: str) -> tuple[str, str, str]:
        base = _key(self._prefix, email.strip().lower(), ip)
        return f"{base}:fails", f"{base}:lock", f"{base}:strikes"

    def check(self, email: str, ip: str, now: datetime) -> Lockout | None:
        """Return the lockout if this (email, IP) is currently locked."""
        _, lock_key, _ = self._keys(email, ip)
        raw = self._redis.get(lock_key)
        if raw is None:
            return None
        until = float(raw)
        remaining = until - now.timestamp()
        if remaining <= 0:
            return None
        return Lockout(retry_after_seconds=max(1, math.ceil(remaining)))

    def record_failure(self, email: str, ip: str, now: datetime) -> Lockout | None:
        """Count a failure. Returns the new lockout when this failure reaches the limit."""
        fails_key, lock_key, strikes_key = self._keys(email, ip)
        ts = now.timestamp()
        window_start = ts - self._window.total_seconds()
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(fails_key, "-inf", window_start)
        pipe.zadd(fails_key, {f"{ts:.6f}:{secrets.token_hex(4)}": ts})  # unique even at equal times
        pipe.zcard(fails_key)
        pipe.expire(fails_key, int(self._window.total_seconds()) + 60)
        count = int(pipe.execute()[2])
        if count < self._max_failures:
            return None

        strikes = int(self._redis.incr(strikes_key))
        self._redis.expire(strikes_key, int(_STRIKE_MEMORY.total_seconds()))
        duration = min(self._window * (2 ** (strikes - 1)), _MAX_LOCK)
        until = ts + duration.total_seconds()
        pipe = self._redis.pipeline()
        pipe.set(lock_key, f"{until:.3f}", ex=int(duration.total_seconds()) + 60)
        pipe.delete(fails_key)
        pipe.execute()
        return Lockout(retry_after_seconds=max(1, math.ceil(duration.total_seconds())))

    def record_success(self, email: str, ip: str) -> None:
        self._redis.delete(*self._keys(email, ip))


class FixedWindowLimiter:
    def __init__(
        self, client: redis.Redis[bytes], *, prefix: str, limit: int, window: timedelta
    ) -> None:
        self._redis = client
        self._prefix = prefix
        self._limit = limit
        self._window = window

    def hit(self, key: str, now: datetime) -> bool:
        """Count one use. Returns ``False`` when the limit for the current window is exceeded."""
        seconds = int(self._window.total_seconds())
        bucket = int(now.timestamp()) // seconds
        redis_key = f"{_key(self._prefix, key)}:{bucket}"
        pipe = self._redis.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, seconds + 60)
        count = int(pipe.execute()[0])
        return count <= self._limit
