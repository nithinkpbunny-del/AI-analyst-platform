"""A cap on request bodies (M19, docs/SECURITY.md "Uploads and request size").

FastAPI reads a JSON body whole before validating it, so without a cap one request could make the API hold hundreds
of megabytes. Every request is limited here, counted as the bytes arrive (a missing or false ``Content-Length``
does not help):

* file uploads (``.../files``): the upload limit plus room for the multipart wrapping (the route checks the file
  itself again, with a friendlier message);
* the Tally bridge (``/bridge/``): its batch limit (the route checks again after un-gzipping);
* everything else: ``DEFAULT_MAX_BYTES`` (the largest JSON the app sends back to itself, a whole dictionary, is
  far below it).

A request over its cap gets ``413`` in the app's error shape and the body is not read further.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
MULTIPART_OVERHEAD = 1024 * 1024

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]


class _TooLarge(Exception):
    pass


def limit_for(path: str, *, upload_max_mb: int, bridge_max_bytes: int) -> int:
    if path.endswith("/files"):
        return upload_max_mb * 1024 * 1024 + MULTIPART_OVERHEAD
    if path.startswith("/bridge/"):
        return bridge_max_bytes + MULTIPART_OVERHEAD
    return DEFAULT_MAX_BYTES


class BodyLimit:
    def __init__(self, app: App, *, upload_max_mb: int, bridge_max_bytes: int) -> None:
        self.app = app
        self.upload_max_mb = upload_max_mb
        self.bridge_max_bytes = bridge_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(
            scope.get("path", ""),
            upload_max_mb=self.upload_max_mb,
            bridge_max_bytes=self.bridge_max_bytes,
        )
        declared = _content_length(scope)
        code = "file_too_large" if scope.get("path", "").endswith("/files") else "request_too_large"
        if declared is not None and declared > limit:
            await _refuse(send, limit, code)
            return
        received = 0
        started = False
        refused = False

        async def counted_receive() -> Message:
            # FastAPI turns errors while reading a body into its own 400, so the 413 is sent here, at once, and
            # whatever the app answers afterwards is dropped.
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    if not started and not refused:
                        refused = True
                        await _refuse(send, limit, code)
                    raise _TooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started
            if refused:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with contextlib.suppress(_TooLarge):  # the 413 was sent from counted_receive
            await self.app(scope, counted_receive, tracked_send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


async def _refuse(send: Send, limit: int, code: str) -> None:
    megabytes = max(1, limit // (1024 * 1024))
    body = json.dumps(
        {
            "error": {
                "code": code,
                "message": f"This request is too large (at most {megabytes} MB).",
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
