"""Recorded Google answers for tests (BUILD_SPEC 8.1: "VCR-style JSON fixtures; no live calls in CI").

``RecordedGoogle`` is an httpx transport that answers from ``tests/connectors/fixtures/google_sheets.json`` and
remembers every request, so tests can check what was sent (e.g. that nothing but reads went out).
``override`` puts an extra answer in front, for one test (a changed Drive version, an outage).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

from app.connectors.google_sheets import GoogleSheetsSetup

FIXTURE = Path(__file__).parent / "connectors" / "fixtures" / "google_sheets.json"
CLIENT_ID = "test-client.apps.googleusercontent.com"
SHEET_ID = "1UrbanCartTargets2026aBcDeFgHiJkLmNoPqRsTu"
NO_ACCESS_ID = "1NoLongerSharedSheetxYz0123456789abcdefghij"
DELETED_ID = "1DeletedSpreadsheetQwErTy0123456789abcdefgh"
REFRESH_TOKEN = "1//test-refresh-token"
ACCESS_TOKEN = "ya29.test-access-token"
ACCOUNT = "asha.urbancart@gmail.com"


@dataclass(frozen=True)
class Call:
    method: str
    url: str  # scheme://host/path, unquoted, without the query
    query: dict[str, str]
    form: dict[str, str]
    authorization: str


class RecordedGoogle(httpx.BaseTransport):
    def __init__(self, path: Path = FIXTURE) -> None:
        self.interactions: list[dict[str, Any]] = json.loads(path.read_text())["interactions"]
        self.overrides: list[dict[str, Any]] = []
        self.calls: list[Call] = []

    def override(
        self,
        method: str,
        url: str,
        status: int,
        body: Any,
        *,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.overrides.insert(
            0,
            {
                "request": {"method": method, "url": url, **({"form": form} if form else {})},
                "response": {"status": status, "json": body, "headers": headers or {}},
            },
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = f"{request.url.scheme}://{request.url.host}{unquote(request.url.path)}"
        form = (
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.method == "POST"
            else {}
        )
        query = dict(request.url.params.items())
        self.calls.append(
            Call(request.method, url, query, form, request.headers.get("authorization", ""))
        )
        for item in [*self.overrides, *self.interactions]:
            wanted = item["request"]
            if wanted["method"] != request.method or wanted["url"] != url:
                continue
            if any(form.get(k) != v for k, v in wanted.get("form", {}).items()):
                continue
            answer = item["response"]
            return httpx.Response(
                answer["status"], json=answer["json"], headers=answer.get("headers") or {}
            )
        return httpx.Response(404, json={"error": {"code": 404, "message": "no recorded answer"}})

    def methods(self) -> set[tuple[str, str]]:
        return {(c.method, c.url) for c in self.calls}


def recorded_setup(transport: RecordedGoogle) -> GoogleSheetsSetup:
    return GoogleSheetsSetup(
        mode="google",
        client_id=CLIENT_ID,
        client_secret="test-client-secret",
        picker_api_key="test-picker-key",
        app_id="123456789012",
        api_base_url="http://localhost:8000",
        transport=transport,
        sleep=lambda _seconds: None,
    )
