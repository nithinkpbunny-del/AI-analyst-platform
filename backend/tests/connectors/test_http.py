"""The read-only HTTP client (BUILD_SPEC 6.3, layer 2): only reads leave the platform, retried politely."""

from __future__ import annotations

import httpx
import pytest

from app.connectors.base import ReadOnlyViolation
from app.connectors.http import ReadOnlyHttpClient

TOKEN = "https://oauth2.example.com/token"


class Counter(httpx.BaseTransport):
    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else httpx.Response(200, json={})
        if isinstance(answer, Exception):
            raise answer
        return answer


def make(transport: Counter, sleeps: list[float] | None = None) -> ReadOnlyHttpClient:
    record = sleeps if sleeps is not None else []
    return ReadOnlyHttpClient(
        httpx.Client(transport=transport), allowed_posts=[TOKEN], sleep=record.append
    )


def test_get_is_allowed() -> None:
    transport = Counter(httpx.Response(200, json={"ok": True}))
    assert make(transport).get("https://api.example.com/rows").json() == {"ok": True}


def test_post_only_to_the_allow_list() -> None:
    transport = Counter()
    client = make(transport)
    assert client.post_form(TOKEN + "?x=1", {"a": "b"}).status_code == 200
    with pytest.raises(ReadOnlyViolation):
        client.post_form("https://api.example.com/rows", {"a": "b"})
    assert len(transport.requests) == 1


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "post"])
def test_writes_are_refused(method: str) -> None:
    transport = Counter()
    with pytest.raises(ReadOnlyViolation):
        make(transport).request(method, "https://api.example.com/rows/1")
    assert transport.requests == []


def test_even_the_raw_client_cannot_write() -> None:
    """The guard is also an httpx request hook, so reaching the wrapped client does not help."""
    transport = Counter()
    raw = httpx.Client(transport=transport)
    ReadOnlyHttpClient(raw)
    with pytest.raises(ReadOnlyViolation):
        raw.delete("https://api.example.com/rows/1")
    with pytest.raises(ReadOnlyViolation):
        raw.post("https://api.example.com/rows", json={})
    assert transport.requests == []


def test_429_and_5xx_are_retried_honouring_retry_after() -> None:
    sleeps: list[float] = []
    transport = Counter(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    )
    response = make(transport, sleeps).get("https://api.example.com/rows")
    assert response.status_code == 200
    assert sleeps == [7.0, 3.0]


def test_retry_after_is_capped() -> None:
    sleeps: list[float] = []
    make(Counter(httpx.Response(429, headers={"Retry-After": "3600"})), sleeps).get(
        "https://x.example"
    )
    assert sleeps == [30.0]


def test_gives_up_with_the_last_answer() -> None:
    sleeps: list[float] = []
    transport = Counter(*[httpx.Response(502) for _ in range(4)])
    assert make(transport, sleeps).get("https://api.example.com").status_code == 502
    assert sleeps == [1.0, 3.0, 8.0]


def test_other_errors_are_not_retried() -> None:
    transport = Counter(httpx.Response(404))
    assert make(transport).get("https://api.example.com").status_code == 404
    assert len(transport.requests) == 1


def test_network_errors_are_retried_then_raised() -> None:
    sleeps: list[float] = []
    transport = Counter(*[httpx.ConnectError("down") for _ in range(4)])
    with pytest.raises(httpx.ConnectError):
        make(transport, sleeps).get("https://api.example.com")
    assert len(sleeps) == 3
