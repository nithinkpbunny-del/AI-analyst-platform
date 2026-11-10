"""The read-only HTTP client every connector uses (BUILD_SPEC 6.3, layer 2; 8.1).

``ReadOnlyHttpClient`` only sends ``GET`` requests, plus ``POST`` to an explicit allow-list of URLs that change
nothing in the customer's data (e.g. the OAuth token endpoint, or a vendor's read-only query endpoint). Anything
else raises ``ReadOnlyViolation``. The check runs twice: in the wrapper's own methods, and as an httpx request
hook on the underlying client, so even code that reached the raw client could not send a write.

Requests are retried on 429 and 5xx answers and on network errors, with backoff that honours ``Retry-After``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.base import ReadOnlyViolation

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_DELAYS = (1.0, 3.0, 8.0)
MAX_RETRY_AFTER_SECONDS = 30.0


def _endpoint(url: str | httpx.URL) -> str:
    parts = urlsplit(str(url))
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class ReadOnlyHttpClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        allowed_posts: Iterable[str] = (),
        sleep: Callable[[float], None] = time.sleep,
        delays: tuple[float, ...] = DEFAULT_DELAYS,
    ) -> None:
        self._allowed_posts = frozenset(_endpoint(u) for u in allowed_posts)
        self.__client = client
        self._sleep = sleep
        self._delays = delays
        client.event_hooks.setdefault("request", []).append(self._guard)

    # ------------------------------------------------------------------------------------- the guard
    def _allowed(self, method: str, url: str | httpx.URL) -> bool:
        method = method.upper()
        if method in ("GET", "HEAD"):
            return True
        return method == "POST" and _endpoint(url) in self._allowed_posts

    def _guard(self, request: httpx.Request) -> None:
        if not self._allowed(request.method, request.url):
            raise ReadOnlyViolation(f"{request.method} {_endpoint(request.url)} is not a read")

    def check(self, method: str, url: str) -> None:
        if not self._allowed(method, url):
            raise ReadOnlyViolation(f"{method.upper()} {_endpoint(url)} is not a read")

    # ------------------------------------------------------------------------------------- requests
    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.check(method, url)
        attempts = len(self._delays) + 1
        for attempt in range(attempts):
            try:
                response = self.__client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
                self._sleep(self._delays[attempt])
                continue
            if response.status_code not in RETRY_STATUSES or attempt == attempts - 1:
                return response
            delay = self._retry_after(response) or self._delays[attempt]
            logger.info("source answered %s; retrying in %.0f s", response.status_code, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after", "")
        try:
            return min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(value)))
        except ValueError:
            return None

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return self._send("GET", url, params=params, headers=headers)

    def post_form(
        self, url: str, data: Mapping[str, str], *, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        """A POST to an allow-listed endpoint (token exchange, token revocation, read-only queries)."""
        return self._send("POST", url, data=data, headers=headers)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self._send(method, url, **kwargs)

    def close(self) -> None:
        self.__client.close()
