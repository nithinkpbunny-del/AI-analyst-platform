"""The Zoho Books connector (BUILD_SPEC 8.6): invoices, bills, expenses, contacts, items, chart of accounts and a
monthly profit and loss report, read-only.

* **Sign-in**: Zoho's OAuth 2.0 authorization-code flow with ``access_type=offline`` (a refresh token) and **read
  scopes only** (``ZohoBooks.<module>.READ``). Anything else in the scope setting is dropped before it is
  requested, and a token whose granted scopes include a write is refused (BUILD_SPEC 6.3, layer 1). The refresh
  token is stored encrypted; access tokens live for an hour and are kept in memory only.
* **Data centres**: India (``accounts.zoho.in`` / ``www.zohoapis.in``) by default (``ZOHO_REGION``). Zoho sends
  the person's own data centre back with the sign-in (``accounts-server``); it is accepted only when it is one
  of Zoho's known data centres, and the API host the token answer names must belong to the same one
  (DECISIONS D96).
* **Organisation**: one Zoho Books organisation per connection (``organization_id`` on every request). The default
  organisation is chosen at sign-in; the Owner can switch to another one the account can see.
* **Reading**: ``GET`` only (the read-only HTTP client allows a ``POST`` to the token and revoke endpoints and
  nothing else). Pages of 200 (``page`` / ``per_page`` / ``page_context.has_more_page``). Incremental reads ask
  for rows modified since the newest ``last_modified_time`` seen; they replace earlier copies by id. The profit and
  loss report is read one month at a time; later reads repeat the last two months (books are adjusted).
* **Limits**: Zoho counts API calls per organisation per day (1,000 on the free plan, more on paid plans) and per
  minute (100). Every call is counted in Redis; reads slow down near the per-minute limit and stop at 90% of the
  daily limit, and the platform then keeps using the copy it already has (DECISIONS D97).
* **Money is in rupees** (decimals in the organisation's currency), declared by the source (``stored_in``), like
  Shopify and unlike Razorpay's paise (D95).
* **Personal data**: contacts are read with their name, type and balances only; e-mail addresses, phone numbers,
  addresses, contact persons, GSTIN/PAN and bank details are never requested (data minimisation, D98).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
import pandas as pd
import redis

from app.config import Settings
from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.http import ReadOnlyHttpClient
from app.connectors.tabular import ColumnInfo, ColumnType

logger = logging.getLogger(__name__)

# Zoho's data centres: the accounts server (sign-in, tokens) and the API host of each.
REGIONS: dict[str, tuple[str, str]] = {
    "in": ("https://accounts.zoho.in", "https://www.zohoapis.in"),
    "com": ("https://accounts.zoho.com", "https://www.zohoapis.com"),
    "eu": ("https://accounts.zoho.eu", "https://www.zohoapis.eu"),
    "com.au": ("https://accounts.zoho.com.au", "https://www.zohoapis.com.au"),
    "jp": ("https://accounts.zoho.jp", "https://www.zohoapis.jp"),
    "ca": ("https://accounts.zohocloud.ca", "https://www.zohoapis.ca"),
    "sa": ("https://accounts.zoho.sa", "https://www.zohoapis.sa"),
}
REGION_NAMES = {
    "in": "India",
    "com": "United States",
    "eu": "Europe",
    "com.au": "Australia",
    "jp": "Japan",
    "ca": "Canada",
    "sa": "Saudi Arabia",
}
DEFAULT_SCOPES = (
    "ZohoBooks.invoices.READ",
    "ZohoBooks.bills.READ",
    "ZohoBooks.expenses.READ",
    "ZohoBooks.contacts.READ",
    "ZohoBooks.settings.READ",  # organisations and items
    "ZohoBooks.accountants.READ",  # chart of accounts
)
# Zoho's documented scope list has no "reports" module; the profit and loss report is read with the scopes above.
# If an organisation refuses it, only that table says so (the connection stays live) and ``ZOHO_SCOPES`` can add
# the scope Zoho's API console names for reports (DECISIONS D96).
REPORT_REFUSED = (
    "Zoho Books did not allow reading the profit and loss report with the access given. The other tables are "
    "read as usual. See docs/SETUP_ZOHO.md (Reports)."
)
REQUIRED_SCOPES = frozenset(
    {"zohobooks.invoices.read", "zohobooks.settings.read", "zohobooks.contacts.read"}
)
PAGE_SIZE = 200
HTTP_TIMEOUT_SECONDS = 30.0
MAX_PAGES_PER_FETCH = 10_000
OVERLAP = timedelta(hours=1)
REPORT_LOOKBACK_MONTHS = 2
REAUTH_MESSAGE = "Zoho Books stopped sharing data. Sign in to Zoho again to reconnect."
QUOTA_MESSAGE = (
    "Zoho Books' daily API limit for this organisation is nearly used up, so the last data read is shown. "
    "It resets tomorrow."
)
TOKEN_SAFETY = timedelta(minutes=5)

Mode = Literal["zoho", "stand_in", "off"]


def _scope_key(scope: str) -> str:
    return scope.strip().lower()


def read_scopes(value: str) -> tuple[str, ...]:
    """The configured scopes in their written order without repeats; the defaults when none are set. Only read
    scopes are ever requested (BUILD_SPEC 6.3, layer 1): anything else in the setting is dropped."""
    words = (s.strip() for s in re.split(r"[,\s]+", value) if s.strip())
    chosen = tuple(
        dict.fromkeys(
            s for s in words if s.lower().startswith("zohobooks.") and s.lower().endswith(".read")
        )
    )
    return chosen or DEFAULT_SCOPES


def check_scopes(granted: str | None) -> None:
    """Zoho may report the granted scopes with the token. A write scope, or a missing core read, is refused."""
    if not granted:
        return
    scopes = {_scope_key(s) for s in re.split(r"[,\s]+", granted) if s.strip()}
    extra = sorted(s for s in scopes if not s.endswith(".read") and s != "aaaserver.profile.read")
    if extra:
        raise ConnectorError(
            "invalid",
            "Zoho gave this app more access than it asks for. Connect again and allow only reading.",
            detail=f"unexpected scopes: {extra}",
        )
    missing = REQUIRED_SCOPES - scopes
    if missing:
        raise ConnectorError(
            "invalid",
            "Zoho did not allow reading invoices, contacts and settings. Connect again and accept the access "
            "the app asks for.",
            detail=f"missing scopes: {sorted(missing)}",
        )


def region_for_accounts_server(server: str) -> str | None:
    """The data centre of an ``accounts-server`` Zoho sent back; None for anything that is not Zoho's."""
    parts = urlsplit(server.strip())
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    for region, (accounts, _api) in REGIONS.items():
        if origin == accounts:
            return region
    return None


def api_domain_for(region: str, given: str | None) -> str:
    """The API host to use: the token answer's ``api_domain`` when it is the region's own, else the region's."""
    expected = REGIONS[region][1]
    if given:
        parts = urlsplit(given.strip())
        if f"{parts.scheme}://{parts.netloc}".lower() == expected and parts.path in ("", "/"):
            return expected
        logger.warning("Zoho named an unexpected API host; using the region's own")
    return expected


@dataclass
class _TokenCache:
    """Access tokens in memory (never stored): one per refresh token, until five minutes before expiry."""

    items: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def key(refresh_token: str) -> str:
        return hashlib.sha256(refresh_token.encode()).hexdigest()

    def get(self, refresh_token: str, now: datetime) -> str | None:
        with self.lock:
            found = self.items.get(self.key(refresh_token))
        if found is None or found[1] <= now:
            return None
        return found[0]

    def put(self, refresh_token: str, token: str, expires_at: datetime) -> None:
        with self.lock:
            self.items[self.key(refresh_token)] = (token, expires_at)

    def drop(self, refresh_token: str) -> None:
        with self.lock:
            self.items.pop(self.key(refresh_token), None)


@dataclass(frozen=True)
class ZohoSetup:
    """Where Zoho is and how the app is registered. Tests and the development stand-in replace the transport."""

    mode: Mode = "off"
    client_id: str = ""
    client_secret: str = ""
    region: str = "in"
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    api_base_url: str = "http://localhost:8000"
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] | None = None
    history_days: int = 730
    report_months: int = 24
    daily_call_limit: int = 1000
    # Reading stops when this share of the daily limit is used (the rest is left for the Owner's own tools).
    daily_reserve_pct: float = 10.0
    per_minute_limit: int = 90
    tokens: _TokenCache = field(default_factory=_TokenCache, compare=False, repr=False)

    @property
    def available(self) -> bool:
        return self.mode != "off"

    @property
    def stand_in(self) -> bool:
        return self.mode == "stand_in"

    def redirect_uri(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/connections/zoho/callback"

    @staticmethod
    def accounts_url(region: str) -> str:
        return REGIONS[region][0]

    def token_url(self, region: str) -> str:
        return f"{self.accounts_url(region)}/oauth/v2/token"

    def revoke_url(self, region: str) -> str:
        return f"{self.accounts_url(region)}/oauth/v2/token/revoke"

    def http(self, region: str) -> ReadOnlyHttpClient:
        client = httpx.Client(transport=self.transport, timeout=HTTP_TIMEOUT_SECONDS)
        kwargs: dict[str, Any] = {
            "allowed_posts": (self.token_url(region), self.revoke_url(region))
        }
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return ReadOnlyHttpClient(client, **kwargs)


def default_setup(settings: Settings) -> ZohoSetup:
    """Real Zoho when the app's client id and secret are set; the local stand-in in development; otherwise off."""
    region = settings.zoho_region if settings.zoho_region in REGIONS else "in"
    common: dict[str, Any] = {
        "region": region,
        "scopes": read_scopes(settings.zoho_scopes),
        "api_base_url": settings.api_base_url,
        "history_days": settings.zoho_history_days,
        "report_months": settings.zoho_report_months,
        "daily_call_limit": settings.zoho_daily_call_limit,
    }
    secret = settings.zoho_client_secret.get_secret_value()
    if settings.zoho_client_id and secret:
        return ZohoSetup(
            mode="zoho", client_id=settings.zoho_client_id, client_secret=secret, **common
        )
    if settings.app_env == "development":
        from app.connectors.zoho_stand_in import (
            STAND_IN_CLIENT_ID,
            STAND_IN_CLIENT_SECRET,
            StandInZohoTransport,
        )

        return ZohoSetup(
            mode="stand_in",
            client_id=STAND_IN_CLIENT_ID,
            client_secret=STAND_IN_CLIENT_SECRET,
            transport=StandInZohoTransport(),
            **common,
        )
    return ZohoSetup(mode="off", **common)


# ------------------------------------------------------------------------------------------------ OAuth
def authorization_url(setup: ZohoSetup, *, state: str) -> str:
    params = {
        "scope": ",".join(setup.scopes),
        "client_id": setup.client_id,
        "response_type": "code",
        "access_type": "offline",  # a refresh token, so data can be read later
        "prompt": "consent",
        "redirect_uri": setup.redirect_uri(),
        "state": state,
    }
    if setup.stand_in:
        return f"{setup.api_base_url.rstrip('/')}/connections/zoho/stand-in?{urlencode(params)}"
    return f"{setup.accounts_url(setup.region)}/oauth/v2/auth?{urlencode(params)}"


@dataclass(frozen=True)
class ZohoTokens:
    refresh_token: str
    access_token: str
    expires_in: int
    api_domain: str


def _token_body(response: httpx.Response, *, what: str) -> dict[str, Any]:
    """Zoho answers token errors with HTTP 200 and ``{"error": ...}``; both shapes are handled."""
    if response.status_code == 429:
        raise ConnectorError(
            "rate_limited", "Zoho is limiting sign-ins right now. Try again in a few minutes."
        )
    try:
        body = response.json()
    except ValueError as error:
        raise ConnectorError(
            "unavailable", "Zoho sent an answer that could not be read.", detail=what
        ) from error
    if not isinstance(body, dict):
        raise ConnectorError("unavailable", "Zoho sent an answer that could not be read.")
    if response.status_code >= 500:
        raise ConnectorError(
            "unavailable",
            "Zoho is not answering properly. Try again in a few minutes.",
            detail=f"{what}: HTTP {response.status_code}",
        )
    refused = body.get("error")
    if refused or response.status_code != 200 or not body.get("access_token"):
        raise ConnectorError(
            "needs_reauth",
            "Zoho did not accept the sign-in. Sign in to Zoho again.",
            detail=f"{what}: {refused or response.status_code}",
        )
    return body


def exchange_code(
    setup: ZohoSetup, http: ReadOnlyHttpClient, *, region: str, code: str
) -> ZohoTokens:
    response = http.post_form(
        setup.token_url(region),
        {
            "grant_type": "authorization_code",
            "client_id": setup.client_id,
            "client_secret": setup.client_secret,
            "redirect_uri": setup.redirect_uri(),
            "code": code,
        },
    )
    try:
        body = _token_body(response, what="code exchange")
    except ConnectorError as error:
        if error.kind == "needs_reauth":
            raise ConnectorError(
                "invalid",
                "Zoho did not accept the sign-in. Start again with 'Connect Zoho Books'.",
                detail=error.detail,
            ) from error
        raise
    refresh = body.get("refresh_token")
    if not refresh:
        raise ConnectorError(
            "invalid",
            "Zoho did not give lasting access. Start again with 'Connect Zoho Books' and accept the access.",
            detail="no refresh token",
        )
    check_scopes(body.get("scope"))
    return ZohoTokens(
        refresh_token=str(refresh),
        access_token=str(body["access_token"]),
        expires_in=int(body.get("expires_in") or 3600),
        api_domain=api_domain_for(region, body.get("api_domain")),
    )


def revoke(setup: ZohoSetup, http: ReadOnlyHttpClient, *, region: str, refresh_token: str) -> None:
    """Best effort: Zoho forgets the refresh token (and its access tokens)."""
    setup.tokens.drop(refresh_token)
    http.request("POST", setup.revoke_url(region), params={"token": refresh_token})


# ----------------------------------------------------------------------------------------- the quota
class ZohoQuota:
    """API calls per Zoho organisation: per UTC day (Zoho's daily limit) and per minute, counted in Redis."""

    def __init__(
        self,
        client: redis.Redis[bytes] | None,
        *,
        daily_limit: int,
        reserve_pct: float,
        per_minute: int,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None],
    ) -> None:
        self._redis = client
        self.daily_limit = daily_limit
        self._stop_at = max(1, int(daily_limit * (100.0 - reserve_pct) / 100.0))
        self._per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._local: dict[str, int] = {}

    def _day_key(self, organization_id: str) -> str:
        return f"zoho:calls:{organization_id}:{self._clock().astimezone(UTC):%Y-%m-%d}"

    def used(self, organization_id: str) -> int:
        key = self._day_key(organization_id)
        if self._redis is None:
            return self._local.get(key, 0)
        raw = self._redis.get(key)
        return int(raw) if raw else 0

    def near_limit(self, organization_id: str) -> bool:
        return self.used(organization_id) >= self._stop_at

    def exhausted(self, organization_id: str) -> None:
        """Zoho said the daily limit is reached: stop reading until tomorrow."""
        key = self._day_key(organization_id)
        if self._redis is None:
            self._local[key] = self.daily_limit
            return
        self._redis.set(key, self.daily_limit, ex=2 * 86_400)

    def take(self, organization_id: str) -> None:
        """Count one call, or refuse it when the day's share is used; wait when the minute's share is used."""
        if self.near_limit(organization_id):
            raise ConnectorError("rate_limited", QUOTA_MESSAGE, detail="daily quota", stale_ok=True)
        now = self._clock().astimezone(UTC)
        key = self._day_key(organization_id)
        if self._redis is None:
            self._local[key] = self._local.get(key, 0) + 1
            return
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 2 * 86_400)
        minute_key = f"zoho:minute:{organization_id}:{now:%Y%m%d%H%M}"
        pipe.incr(minute_key)
        pipe.expire(minute_key, 120)
        results = pipe.execute()
        if int(results[2]) > self._per_minute:
            self._sleep(60 - now.second + 1)


# ------------------------------------------------------------------------------------------ the tables
@dataclass(frozen=True)
class Field:
    name: str
    type: ColumnType
    money: bool = False


@dataclass(frozen=True)
class Table:
    ref: str
    path: str
    list_key: str
    id_field: str
    fields: tuple[Field, ...]
    dated: bool = False  # filtered by ``date_start`` for the history window
    incremental: bool = True  # Zoho filters by ``last_modified_time``


def _f(name: str, kind: ColumnType = "text", *, money: bool = False) -> Field:
    return Field(name, kind, money)


_TIMES = (_f("created_time", "datetime"), _f("last_modified_time", "datetime"))

INVOICES = Table(
    "invoices",
    "/invoices",
    "invoices",
    "invoice_id",
    (
        _f("invoice_id"),
        _f("invoice_number"),
        _f("reference_number"),
        _f("date", "date"),
        _f("due_date", "date"),
        _f("customer_id"),
        _f("customer_name"),
        _f("status"),
        _f("currency_code"),
        _f("total", "number", money=True),
        _f("balance", "number", money=True),
        *_TIMES,
    ),
    dated=True,
)
BILLS = Table(
    "bills",
    "/bills",
    "bills",
    "bill_id",
    (
        _f("bill_id"),
        _f("bill_number"),
        _f("reference_number"),
        _f("date", "date"),
        _f("due_date", "date"),
        _f("vendor_id"),
        _f("vendor_name"),
        _f("status"),
        _f("currency_code"),
        _f("total", "number", money=True),
        _f("balance", "number", money=True),
        *_TIMES,
    ),
    dated=True,
)
EXPENSES = Table(
    "expenses",
    "/expenses",
    "expenses",
    "expense_id",
    (
        _f("expense_id"),
        _f("date", "date"),
        _f("account_id"),
        _f("account_name"),
        _f("paid_through_account_name"),
        _f("vendor_id"),
        _f("vendor_name"),
        _f("reference_number"),
        _f("status"),
        _f("is_billable", "boolean"),
        _f("currency_code"),
        _f("total", "number", money=True),
        _f("bcy_total", "number", money=True),
        *_TIMES,
    ),
    dated=True,
)
CONTACTS = Table(
    "contacts",
    "/contacts",
    "contacts",
    "contact_id",
    (
        _f("contact_id"),
        _f("contact_name"),
        _f("company_name"),
        _f("contact_type"),
        _f("status"),
        _f("currency_code"),
        _f("outstanding_receivable_amount", "number", money=True),
        _f("outstanding_payable_amount", "number", money=True),
        *_TIMES,
    ),
)
ITEMS = Table(
    "items",
    "/items",
    "items",
    "item_id",
    (
        _f("item_id"),
        _f("name"),
        _f("sku"),
        _f("status"),
        _f("product_type"),
        _f("item_type"),
        _f("unit"),
        _f("rate", "number", money=True),
        _f("purchase_rate", "number", money=True),
        _f("stock_on_hand", "number"),
        *_TIMES,
    ),
)
CHART_OF_ACCOUNTS = Table(
    "chart_of_accounts",
    "/chartofaccounts",
    "chartofaccounts",
    "account_id",
    (
        _f("account_id"),
        _f("account_name"),
        _f("account_code"),
        _f("account_type"),
        _f("is_active", "boolean"),
        _f("parent_account_name"),
        _f("last_modified_time", "datetime"),
    ),
    incremental=False,
)
PROFIT_AND_LOSS = Table(
    "profit_and_loss",
    "/reports/profitandloss",
    "profit_and_loss",
    "line_id",
    (
        _f("line_id"),
        _f("month", "date"),
        _f("section"),
        _f("account_name"),
        _f("amount", "number", money=True),
    ),
)
TABLES: dict[str, Table] = {
    t.ref: t
    for t in (INVOICES, BILLS, EXPENSES, CONTACTS, ITEMS, CHART_OF_ACCOUNTS, PROFIT_AND_LOSS)
}
DATASET_NAMES = {
    "invoices": "invoices",
    "bills": "bills",
    "expenses": "expenses",
    "contacts": "contacts",
    "items": "items",
    "chart_of_accounts": "chart of accounts",
    "profit_and_loss": "profit and loss",
}
# The P&L report's groups that hold accounts; a line is filed under the nearest one above it.
PNL_SECTIONS = (
    "Operating Income",
    "Cost of Goods Sold",
    "Operating Expense",
    "Non Operating Income",
    "Non Operating Expense",
)


def _parse_time(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    text = str(value)
    # Zoho writes offsets as +0530; pandas wants +05:30.
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        stamp = pd.Timestamp(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.tz_localize("UTC")


def _column(values: list[Any], field_: Field, timezone: str) -> pd.Series:
    if field_.type == "datetime":
        stamps = [_parse_time(v) for v in values]
        parsed = pd.to_datetime(
            pd.Series(
                [s.tz_convert("UTC") if s is not None else None for s in stamps], dtype="object"
            ),
            utc=True,
            errors="coerce",
        )
        return parsed.dt.tz_convert(timezone).dt.tz_localize(None)
    if field_.type == "date":
        return pd.to_datetime(
            pd.Series([None if v in (None, "") else str(v)[:10] for v in values], dtype="object"),
            format="%Y-%m-%d",
            errors="coerce",
        )
    if field_.type == "number":
        return pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").astype("float64")
    if field_.type == "boolean":
        return (
            pd.Series(values, dtype="object")
            .map(lambda v: None if v in (None, "") else bool(v))
            .astype("boolean")
        )
    return pd.Series([None if v in (None, "") else str(v) for v in values], dtype="string")


def to_frame(table: Table, rows: list[dict[str, Any]], timezone: str) -> pd.DataFrame:
    return pd.DataFrame(
        {f.name: _column([r.get(f.name) for r in rows], f, timezone) for f in table.fields}
    )


def column_infos(table: Table, frame: pd.DataFrame, currency: str | None) -> list[ColumnInfo]:
    rupees = currency in (None, "INR")
    return [
        ColumnInfo(
            name=f.name,
            label=f.name,
            type=f.type,
            # Zoho Books sends rupees (decimals), not paise: the unit is known, nothing is divided (D95, D98).
            unit="INR" if f.money and rupees else None,
            stored_in="rupees" if f.money and rupees else None,
            currencies=(currency,) if f.money and not rupees and currency else (),
            date_format="ISO 8601" if f.type in ("date", "datetime") else None,
            null_count=int(frame[f.name].isna().sum()),
        )
        for f in table.fields
    ]


def _month_starts(first: date, last: date) -> list[date]:
    months = []
    current = first.replace(day=1)
    while current <= last:
        months.append(current)
        current = (current + timedelta(days=32)).replace(day=1)
    return months


def _month_end(start: date) -> date:
    return (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def pnl_lines(report: Any, month: date) -> list[dict[str, Any]]:
    """Flatten Zoho's nested profit and loss report into one row per account line of one month."""
    rows: list[dict[str, Any]] = []

    def walk(nodes: Any, section: str | None) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or node.get("account_name") or "").strip()
            children = node.get("account_transactions")
            here = name if name in PNL_SECTIONS else section
            if isinstance(children, list) and children:
                walk(children, here)
                continue
            if here is None or not name or name in PNL_SECTIONS:
                continue
            try:
                amount = float(node.get("total") or 0)
            except (TypeError, ValueError):
                continue
            if amount == 0:
                continue
            rows.append(
                {
                    "line_id": f"{month:%Y-%m}:{here}:{name}",
                    "month": month.isoformat(),
                    "section": here,
                    "account_name": name,
                    "amount": amount,
                }
            )

    walk(report, None)
    return rows


def _zoho_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


# ------------------------------------------------------------------------------------------ connector
class ZohoBooksConnector:
    type = "zoho_books"

    def __init__(
        self,
        *,
        setup: ZohoSetup,
        refresh_token: str,
        region: str,
        organization_id: str,
        timezone: str,
        max_rows: int,
        clock: Callable[[], datetime] | None = None,
        quota: ZohoQuota | None = None,
        currency: str | None = "INR",
        access_token: str | None = None,
        api_domain: str | None = None,
    ) -> None:
        if region not in REGIONS:
            raise ConnectorError("needs_reauth", REAUTH_MESSAGE, detail="unknown region")
        self._setup = setup
        self._refresh = refresh_token
        self._region = region
        self._api = api_domain_for(region, api_domain)
        self.organization_id = organization_id
        self._timezone = timezone
        self._max_rows = max_rows
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = setup.sleep or time.sleep
        self._quota = quota or ZohoQuota(
            None,
            daily_limit=setup.daily_call_limit,
            reserve_pct=setup.daily_reserve_pct,
            per_minute=setup.per_minute_limit,
            clock=self._clock,
            sleep=self._sleep,
        )
        self._currency = currency
        self._http = setup.http(region)
        if access_token:
            setup.tokens.put(
                refresh_token, access_token, self._clock() + timedelta(hours=1) - TOKEN_SAFETY
            )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------------------------ tokens
    def _access_token(self, *, renew: bool = False) -> str:
        now = self._clock()
        if renew:
            self._setup.tokens.drop(self._refresh)
        cached = self._setup.tokens.get(self._refresh, now)
        if cached:
            return cached
        try:
            response = self._http.post_form(
                self._setup.token_url(self._region),
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh,
                    "client_id": self._setup.client_id,
                    "client_secret": self._setup.client_secret,
                },
            )
        except httpx.HTTPError as error:
            raise ConnectorError(
                "unavailable",
                "Zoho could not be reached. Try again in a few minutes.",
                detail=type(error).__name__,
            ) from error
        try:
            body = _token_body(response, what="token refresh")
        except ConnectorError as error:
            if error.kind == "needs_reauth":
                raise ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=error.detail) from error
            raise
        token = str(body["access_token"])
        lifetime = timedelta(seconds=int(body.get("expires_in") or 3600))
        self._setup.tokens.put(self._refresh, token, now + lifetime - TOKEN_SAFETY)
        return token

    # ------------------------------------------------------------------------------------ requests
    def _get(
        self, path: str, params: dict[str, Any] | None = None, *, organization: bool = True
    ) -> dict[str, Any]:
        url = f"{self._api}/books/v3{path}"
        query = dict(params or {})
        if organization:
            query["organization_id"] = self.organization_id
        renewed = False
        while True:
            self._quota.take(self.organization_id or "_account")
            headers = {"Authorization": f"Zoho-oauthtoken {self._access_token(renew=renewed)}"}
            try:
                response = self._http.get(url, params=query, headers=headers)
            except httpx.HTTPError as error:
                raise ConnectorError(
                    "unavailable",
                    "Zoho Books could not be reached. Try again in a few minutes.",
                    detail=type(error).__name__,
                ) from error
            if response.status_code == 401 and not renewed:
                renewed = True  # the access token expired early or was revoked: get a new one once
                continue
            return self._body(response, path)

    def _body(self, response: httpx.Response, path: str) -> dict[str, Any]:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        code = body.get("code") if isinstance(body, dict) else None
        message = str(body.get("message", "")) if isinstance(body, dict) else ""
        detail = f"{path}: HTTP {status}, code {code}"
        if status == 200 and isinstance(body, dict) and code in (0, None):
            return body
        if status == 429 or "limit" in message.lower():
            if "day" in message.lower() or "daily" in message.lower():
                self._quota.exhausted(self.organization_id)
                raise ConnectorError("rate_limited", QUOTA_MESSAGE, detail=detail, stale_ok=True)
            raise ConnectorError(
                "rate_limited",
                "Zoho Books is limiting requests. Try again in a few minutes.",
                detail=detail,
                stale_ok=True,
            )
        if status == 401:
            raise ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=detail)
        if status == 403 or code == 57:
            raise ConnectorError(
                "invalid",
                "Zoho Books did not allow reading this data with the access given. Sign in to Zoho again and "
                "accept every read permission the app asks for.",
                detail=detail,
            )
        if status == 404:
            raise ConnectorError(
                "not_found",
                "Zoho Books does not have this data for the organisation.",
                detail=detail,
            )
        if status == 400:
            raise ConnectorError(
                "invalid", "Zoho Books refused the request for this data.", detail=detail
            )
        raise ConnectorError(
            "unavailable",
            "Zoho Books is not answering properly. Try again in a few minutes.",
            detail=detail,
        )

    # --------------------------------------------------------------------------------- organisations
    def organizations(self) -> list[dict[str, Any]]:
        """The organisations the account can see: id, name, currency and whether it is the default. Zoho also
        sends the organisation's contact person and e-mail; they are dropped here (D98)."""
        body = self._get("/organizations", organization=False)
        out = []
        for item in body.get("organizations") or []:
            if not isinstance(item, dict) or not item.get("organization_id"):
                continue
            out.append(
                {
                    "id": str(item["organization_id"]),
                    "name": str(item.get("name") or "")[:120],
                    "currency": str(item.get("currency_code") or "") or None,
                    "default": bool(item.get("is_default_org")),
                }
            )
        return out

    # --------------------------------------------------------------------------------- the interface
    def test(self) -> ConnectionTestResult:
        checks: list[TestCheck] = []
        try:
            organizations = self.organizations()
        except ConnectorError as error:
            checks.append(TestCheck("signed_in", error.kind != "needs_reauth"))
            checks.append(TestCheck("invoices_readable", False))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        mine = next((o for o in organizations if o["id"] == self.organization_id), None)
        checks += [TestCheck("signed_in", True), TestCheck("read_only", True)]
        if mine is None:
            checks.append(TestCheck("organization", False))
            return ConnectionTestResult(
                ok=False,
                message="This Zoho account can no longer see the organisation. Choose another organisation or "
                "sign in with an account that can see it.",
                checks=checks,
            )
        checks.append(TestCheck("organization", True))
        try:
            self._get("/invoices", {"page": 1, "per_page": 1})
        except ConnectorError as error:
            checks.append(TestCheck("invoices_readable", False))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        checks.append(TestCheck("invoices_readable", True))
        used = self._quota.used(self.organization_id)
        return ConnectionTestResult(
            ok=True,
            message=f"Zoho Books accepted the access to {mine['name']}. This platform only reads.",
            checks=checks,
            details={
                "organization_name": mine["name"],
                "currency": mine["currency"],
                "calls_today": used,
                "daily_limit": self._quota.daily_limit,
            },
        )

    def list_datasets(self) -> list[DatasetInfo]:
        return [DatasetInfo(ref=t.ref, name=DATASET_NAMES[t.ref]) for t in TABLES.values()]

    def source_version(self, dataset: DatasetInfo) -> str | None:
        return None  # Zoho cannot say "nothing changed"; the cursor makes the next read small

    def supports_incremental(self) -> bool:
        return True

    def supports_pushdown(self) -> bool:
        return False

    def use_organization(self, organization_id: str, currency: str | None) -> None:
        self.organization_id = organization_id
        self._currency = currency

    def calls_today(self) -> int:
        return self._quota.used(self.organization_id)

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        table = TABLES.get(dataset.ref)
        if table is None:
            raise ConnectorError("not_found", "This Zoho Books table does not exist.")
        if self._quota.near_limit(self.organization_id):
            raise ConnectorError("rate_limited", QUOTA_MESSAGE, detail="daily quota", stale_ok=True)
        cursor: str | None
        if table is PROFIT_AND_LOSS:
            rows, cursor, incremental = self._read_pnl(since_cursor)
            truncated = False
        else:
            since = _parse_cursor(since_cursor) if table.incremental else None
            incremental = since is not None
            rows, truncated = self._read_list(table, since)
            newest: str | None = max(
                (str(r["last_modified_time"]) for r in rows if r.get("last_modified_time")),
                default=None,
                key=_sort_time,
            )
            cursor = newest or since_cursor
        frame = to_frame(table, rows, self._timezone)
        return FetchResult(
            frame=frame,
            columns=column_infos(table, frame, self._currency),
            source_version=None,
            cursor=cursor,
            incremental=incremental,
            key_column=table.id_field,
            truncated=truncated,
        )

    # ------------------------------------------------------------------------------------- reading
    def _pages(self, table: Table, params: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
        page = 1
        while page <= MAX_PAGES_PER_FETCH:
            body = self._get(table.path, {**params, "page": page, "per_page": PAGE_SIZE})
            items = body.get(table.list_key) or []
            if not isinstance(items, list):
                raise ConnectorError(
                    "unavailable", "Zoho Books sent an answer that could not be read."
                )
            yield [i for i in items if isinstance(i, dict)]
            context = body.get("page_context") or {}
            if not (isinstance(context, dict) and context.get("has_more_page")):
                return
            page += 1

    def _read_list(self, table: Table, since: datetime | None) -> tuple[list[dict[str, Any]], bool]:
        params: dict[str, Any] = {}
        if since is not None:
            params["last_modified_time"] = _zoho_time(since - OVERLAP)
        elif table.dated:
            start = self._clock() - timedelta(days=self._setup.history_days)
            params["date_start"] = start.date().isoformat()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        wanted = {f.name for f in table.fields}
        for page in self._pages(table, params):
            for item in page:
                key = str(item.get(table.id_field) or "")
                if not key or key in seen:
                    continue  # a row that moved between pages while we were reading
                seen.add(key)
                # Only the fields the table declares are kept: nothing personal slips in (D98).
                rows.append({k: v for k, v in item.items() if k in wanted})
                if len(rows) >= self._max_rows:
                    return rows, True
        return rows, False

    def _read_pnl(self, since_cursor: str | None) -> tuple[list[dict[str, Any]], str, bool]:
        today = self._clock().astimezone(UTC).date()
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        for _ in range(max(0, self._setup.report_months - 2)):
            first = (first - timedelta(days=1)).replace(day=1)
        incremental = False
        if since_cursor and re.fullmatch(r"\d{4}-\d{2}", since_cursor):
            start = date.fromisoformat(f"{since_cursor}-01")
            for _ in range(REPORT_LOOKBACK_MONTHS - 1):
                start = (start - timedelta(days=1)).replace(day=1)
            first = max(first, start)
            incremental = True
        rows: list[dict[str, Any]] = []
        months = _month_starts(first, today)
        for month in months:
            try:
                body = self._get(
                    PROFIT_AND_LOSS.path,
                    {"from_date": month.isoformat(), "to_date": _month_end(month).isoformat()},
                )
            except ConnectorError as error:
                # A refusal of the report alone (the token itself was just renewed) must not mark the whole
                # connection as needing a new sign-in.
                if error.kind in ("needs_reauth", "invalid") and error.detail.startswith(
                    "/reports"
                ):
                    raise ConnectorError("invalid", REPORT_REFUSED, detail=error.detail) from error
                raise
            rows.extend(pnl_lines(body.get("profit_and_loss"), month))
        return rows, f"{months[-1]:%Y-%m}" if months else (since_cursor or ""), incremental


def _sort_time(value: str) -> pd.Timestamp:
    return _parse_time(value) or pd.Timestamp(0, tz="UTC")


def _parse_cursor(cursor: str | None) -> datetime | None:
    stamp = _parse_time(cursor) if cursor else None
    return stamp.to_pydatetime() if stamp is not None else None
