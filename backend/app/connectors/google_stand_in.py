"""A local stand-in for Google, so Google Sheets works in development without Google credentials.

It is an ``httpx`` transport that answers the same URLs the real connector calls (token endpoint, Sheets API v4,
Drive ``files.get``), in Google's formats, from demo workbooks:

* ``eval/fixtures/marketing_sheet.xlsx`` and ``zoho_budget_expenses.xlsx`` (``make fixtures``), when present;
* a small built-in "Outlet sales (sample)" sheet written the way Indian businesses often type data (dd/mm/yyyy
  dates, ₹ amounts with lakh grouping), always.

The consent screen is a small page served by the API (``/connections/google/stand-in``) and the Picker is a
dialog in the web app listing ``STAND_IN_FILES``. Tokens are not secrets here: they only encode the email typed
on the consent page. Never used in production (``GoogleSheetsSetup`` picks it only in ``APP_ENV=development``).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx
import openpyxl

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"
CODE_PREFIX = "standin-code."
ACCESS_PREFIX = "standin-at."
REFRESH_PREFIX = "standin-rt."
GRANTED_SCOPES = (
    "openid https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/spreadsheets.readonly https://www.googleapis.com/auth/drive.file"
)


@dataclass(frozen=True)
class StandInFile:
    id: str
    name: str
    workbook: str | None  # a file in FIXTURES_DIR, or None for the built-in sample


ALL_FILES = (
    StandInFile("standin-sample-outlets", "Outlet sales (sample)", None),
    StandInFile("standin-urbancart-marketing", "UrbanCart marketing plan", "marketing_sheet.xlsx"),
    StandInFile(
        "standin-urbancart-budget", "UrbanCart budget vs actual", "zoho_budget_expenses.xlsx"
    ),
)


def stand_in_files(fixtures_dir: Path = FIXTURES_DIR) -> list[StandInFile]:
    """What the stand-in Picker offers: the sample always, the demo workbooks when they were generated."""
    return [f for f in ALL_FILES if f.workbook is None or (fixtures_dir / f.workbook).exists()]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


def issue_code(email: str) -> str:
    return CODE_PREFIX + _b64(email.strip().lower())


def _sample_rows() -> list[list[Any]]:
    outlets = [("Indiranagar", "Bengaluru"), ("Koramangala", "Bengaluru"), ("Andheri", "Mumbai")]
    rows: list[list[Any]] = [
        ["Outlet sales (sample)"],
        [],
        ["Date", "Outlet", "City", "Bills", "Sales (₹)", "Discount %"],
    ]
    for day in range(1, 29):
        for index, (outlet, city) in enumerate(outlets):
            bills = 120 + (day * 7 + index * 13) % 45
            sales = bills * (410 + index * 35) + day * 1000
            rows.append(
                [
                    f"{day:02d}/10/2026",
                    outlet,
                    city,
                    bills,
                    f"₹{sales:,}".replace(",", "_").replace("_", ","),
                    f"{5 + (day + index) % 4}%",
                ]
            )
    return rows


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return f"{value:%d/%m/%Y}"  # FORMATTED_STRING in an en-IN spreadsheet
    return value


def _trim(rows: list[list[Any]]) -> list[list[Any]]:
    """Google leaves out empty cells at the end of a row and empty rows at the end of a tab."""
    out = []
    for row in rows:
        while row and row[-1] == "":
            row = row[:-1]
        out.append(row)
    while out and not out[-1]:
        out.pop()
    return out


@lru_cache(maxsize=8)
def _workbook(path: str, mtime: float) -> dict[str, list[list[Any]]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            ws.title: _trim([[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)])
            for ws in workbook.worksheets
        }
    finally:
        workbook.close()


class StandInGoogleTransport(httpx.BaseTransport):
    def __init__(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self._dir = fixtures_dir

    # ------------------------------------------------------------------------------------------ data
    def _tabs(self, file_id: str) -> tuple[StandInFile, dict[str, list[list[Any]]], str] | None:
        found = next((f for f in stand_in_files(self._dir) if f.id == file_id), None)
        if found is None:
            return None
        if found.workbook is None:
            return found, {"Sales": _trim([[_cell(v) for v in r] for r in _sample_rows()])}, "1"
        path = self._dir / found.workbook
        mtime = path.stat().st_mtime
        version = str(int(mtime))  # changes when `make fixtures` writes the file again
        return found, _workbook(str(path), mtime), version

    # ----------------------------------------------------------------------------------- responses
    @staticmethod
    def _json(status: int, body: Any) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _error(self, status: int, message: str) -> httpx.Response:
        return self._json(
            status, {"error": {"code": status, "message": message, "status": "ERROR"}}
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("grant_type") == "authorization_code":
            code = form.get("code", "")
            if not code.startswith(CODE_PREFIX):
                return self._json(400, {"error": "invalid_grant"})
            email = _unb64(code.removeprefix(CODE_PREFIX))
        elif form.get("grant_type") == "refresh_token":
            token = form.get("refresh_token", "")
            if not token.startswith(REFRESH_PREFIX):
                return self._json(400, {"error": "invalid_grant"})
            email = _unb64(token.removeprefix(REFRESH_PREFIX))
        else:
            return self._json(400, {"error": "unsupported_grant_type"})
        claims = {
            "iss": "https://accounts.google.com",
            "aud": form.get("client_id", ""),
            "email": email,
            "email_verified": True,
        }
        id_token = ".".join([_b64('{"alg":"none"}'), _b64(json.dumps(claims)), "stand-in"])
        body: dict[str, Any] = {
            "access_token": ACCESS_PREFIX + _b64(email),
            "expires_in": 3599,
            "scope": GRANTED_SCOPES,
            "token_type": "Bearer",
            "id_token": id_token,
        }
        if form.get("grant_type") == "authorization_code":
            body["refresh_token"] = REFRESH_PREFIX + _b64(email)
        return self._json(200, body)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        endpoint = f"{url.scheme}://{url.host}{url.path}"
        if endpoint == "https://oauth2.googleapis.com/token" and request.method == "POST":
            return self._token(request)
        if endpoint == "https://oauth2.googleapis.com/revoke" and request.method == "POST":
            return self._json(200, {})
        if request.method != "GET":
            return self._error(405, "The stand-in only reads.")
        if not request.headers.get("authorization", "").startswith(f"Bearer {ACCESS_PREFIX}"):
            return self._error(401, "Request had invalid authentication credentials.")

        parts = [unquote(p) for p in url.path.split("/") if p]
        if (
            url.host == "sheets.googleapis.com"
            and parts[:2] == ["v4", "spreadsheets"]
            and len(parts) >= 3
        ):
            found = self._tabs(parts[2])
            if found is None:
                return self._error(404, "Requested entity was not found.")
            file, tabs, _ = found
            if len(parts) == 3:
                return self._json(
                    200,
                    {
                        "spreadsheetId": file.id,
                        "properties": {"title": file.name, "locale": "en_IN"},
                        "sheets": [
                            {
                                "properties": {
                                    "sheetId": i,
                                    "title": t,
                                    "index": i,
                                    "sheetType": "GRID",
                                }
                            }
                            for i, t in enumerate(tabs)
                        ],
                    },
                )
            if len(parts) == 5 and parts[3] == "values":
                title = parts[4].strip("'").replace("''", "'")
                if title not in tabs:
                    return self._error(400, f"Unable to parse range: {parts[4]}")
                return self._json(
                    200, {"range": f"'{title}'", "majorDimension": "ROWS", "values": tabs[title]}
                )
        if (
            url.host == "www.googleapis.com"
            and parts[:3] == ["drive", "v3", "files"]
            and len(parts) == 4
        ):
            found = self._tabs(parts[3])
            if found is None:
                return self._error(404, "File not found.")
            _, _, version = found
            digest = hashlib.sha256(version.encode()).hexdigest()[:8]
            return self._json(
                200, {"version": version, "modifiedTime": f"2026-11-01T00:00:00Z#{digest}"}
            )
        return self._error(404, "Not found.")
