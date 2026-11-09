"""Redis rate limits, on fakeredis with a moving clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis

from app.auth.ratelimit import FixedWindowLimiter, LoginThrottle

T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _throttle() -> tuple[LoginThrottle, fakeredis.FakeRedis]:
    client = fakeredis.FakeRedis()
    return LoginThrottle(client, max_failures=5, window=timedelta(minutes=15)), client


def test_locks_on_the_fifth_failure() -> None:
    throttle, _ = _throttle()
    for i in range(4):
        assert throttle.record_failure("a@x.in", "1.1.1.1", T0 + timedelta(seconds=i)) is None
    lock = throttle.record_failure("a@x.in", "1.1.1.1", T0 + timedelta(seconds=5))
    assert lock is not None and lock.retry_after_seconds == 900
    check = throttle.check("a@x.in", "1.1.1.1", T0 + timedelta(minutes=5, seconds=5))
    assert check is not None and check.retry_after_seconds == 600
    assert throttle.check("a@x.in", "1.1.1.1", T0 + timedelta(minutes=15, seconds=6)) is None


def test_scoped_by_email_and_ip_and_case_insensitive() -> None:
    throttle, _ = _throttle()
    for _ in range(5):
        throttle.record_failure("A@X.in", "1.1.1.1", T0)
    assert throttle.check("a@x.in", "1.1.1.1", T0) is not None
    assert throttle.check("a@x.in", "2.2.2.2", T0) is None
    assert throttle.check("b@x.in", "1.1.1.1", T0) is None


def test_backoff_doubles_and_is_capped() -> None:
    throttle, _ = _throttle()
    now = T0
    durations = []
    for _ in range(9):
        for _ in range(5):
            lock = throttle.record_failure("a@x.in", "ip", now)
        assert lock is not None
        durations.append(lock.retry_after_seconds)
        now += timedelta(seconds=lock.retry_after_seconds + 1)
    assert durations[:4] == [900, 1800, 3600, 7200]
    assert max(durations) == 24 * 3600


def test_success_resets_everything() -> None:
    throttle, client = _throttle()
    for _ in range(5):
        throttle.record_failure("a@x.in", "ip", T0)
    throttle.record_success("a@x.in", "ip")
    assert throttle.check("a@x.in", "ip", T0) is None
    assert client.keys("*") == []


def test_keys_do_not_contain_the_email() -> None:
    throttle, client = _throttle()
    throttle.record_failure("secret.person@x.in", "ip", T0)
    assert all(b"secret" not in key for key in client.keys("*"))


def test_fixed_window() -> None:
    limiter = FixedWindowLimiter(
        fakeredis.FakeRedis(), prefix="t", limit=2, window=timedelta(hours=1)
    )
    assert limiter.hit("k", T0) and limiter.hit("k", T0)
    assert not limiter.hit("k", T0)
    assert limiter.hit("other", T0)
    assert limiter.hit("k", T0 + timedelta(hours=1))
