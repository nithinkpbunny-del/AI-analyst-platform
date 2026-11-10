"""A small Redis lock: ``SET key token NX EX`` to take it, and a WATCH/MULTI check-and-delete to give it back,
so only the holder can release it and an expired lock is never released by someone else."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

import redis
from redis.exceptions import WatchError


class RedisLock:
    def __init__(
        self,
        client: redis.Redis[bytes],
        name: str,
        *,
        timeout_seconds: int,
        wait_seconds: float,
        poll_seconds: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis = client
        self._name = name
        self._timeout = timeout_seconds
        self._wait = wait_seconds
        self._poll = poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._token = secrets.token_hex(16).encode()

    def acquire(self) -> bool:
        deadline = self._monotonic() + self._wait
        while True:
            if self._redis.set(self._name, self._token, nx=True, ex=self._timeout):
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(self._poll)

    def release(self) -> None:
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(self._name)
                current: bytes | None = pipe.get(self._name)  # type: ignore[assignment]  # immediate after WATCH
                if current != self._token:
                    pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(self._name)
                pipe.execute()
            except WatchError:  # changed meanwhile: no longer ours
                return
