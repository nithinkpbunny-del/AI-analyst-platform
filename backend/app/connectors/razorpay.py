"""The Razorpay connector (BUILD_SPEC 8.4): payments, orders, refunds and settlements, read-only.

* **Credentials**: a key id (``rzp_live_...`` or ``rzp_test_...``, kept in the connection's config, it is not a
  secret) and the key secret (encrypted in the secret store). Basic auth. Razorpay keys can make changes; this
  connector sends ``GET`` requests only (``ReadOnlyHttpClient`` with no allowed POSTs), and the web app tells the
  Owner so and recommends a dedicated key (DECISIONS D89).
* **Paging**: ``count`` (at most 100) and ``skip``, in time windows (``from``/``to`` as Unix seconds) of
  ``WINDOW_DAYS``, newest window first, so deep ``skip`` values are never needed and a row cap drops the oldest
  rows.
* **Money stays in paise** exactly as Razorpay sends it (``amount``, ``fee``, ``tax``, ``amount_refunded`` ...).
  The business dictionary says which columns are paise and divides by 100 (``app.dictionary.units``), the same
  way it does for the demo ``razorpay_payments.csv``.
* **Incremental**: the cursor is the newest ``created_at`` seen. A payment changes after it is created (authorized
  -> captured -> refunded), so the next fetch starts ``lookback_days`` before the cursor and the rows it returns
  replace their earlier copies by ``id`` (DECISIONS D90).
* **Personal data**: customer e-mail, phone, card and bank details are never read into a table (data
  minimisation); only what an analysis needs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import pandas as pd

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

API_BASE = "https://api.razorpay.com/v1"
PAGE_SIZE = 100
WINDOW_DAYS = 31
HTTP_TIMEOUT_SECONDS = 30.0
MAX_PAGES_PER_FETCH = 20_000  # a runaway guard; the row cap normally stops a fetch first
KEY_ID_PREFIXES = ("rzp_live_", "rzp_test_")

Mode = Literal["live", "test"]


def key_mode(key_id: str) -> Mode | None:
    if key_id.startswith("rzp_live_"):
        return "live"
    if key_id.startswith("rzp_test_"):
        return "test"
    return None


def valid_key_id(key_id: str) -> bool:
    return key_mode(key_id) is not None and len(key_id) <= 64 and key_id.isascii()


def mask_key_id(key_id: str) -> str:
    """``rzp_live_AbCd••••••``: enough to recognise the key, not enough to use it for anything."""
    prefix = next((p for p in KEY_ID_PREFIXES if key_id.startswith(p)), "")
    head = key_id[: len(prefix) + 4]
    return f"{head}••••"


@dataclass(frozen=True)
class RazorpaySetup:
    """Where the API is. Tests and the development stand-in replace the transport; nothing else differs."""

    base_url: str = API_BASE
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] | None = None
    history_days: int = 365
    lookback_days: int = 30
    # Keys whose id starts with this are answered by ``transport`` (the stand-in); others go to Razorpay.
    stand_in_prefix: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def http(self, key_id: str = "") -> ReadOnlyHttpClient:
        use_stand_in = bool(
            self.transport is not None
            and (self.stand_in_prefix is None or key_id.startswith(self.stand_in_prefix))
        )
        client = httpx.Client(
            transport=self.transport if use_stand_in else None, timeout=HTTP_TIMEOUT_SECONDS
        )
        kwargs: dict[str, Any] = {}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return ReadOnlyHttpClient(client, **kwargs)


def default_setup(settings: Settings) -> RazorpaySetup:
    """Real Razorpay for every key; in development, keys starting ``rzp_test_demo`` use the local stand-in."""
    common: dict[str, Any] = {
        "history_days": settings.razorpay_history_days,
        "lookback_days": settings.razorpay_refresh_lookback_days,
    }
    if settings.app_env == "development":
        from app.connectors.razorpay_stand_in import STAND_IN_PREFIX, StandInRazorpayTransport

        return RazorpaySetup(
            transport=StandInRazorpayTransport(), stand_in_prefix=STAND_IN_PREFIX, **common
        )
    return RazorpaySetup(**common)


# ------------------------------------------------------------------------------------- the four tables
@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: ColumnType
    source: str | None = None  # key in Razorpay's JSON when it differs from ``name``
    paise: bool = False  # a money amount, whole paise


@dataclass(frozen=True)
class Spec:
    ref: str
    name: str
    path: str
    fields: tuple[Field, ...]


PAYMENTS = Spec(
    "payments",
    "payments",
    "/payments",
    (
        Field("id", "id", "text"),
        Field("entity", "entity", "text"),
        Field("amount", "amount", "integer", paise=True),
        Field("currency", "currency", "text"),
        Field("status", "status", "text"),
        Field("order_id", "order_id", "text"),
        Field("method", "method", "text"),
        Field("captured", "captured", "boolean"),
        Field("description", "description", "text"),
        Field("amount_refunded", "amount_refunded", "integer", paise=True),
        Field("refund_status", "refund_status", "text"),
        Field("fee", "fee", "integer", paise=True),
        Field("tax", "tax", "integer", paise=True),
        Field("international", "international", "boolean"),
        Field("error_code", "error_code", "text"),
        Field("created_at", "created_at", "datetime"),
    ),
)
ORDERS = Spec(
    "orders",
    "orders",
    "/orders",
    (
        Field("id", "id", "text"),
        Field("entity", "entity", "text"),
        Field("amount", "amount", "integer", paise=True),
        Field("amount_paid", "amount_paid", "integer", paise=True),
        Field("amount_due", "amount_due", "integer", paise=True),
        Field("currency", "currency", "text"),
        Field("receipt", "receipt", "text"),
        Field("status", "status", "text"),
        Field("attempts", "attempts", "integer"),
        Field("created_at", "created_at", "datetime"),
    ),
)
REFUNDS = Spec(
    "refunds",
    "refunds",
    "/refunds",
    (
        Field("id", "id", "text"),
        Field("entity", "entity", "text"),
        Field("amount", "amount", "integer", paise=True),
        Field("currency", "currency", "text"),
        Field("payment_id", "payment_id", "text"),
        Field("status", "status", "text"),
        Field("speed_processed", "speed_processed", "text"),
        Field("created_at", "created_at", "datetime"),
    ),
)
SETTLEMENTS = Spec(
    "settlements",
    "settlements",
    "/settlements",
    (
        Field("id", "id", "text"),
        Field("entity", "entity", "text"),
        Field("amount", "amount", "integer", paise=True),
        Field("status", "status", "text"),
        Field("fees", "fees", "integer", paise=True),
        Field("tax", "tax", "integer", paise=True),
        Field("utr", "utr", "text"),
        Field("created_at", "created_at", "datetime"),
    ),
)
SPECS: dict[str, Spec] = {s.ref: s for s in (PAYMENTS, ORDERS, REFUNDS, SETTLEMENTS)}


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _column(series: list[Any], field_: Field, timezone: str) -> pd.Series:
    if field_.type == "datetime":
        seconds = pd.to_numeric(pd.Series(series, dtype="object"), errors="coerce")
        parsed = pd.to_datetime(seconds, unit="s", utc=True)
        return parsed.dt.tz_convert(timezone).dt.tz_localize(None)
    if field_.type == "integer":
        numbers = pd.to_numeric(pd.Series(series, dtype="object"), errors="coerce")
        return numbers.round().astype("Int64")
    if field_.type == "boolean":
        return (
            pd.Series(series, dtype="object")
            .map(lambda v: None if v is None else bool(v))
            .astype("boolean")
        )
    return pd.Series([None if v is None else str(v) for v in series], dtype="string")


def to_frame(spec: Spec, items: list[dict[str, Any]], timezone: str) -> pd.DataFrame:
    data = {
        f.name: _column([item.get(f.source or f.name) for item in items], f, timezone)
        for f in spec.fields
    }
    return pd.DataFrame(data)


def column_infos(spec: Spec, frame: pd.DataFrame) -> list[ColumnInfo]:
    infos = []
    for f in spec.fields:
        series = frame[f.name]
        infos.append(
            ColumnInfo(
                name=f.name,
                label=f.label,
                type=f.type,
                unit=None,  # rupees vs paise is the dictionary's decision (units.py) ...
                stored_in="paise"
                if f.paise
                else None,  # ... and Razorpay says which columns are paise
                date_format="unix time" if f.type == "datetime" else None,
                null_count=int(series.isna().sum()),
            )
        )
    return infos


# ------------------------------------------------------------------------------------------ connector
class RazorpayConnector:
    type = "razorpay"

    def __init__(
        self,
        *,
        setup: RazorpaySetup,
        key_id: str,
        key_secret: str,
        timezone: str,
        max_rows: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._setup = setup
        self._key_id = key_id
        self._auth = httpx.BasicAuth(key_id, key_secret)
        self._timezone = timezone
        self._max_rows = max_rows
        self._clock = clock or (lambda: datetime.now(UTC))
        self._http = setup.http(key_id)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------------------------ requests
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._setup.base_url.rstrip('/')}{path}"
        try:
            response = self._http.request("GET", url, params=params, auth=self._auth)
        except httpx.HTTPError as error:
            raise ConnectorError(
                "unavailable",
                "Razorpay could not be reached. Try again in a few minutes.",
                detail=type(error).__name__,
            ) from error
        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError as error:
                raise ConnectorError(
                    "unavailable", "Razorpay sent an answer that could not be read."
                ) from error
            if not isinstance(body, dict):
                raise ConnectorError(
                    "unavailable", "Razorpay sent an answer that could not be read."
                )
            return body
        detail = f"HTTP {status}"
        if status in (401, 403):
            raise ConnectorError(
                "needs_reauth",
                "Razorpay did not accept the API key. Enter the key id and secret again.",
                detail=detail,
            )
        if status == 404:
            raise ConnectorError(
                "not_found", "Razorpay does not have this data for the account.", detail=detail
            )
        if status == 429:
            raise ConnectorError(
                "rate_limited",
                "Razorpay is limiting requests. Try again in a few minutes.",
                detail=detail,
            )
        if status == 400:
            raise ConnectorError(
                "invalid", "Razorpay refused the request for this data.", detail=detail
            )
        raise ConnectorError(
            "unavailable",
            "Razorpay is not answering properly. Try again in a few minutes.",
            detail=detail,
        )

    # --------------------------------------------------------------------------------- the interface
    def test(self) -> ConnectionTestResult:
        checks: list[TestCheck] = []
        try:
            self._get("/payments", {"count": 1})
        except ConnectorError as error:
            checks.append(TestCheck("signed_in", error.kind != "needs_reauth"))
            checks.append(TestCheck("payments_readable", False))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        checks += [
            TestCheck("signed_in", True),
            TestCheck("read_only", True),
            TestCheck("payments_readable", True),
        ]
        mode = key_mode(self._key_id)
        label = "test mode (no real money)" if mode == "test" else "live mode"
        return ConnectionTestResult(
            ok=True,
            message=f"Razorpay accepted the key ({label}). This platform only reads.",
            checks=checks,
            details={"mode": mode},
        )

    def list_datasets(self) -> list[DatasetInfo]:
        return [DatasetInfo(ref=s.ref, name=s.name) for s in SPECS.values()]

    def source_version(self, dataset: DatasetInfo) -> str | None:
        return None  # Razorpay cannot say "nothing changed"; the cursor makes the next read small

    def supports_incremental(self) -> bool:
        return True

    def supports_pushdown(self) -> bool:
        return False

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        spec = SPECS.get(dataset.ref)
        if spec is None:
            raise ConnectorError("not_found", "This Razorpay table does not exist.")
        now = self._clock()
        incremental = False
        start = now - timedelta(days=self._setup.history_days)
        if since_cursor:
            try:
                since = datetime.fromtimestamp(int(since_cursor), UTC)
            except (ValueError, OverflowError, OSError):
                since = None
            if since is not None:
                start = max(start, since - timedelta(days=self._setup.lookback_days))
                incremental = True
        items, truncated = self._read_windows(spec, start, now)
        frame = to_frame(spec, items, self._timezone)
        newest = max(
            (int(i["created_at"]) for i in items if i.get("created_at") is not None), default=None
        )
        cursor = str(newest) if newest is not None else since_cursor
        return FetchResult(
            frame=frame,
            columns=column_infos(spec, frame),
            source_version=None,
            cursor=cursor,
            incremental=incremental,
            key_column="id",
            truncated=truncated,
        )

    # ------------------------------------------------------------------------------------- reading
    def _read_windows(
        self, spec: Spec, start: datetime, end: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Rows created in [start, end], newest window first; stops at the row cap (``truncated``)."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        window_end = end
        pages = 0
        while window_end > start:
            window_start = max(start, window_end - timedelta(days=WINDOW_DAYS))
            skip = 0
            while True:
                pages += 1
                if pages > MAX_PAGES_PER_FETCH:
                    return items, True
                body = self._get(
                    spec.path,
                    {
                        "count": PAGE_SIZE,
                        "skip": skip,
                        "from": _unix(window_start),
                        "to": _unix(window_end),
                    },
                )
                page = body.get("items") or []
                if not isinstance(page, list):
                    raise ConnectorError(
                        "unavailable", "Razorpay sent an answer that could not be read."
                    )
                for item in page:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("id", ""))
                    if key and key in seen:
                        continue  # a row that moved between pages while we were reading
                    seen.add(key)
                    items.append(item)
                if len(items) >= self._max_rows:
                    return items[: self._max_rows], True
                if len(page) < PAGE_SIZE:
                    break
                skip += PAGE_SIZE
            window_end = window_start - timedelta(seconds=1)
        return items, False
