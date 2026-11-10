from __future__ import annotations

import fakeredis

from app.data.locks import RedisLock


def test_only_one_holder_and_only_the_holder_releases() -> None:
    client = fakeredis.FakeRedis()
    now = [0.0]

    def lock() -> RedisLock:
        return RedisLock(
            client,
            "lock:x",
            timeout_seconds=60,
            wait_seconds=1,
            sleep=lambda s: now.__setitem__(0, now[0] + s),
            monotonic=lambda: now[0],
        )

    first, second = lock(), lock()
    assert first.acquire()
    assert not second.acquire()  # waited a second, gave up
    second.release()  # not the holder: does nothing
    assert client.get("lock:x") is not None
    first.release()
    assert second.acquire()


def test_an_expired_lock_is_not_released_by_its_old_holder() -> None:
    client = fakeredis.FakeRedis()
    first = RedisLock(client, "lock:y", timeout_seconds=60, wait_seconds=0)
    assert first.acquire()
    client.delete("lock:y")  # expired
    second = RedisLock(client, "lock:y", timeout_seconds=60, wait_seconds=0)
    assert second.acquire()
    first.release()
    assert client.get("lock:y") is not None
