"""The bridge's HTTP clients and its files on the PC."""

from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from tally_bridge.platform_client import (
    PlatformClient,
    PlatformRefused,
    PlatformUnavailable,
    Unauthorized,
    check_server_url,
)
from tally_bridge.store import (
    BridgeConfig,
    DatasetProgress,
    PendingSync,
    Store,
    SyncState,
    protect,
    unprotect,
)
from tally_bridge.tally_client import TallyClient, TallyUnavailable, check_tally_url


@pytest.mark.parametrize(
    "url",
    [
        "http://app.example.in",
        "ftp://x",
        "https://user:pw@app.in",
        "https://app.in/?next=evil",
        "app.in",
    ],
)
def test_the_platform_must_be_https(url: str) -> None:
    with pytest.raises(ValueError):
        check_server_url(url)


def test_plain_http_only_to_this_computer() -> None:
    assert check_server_url("http://localhost:8000/") == "http://localhost:8000"
    assert check_server_url("https://app.example.in/") == "https://app.example.in"


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1:9000", "http://8.8.8.8:9000", "http://tally.example.com"]
)
def test_tally_must_be_local(url: str) -> None:
    with pytest.raises(ValueError):
        check_tally_url(url)


def test_tally_on_the_office_network_is_fine() -> None:
    assert check_tally_url("http://192.168.1.20:9000") == "http://192.168.1.20:9000"


def test_tally_not_running() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TallyUnavailable):
        TallyClient(transport=httpx.MockTransport(refuse)).companies()


def _platform(handler: object, token: str | None = "tlyd_token") -> PlatformClient:  # noqa: S107
    return PlatformClient("https://app.example.in", token, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_large_bodies_are_gzipped_and_the_token_is_sent() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["encoding"] = request.headers.get("content-encoding")
        seen["body"] = json.loads(gzip.decompress(request.read()))
        return httpx.Response(200, json={"accepted": 1, "duplicate": False})

    _platform(handler).send_batch({"records": ["x" * 10_000]})
    assert seen["auth"] == "Bearer tlyd_token"
    assert seen["encoding"] == "gzip"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, Unauthorized),
        (429, PlatformUnavailable),
        (503, PlatformUnavailable),
        (409, PlatformRefused),
    ],
)
def test_answers_map_to_errors(status: int, error: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"code": "company_mismatch", "message": "m"}},
            headers={"Retry-After": "7"},
        )

    with pytest.raises(error) as caught:
        _platform(handler).poll({}, wait_seconds=0)
    if isinstance(caught.value, PlatformUnavailable) and status == 429:
        assert caught.value.retry_after == 7.0
    if isinstance(caught.value, PlatformRefused):
        assert caught.value.code == "company_mismatch"


def test_redirects_are_not_followed() -> None:
    """A redirect could carry the device token to another host."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example.com/steal"})

    with pytest.raises(PlatformRefused):
        _platform(handler).poll({}, wait_seconds=0)


def test_the_token_file_is_private_and_round_trips(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_pairing(
        BridgeConfig(server="https://a.in", device_id="d", tally_url="http://127.0.0.1:9000"),
        "tlyd_secret",
    )
    assert store.token() == "tlyd_secret"
    assert unprotect(protect("x")) == "x"
    if sys.platform != "win32":
        assert oct(os.stat(tmp_path / "token.bin").st_mode & 0o777) == "0o600"
    assert b"tlyd_secret" not in (tmp_path / "config.json").read_bytes()
    store.forget()
    assert not store.paired()


def test_progress_survives_a_restart(tmp_path: Path) -> None:
    store = Store(tmp_path)
    pending = PendingSync("s", "full", "2026-11-20T00:00:00+00:00", "C", "g", alter_id_max=9)
    pending.progress["vouchers"] = DatasetProgress(batches=3, records=600, next_from="2026-05-01")
    store.save_state(SyncState(last_alter_id=5, pending=pending))
    again = Store(tmp_path).state()
    assert again.last_alter_id == 5
    assert again.pending is not None
    assert again.pending.dataset("vouchers").next_from == "2026-05-01"
    (tmp_path / "state.json").write_text("{not json")
    assert Store(tmp_path).state() == SyncState()  # a damaged file starts over, never crashes
