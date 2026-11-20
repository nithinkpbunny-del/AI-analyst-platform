"""Talking to the platform: outbound only, HTTPS, the device token in every request.

The bridge always calls the platform; the platform never connects to the PC. The only thing the platform can ask
for (in the answer to ``poll``) is "please read again now"; settings it sends are clamped to safe ranges. Plain
``http`` is allowed only to this computer (development).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from tally_bridge import PROTOCOL_VERSION, __version__

TIMEOUT_SECONDS = 60.0
GZIP_FROM_BYTES = 8 * 1024
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Unauthorized(Exception):
    """The platform no longer accepts this PC's token (it was unpaired, or the connection was removed)."""


class PlatformUnavailable(Exception):
    """The platform could not be reached or is busy: try again later."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class PlatformRefused(Exception):
    """The platform understood the request and said no (wrong code, too large, missing batches, ...)."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def check_server_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if not parts.hostname or parts.scheme not in ("https", "http"):
        raise ValueError("The platform address must start with https://")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        raise ValueError("The platform address must start with https:// (encrypted)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(
            "The platform address must be a plain address, like https://app.example.in"
        )
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


@dataclass(frozen=True)
class Settings:
    interval_minutes: int = 15
    full_sync_hours: int = 24
    max_batch_rows: int = 2000
    max_batch_bytes: int = 1_500_000
    history_months: int = 36
    poll_wait_seconds: int = 25

    @classmethod
    def from_json(cls, raw: Any) -> Settings:
        base = cls()
        if not isinstance(raw, dict):
            return base

        def pick(name: str, low: int, high: int) -> int:
            value = raw.get(name)
            if not isinstance(value, int) or isinstance(value, bool):
                return int(getattr(base, name))
            return max(low, min(high, value))

        return cls(
            interval_minutes=pick("interval_minutes", 5, 24 * 60),
            full_sync_hours=pick("full_sync_hours", 1, 24 * 14),
            max_batch_rows=pick("max_batch_rows", 50, 10_000),
            max_batch_bytes=pick("max_batch_bytes", 64 * 1024, 8 * 1024 * 1024),
            history_months=pick("history_months", 1, 120),
            poll_wait_seconds=pick("poll_wait_seconds", 0, 50),
        )


@dataclass(frozen=True)
class Paired:
    device_id: str
    token: str
    connection_name: str
    workspace_name: str
    settings: Settings


@dataclass(frozen=True)
class PollAnswer:
    refresh: bool
    full: bool
    settings: Settings


class PlatformClient:
    def __init__(
        self,
        server: str,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.server = check_server_url(server)
        self._token = token
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,  # a redirect could carry the token elsewhere
            headers={"User-Agent": f"tally-bridge/{__version__}"},
        )

    def close(self) -> None:
        self._http.close()

    def _url(self, path: str) -> str:
        return f"{self.server}/bridge/tally{path}"

    def _call(
        self, path: str, body: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Bridge-Protocol": str(PROTOCOL_VERSION),
        }
        if len(data) >= GZIP_FROM_BYTES:
            data = gzip.compress(data, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._http.post(
                self._url(path),
                content=data,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TransportError as error:
            raise PlatformUnavailable(type(error).__name__) from error
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if 200 <= status < 300:
            return payload
        if status == 401:
            raise Unauthorized("not paired")
        if status == 429 or status >= 500:
            retry = response.headers.get("Retry-After")
            raise PlatformUnavailable(
                f"HTTP {status}", float(retry) if retry and retry.isdigit() else None
            )
        found = payload.get("error")
        problem: dict[str, Any] = found if isinstance(found, dict) else {}
        raise PlatformRefused(
            status,
            str(problem.get("code", "refused")),
            str(problem.get("message", f"HTTP {status}")),
            {k: v for k, v in problem.items() if k not in ("code", "message")},
        )

    # --------------------------------------------------------------------------------------- the calls
    def pair(self, code: str, *, device_name: str) -> Paired:
        body = self._call(
            "/pair",
            {"code": code, "device_name": device_name[:80], "bridge_version": __version__},
        )
        return Paired(
            device_id=str(body.get("device_id", "")),
            token=str(body.get("token", "")),
            connection_name=str(body.get("connection_name", "")),
            workspace_name=str(body.get("workspace_name", "")),
            settings=Settings.from_json(body.get("settings")),
        )

    def poll(self, status: dict[str, Any], *, wait_seconds: int) -> PollAnswer:
        body = self._call(
            "/poll",
            {"wait_seconds": max(0, wait_seconds), "status": status},
            timeout=wait_seconds + 20.0,
        )
        return PollAnswer(
            refresh=body.get("refresh") is True,
            full=body.get("full") is True,
            settings=Settings.from_json(body.get("settings")),
        )

    def send_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        return self._call("/batches", batch)

    def complete(self, sync_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/syncs/{sync_id}/complete", body)
