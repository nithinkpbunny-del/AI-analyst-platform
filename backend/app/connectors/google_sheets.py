"""Google Sheets connector (BUILD_SPEC 8.3).

* **Sign-in**: OAuth 2.0 authorization code with PKCE. Scopes ``spreadsheets.readonly`` and ``drive.file`` (plus
  ``openid email`` to label the connection with the Google account). The person picks spreadsheets with the
  Google Picker, so the app never lists their Drive. Granted scopes are checked after every token exchange and
  refresh: any scope that could write is refused (BUILD_SPEC 6.3, layer 1).
* **Reading**: Sheets API v4 ``spreadsheets.get`` (tabs) and ``values.get`` (cells), through the read-only HTTP
  client; only the token endpoints accept a POST. Each tab is a dataset, ``<spreadsheet id>/<tab id>`` (the tab
  id survives renames).
* **Freshness**: Drive's ``version`` number of the spreadsheet tells whether anything changed since the last
  read, so an unchanged spreadsheet is not downloaded again after the cache time (DECISIONS D53).

Modes (``GoogleSheetsSetup.mode``): ``google`` with real credentials, ``stand_in`` for local development without
them (``app.connectors.google_stand_in``), ``off`` otherwise. See ``docs/SETUP_GOOGLE.md``.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx

from app.config import Settings
from app.connectors import tabular
from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.http import ReadOnlyHttpClient

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an endpoint, not a secret)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = ("openid", "email", SHEETS_SCOPE, DRIVE_FILE_SCOPE)
REQUIRED_SCOPES = frozenset({SHEETS_SCOPE, DRIVE_FILE_SCOPE})
# Everything Google may report back for the request above. Anything else (e.g. ".../auth/spreadsheets" or
# ".../auth/drive", which can write) is refused.
ALLOWED_SCOPES = REQUIRED_SCOPES | {
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
}
MAX_SPREADSHEETS = 20
HTTP_TIMEOUT_SECONDS = 30.0

Mode = Literal["google", "stand_in", "off"]
REAUTH_MESSAGE = "Google stopped sharing data. Sign in to Google again to reconnect."


@dataclass(frozen=True)
class GoogleSheetsSetup:
    mode: Mode
    client_id: str = ""
    client_secret: str = ""
    picker_api_key: str = ""
    # The Google Cloud project number; the Picker needs it to grant drive.file access.
    app_id: str = ""
    api_base_url: str = "http://localhost:8000"
    transport: httpx.BaseTransport | None = None  # the stand-in, or recorded answers in tests
    sleep: Callable[[float], None] | None = None

    @property
    def available(self) -> bool:
        return self.mode != "off"

    def http(self, timeout_seconds: float = HTTP_TIMEOUT_SECONDS) -> ReadOnlyHttpClient:
        client = httpx.Client(transport=self.transport, timeout=timeout_seconds)
        kwargs: dict[str, Any] = {"allowed_posts": (TOKEN_URL, REVOKE_URL)}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return ReadOnlyHttpClient(client, **kwargs)

    def redirect_uri(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/connections/google/callback"


def default_setup(settings: Settings) -> GoogleSheetsSetup:
    """Real Google when every credential is set; the local stand-in in development; otherwise off."""
    secret = settings.google_oauth_client_secret.get_secret_value()
    picker = settings.google_picker_api_key.get_secret_value()
    if (
        settings.google_oauth_client_id
        and secret
        and picker
        and settings.google_cloud_project_number
    ):
        return GoogleSheetsSetup(
            mode="google",
            client_id=settings.google_oauth_client_id,
            client_secret=secret,
            picker_api_key=picker,
            app_id=settings.google_cloud_project_number,
            api_base_url=settings.api_base_url,
        )
    if settings.app_env == "development":
        from app.connectors.google_stand_in import StandInGoogleTransport

        return GoogleSheetsSetup(
            mode="stand_in",
            client_id="stand-in-client",
            client_secret="stand-in-secret",  # noqa: S106 (not a real secret)
            picker_api_key="stand-in",
            app_id="0",
            api_base_url=settings.api_base_url,
            transport=StandInGoogleTransport(),
        )
    return GoogleSheetsSetup(mode="off", api_base_url=settings.api_base_url)


# ------------------------------------------------------------------------------------------------ OAuth
def authorization_url(
    setup: GoogleSheetsSetup, *, state: str, code_challenge: str, login_hint: str | None = None
) -> str:
    params = {
        "client_id": setup.client_id,
        "redirect_uri": setup.redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",  # a refresh token, so data can be read later
        "prompt": "consent",
    }
    if login_hint:
        params["login_hint"] = login_hint
    base = (
        f"{setup.api_base_url.rstrip('/')}/connections/google/stand-in"
        if setup.mode == "stand_in"
        else AUTHORIZE_URL
    )
    return f"{base}?{urlencode(params)}"


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: frozenset[str]
    email: str | None = None


def check_scopes(scopes: frozenset[str]) -> None:
    extra = scopes - ALLOWED_SCOPES
    if extra:
        raise ConnectorError(
            "invalid",
            "Google gave this app more access than it asks for. Connect again and allow only reading.",
            detail=f"unexpected scopes: {sorted(extra)}",
        )
    missing = REQUIRED_SCOPES - scopes
    if missing:
        raise ConnectorError(
            "invalid",
            "Google did not allow reading your spreadsheets. Connect again and tick both boxes on Google's "
            "consent screen.",
            detail=f"missing scopes: {sorted(missing)}",
        )


def _email_from_id_token(id_token: str | None, client_id: str) -> str | None:
    """The account's email. The token came straight from Google's token endpoint over TLS, so its signature
    need not be checked (OpenID Connect Core 3.1.3.7); the audience still must be this app."""
    if not id_token or id_token.count(".") != 2:
        return None
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("aud") != client_id or not claims.get("email_verified", False):
        return None
    email = claims.get("email")
    return str(email) if email else None


def _token_error(response: httpx.Response) -> ConnectorError:
    try:
        error = str(response.json().get("error", ""))
    except ValueError:
        error = ""
    if response.status_code in (400, 401) and error in (
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
    ):
        return ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=f"token endpoint: {error}")
    if response.status_code == 429:
        return ConnectorError(
            "rate_limited", "Google is limiting requests right now. Try again in a minute."
        )
    return ConnectorError(
        "unavailable",
        "Google did not answer as expected. Try again in a few minutes.",
        detail=f"token endpoint: HTTP {response.status_code} {error}",
    )


def _tokens(response: httpx.Response, setup: GoogleSheetsSetup) -> GoogleTokens:
    if response.status_code != 200:
        raise _token_error(response)
    body = response.json()
    scopes = frozenset(str(body.get("scope", "")).split())
    check_scopes(scopes)
    return GoogleTokens(
        access_token=str(body["access_token"]),
        refresh_token=body.get("refresh_token"),
        expires_in=int(body.get("expires_in", 3600)),
        scopes=scopes,
        email=_email_from_id_token(body.get("id_token"), setup.client_id),
    )


def exchange_code(
    setup: GoogleSheetsSetup, http: ReadOnlyHttpClient, *, code: str, code_verifier: str
) -> GoogleTokens:
    response = http.post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": setup.redirect_uri(),
            "client_id": setup.client_id,
            "client_secret": setup.client_secret,
            "code_verifier": code_verifier,
        },
    )
    tokens = _tokens(response, setup)
    if not tokens.refresh_token:
        raise ConnectorError(
            "invalid",
            "Google did not allow offline reading. Connect again and allow access.",
            detail="no refresh token",
        )
    return tokens


def refresh_access(
    setup: GoogleSheetsSetup, http: ReadOnlyHttpClient, refresh_token: str
) -> GoogleTokens:
    response = http.post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": setup.client_id,
            "client_secret": setup.client_secret,
        },
    )
    return _tokens(response, setup)


def revoke(http: ReadOnlyHttpClient, token: str) -> None:
    """Tell Google to forget this app's access. Best effort: a failure is logged, never raised."""
    try:
        response = http.post_form(REVOKE_URL, {"token": token})
        if response.status_code not in (200, 400):  # 400: already invalid
            logger.warning("Google token revocation answered HTTP %s", response.status_code)
    except httpx.HTTPError as error:
        logger.warning("Google token revocation failed: %s", type(error).__name__)


# -------------------------------------------------------------------------------------------- connector
@dataclass
class Spreadsheet:
    id: str
    name: str


@dataclass
class _Meta:
    title: str
    tabs: dict[int, str] = field(default_factory=dict)  # sheetId -> title, in order


class GoogleSheetsConnector:
    type = "google_sheets"

    def __init__(
        self,
        *,
        setup: GoogleSheetsSetup,
        refresh_token: str,
        spreadsheets: list[Spreadsheet],
        timezone: str,
        max_rows: int,
        http: ReadOnlyHttpClient | None = None,
    ) -> None:
        self._setup = setup
        self._refresh_token = refresh_token
        self._spreadsheets = spreadsheets
        self._timezone = timezone
        self._max_rows = max_rows
        self._http = http or setup.http()
        self._access: GoogleTokens | None = None
        self._meta: dict[str, _Meta] = {}

    def close(self) -> None:
        self._http.close()

    def access_token(self) -> GoogleTokens:
        if self._access is None:
            self._access = refresh_access(self._setup, self._http, self._refresh_token)
        return self._access

    def _name(self, spreadsheet_id: str) -> str:
        found = next((s.name for s in self._spreadsheets if s.id == spreadsheet_id), None)
        return found or "the spreadsheet"

    def _get(self, url: str, params: dict[str, str], spreadsheet_id: str) -> Any:
        token = self.access_token().access_token
        response = self._http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 200:
            return response.json()
        name = self._name(spreadsheet_id)
        status = response.status_code
        if status == 401:
            raise ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=f"HTTP 401 for {url}")
        if status == 403:
            raise ConnectorError(
                "not_found",
                f"Google no longer lets this app read '{name}'. Choose it again with 'Choose spreadsheets'.",
                detail=f"HTTP 403 for {url}",
            )
        if status == 404:
            raise ConnectorError(
                "not_found",
                f"'{name}' was deleted or moved out of reach in Google Drive.",
                detail=f"HTTP 404 for {url}",
            )
        if status == 429:
            raise ConnectorError(
                "rate_limited", "Google is limiting requests right now. Try again in a minute."
            )
        raise ConnectorError(
            "unavailable",
            "Google Sheets did not answer as expected. Try again in a few minutes.",
            detail=f"HTTP {status} for {url}",
        )

    def _metadata(self, spreadsheet_id: str) -> _Meta:
        if spreadsheet_id not in self._meta:
            body = self._get(
                f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}",
                {
                    "fields": "properties.title,sheets.properties(sheetId,title,index,sheetType,hidden)"
                },
                spreadsheet_id,
            )
            meta = _Meta(title=str(body.get("properties", {}).get("title", "Spreadsheet")))
            for sheet in sorted(
                body.get("sheets", []), key=lambda s: s["properties"].get("index", 0)
            ):
                props = sheet["properties"]
                if props.get("sheetType", "GRID") != "GRID" or props.get("hidden"):
                    continue
                meta.tabs[int(props["sheetId"])] = str(props["title"])
            self._meta[spreadsheet_id] = meta
        return self._meta[spreadsheet_id]

    def spreadsheet_title(self, spreadsheet_id: str) -> str:
        return self._metadata(spreadsheet_id).title

    def list_datasets(self) -> list[DatasetInfo]:
        found = []
        for spreadsheet in self._spreadsheets:
            meta = self._metadata(spreadsheet.id)
            for tab_id, title in meta.tabs.items():
                found.append(
                    DatasetInfo(ref=f"{spreadsheet.id}/{tab_id}", name=f"{meta.title} · {title}")
                )
        return found

    @staticmethod
    def _split(ref: str) -> tuple[str, int]:
        spreadsheet_id, _, tab = ref.rpartition("/")
        return spreadsheet_id, int(tab)

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        spreadsheet_id, tab_id = self._split(dataset.ref)
        meta = self._metadata(spreadsheet_id)
        if tab_id not in meta.tabs:
            raise ConnectorError(
                "not_found", f"The tab of '{dataset.name}' was deleted from '{meta.title}'."
            )
        version = self.source_version(dataset)
        title = meta.tabs[tab_id].replace("'", "''")
        body = self._get(
            f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}/values/{quote(f"'{title}'", safe='')}",
            {
                "majorDimension": "ROWS",
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
            spreadsheet_id,
        )
        rows = body.get("values", [])
        table = tabular.parse_rows(rows, timezone=self._timezone, max_rows=self._max_rows)
        return FetchResult.from_table(table, source_version=version)

    def source_version(self, dataset: DatasetInfo) -> str | None:
        spreadsheet_id, _ = self._split(dataset.ref)
        try:
            body = self._get(
                f"{DRIVE_FILES_URL}/{quote(spreadsheet_id, safe='')}",
                {"fields": "version,modifiedTime"},
                spreadsheet_id,
            )
        except ConnectorError as error:
            if error.kind == "needs_reauth":
                raise
            return None  # cannot tell; the cache falls back to its time limit
        version = body.get("version")
        return str(version) if version is not None else None

    def test(self) -> ConnectionTestResult:
        checks: list[TestCheck] = []
        try:
            self.access_token()
            checks += [TestCheck("signed_in", True), TestCheck("read_only", True)]
            count = 0
            for spreadsheet in self._spreadsheets:
                count += len(self._metadata(spreadsheet.id).tabs)
            checks.append(TestCheck("spreadsheets_readable", True))
        except ConnectorError as error:
            checks.append(TestCheck("spreadsheets_readable", False))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        n = len(self._spreadsheets)
        return ConnectionTestResult(
            ok=True,
            message=f"Signed in to Google with read-only access. {n} {'spreadsheet' if n == 1 else 'spreadsheets'}, "
            f"{count} {'tab' if count == 1 else 'tabs'}.",
            checks=checks,
            details={"spreadsheets": n, "datasets": count},
        )

    def supports_incremental(self) -> bool:
        return False

    def supports_pushdown(self) -> bool:
        return False
