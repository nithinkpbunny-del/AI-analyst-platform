"""Request and response bodies for connections, datasets, uploads, Google Sheets and access (M6).

No response contains a credential. The one exception by design is the short-lived Google access token for the
Picker (``PickerOut``), which the Picker needs in the browser and which only Owners receive.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.schemas.auth import _Strict

ConnectionTypeName = Literal[
    "file", "google_sheets", "razorpay", "shopify", "zoho_books", "sql_database"
]
StatusName = Literal["active", "error", "needs_reauth", "disabled"]
SyncStateName = Literal["idle", "queued", "running"]
AccessName = Literal["all", "selected"]
SPREADSHEET_ID = r"^[A-Za-z0-9_-]{8,128}$"


class QualityOut(BaseModel):
    kind: str
    severity: Literal["warning", "info"]
    message: str
    column: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ColumnOut(BaseModel):
    name: str
    label: str
    type: str
    unit: str | None = None
    date_format: str | None = None


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    row_count: int | None
    columns: list[ColumnOut]
    last_fetched_at: datetime | None
    quality: list[QualityOut]
    last_error: str | None


class FileOut(BaseModel):
    version: int
    original_name: str
    size_bytes: int
    uploaded_at: datetime
    # The newest upload could not be read; the version above is still in use.
    latest_upload_failed: bool
    latest_upload_error: str | None
    # The newest upload differs from the version in use and waits for an Owner (DECISIONS D58).
    latest_upload_needs_review: bool = False
    latest_upload_version: int | None = None


class FileVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    original_name: str
    size_bytes: int
    status: Literal["processing", "ready", "failed", "needs_confirmation"]
    error: str | None
    uploaded_at: datetime
    uploaded_by_name: str | None
    in_use: bool
    # For needs_confirmation: how it differs from the version in use (kind: sheet_missing, sheet_empty,
    # columns_missing, type_changed; with sheet, columns, count, column, from, to, rows_before).
    changes: list[dict[str, Any]] = Field(default_factory=list)


class SpreadsheetOut(BaseModel):
    id: str
    name: str


class GoogleOut(BaseModel):
    account_email: str | None
    spreadsheets: list[SpreadsheetOut]


class RazorpayOut(BaseModel):
    key_id_masked: str
    mode: Literal["live", "test"]


class ShopifyOut(BaseModel):
    shop: str  # the store's *.myshopify.com address (not a secret)
    shop_name: str | None
    currency: str | None
    # Shopify's "read_all_orders" access: without it only the last 60 days of orders can be read.
    all_orders: bool


class ZohoOrganizationOut(BaseModel):
    id: str
    name: str


class ZohoOut(BaseModel):
    region: str  # Zoho's data centre ("in", "com", "eu", ...)
    region_name: str
    organization_id: str
    organization_name: str | None
    currency: str | None
    # Other organisations the Zoho account can see (the Owner may switch to one of them).
    organizations: list[ZohoOrganizationOut]
    # Zoho's daily API calls for the organisation, as counted by this platform (D97). Only on the detail.
    calls_today: int | None = None
    daily_limit: int


class DatabaseOut(BaseModel):
    engine: Literal["postgresql", "mysql"]
    host: str
    port: int
    database: str
    username_masked: str  # the password is never returned
    ssl_mode: Literal["verify-full", "require", "disable"]
    tables: list[str]  # the exposed tables ("schema.table" on PostgreSQL)
    # The connection check found that the database user can change data (D100); the session is read-only anyway.
    can_write: bool
    write_reasons: list[str]
    server_version: str | None


class ConnectionOut(BaseModel):
    id: uuid.UUID
    type: ConnectionTypeName
    name: str
    status: StatusName
    sync_state: SyncStateName
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime
    created_by_name: str | None
    datasets_count: int
    rows_total: int
    warnings_count: int
    file: FileOut | None = None
    google: GoogleOut | None = None
    razorpay: RazorpayOut | None = None
    shopify: ShopifyOut | None = None
    zoho: ZohoOut | None = None
    database: DatabaseOut | None = None


class DictionaryMoveOut(BaseModel):
    table: str
    to: str  # the connection whose same-named table takes the metrics over
    metrics: int


class DictionaryImpactOut(BaseModel):
    """What removing this connection does to the business dictionary (warning before removal, D64)."""

    moves: list[DictionaryMoveOut]
    orphaned_metrics: list[str]
    joins: int


class ConnectionDetailOut(ConnectionOut):
    datasets: list[DatasetOut]
    versions: list[FileVersionOut]
    dictionary_impact: DictionaryImpactOut | None = None


class GoogleSourceOut(BaseModel):
    available: bool
    stand_in: bool


class RazorpaySourceOut(BaseModel):
    available: bool
    stand_in: bool


class ShopifySourceOut(BaseModel):
    available: bool
    stand_in: bool


class ZohoSourceOut(BaseModel):
    available: bool
    stand_in: bool
    region: str


class DatabaseSourceOut(BaseModel):
    available: bool
    # Development only: SSL may be turned off, and these demo database hosts may be private addresses.
    ssl_off_allowed: bool
    demo_hosts: list[str]


class SourcesOut(BaseModel):
    upload_max_mb: int
    google_sheets: GoogleSourceOut
    razorpay: RazorpaySourceOut
    shopify: ShopifySourceOut
    zoho_books: ZohoSourceOut
    database: DatabaseSourceOut


class ConnectionList(BaseModel):
    connections: list[ConnectionOut]
    sources: SourcesOut


class ConnectionPatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    paused: bool | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a name.")
        return value


class TestCheckOut(BaseModel):
    label: str
    ok: bool


class TestOut(BaseModel):
    ok: bool
    message: str
    checks: list[TestCheckOut]


class PreviewOut(BaseModel):
    dataset_id: uuid.UUID
    columns: list[ColumnOut]
    rows: list[list[Any]]
    row_count: int
    fetched_at: datetime
    checked_at: datetime
    source: Literal["cache", "unchanged", "incremental", "full"]
    quality: list[QualityOut]


# ----------------------------------------------------------------------------------------------- access
class PersonAccessOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    email: str
    role: Literal["owner", "analyst", "viewer"]
    access: Literal["owner", "all", "selected"]
    connection_ids: list[uuid.UUID]


class AccessList(BaseModel):
    people: list[PersonAccessOut]


class AccessRequest(_Strict):
    access: AccessName
    connection_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


# ----------------------------------------------------------------------------------------------- Google
class GoogleStartRequest(_Strict):
    connection_id: uuid.UUID | None = None  # set to sign in again for an existing connection


class GoogleStartOut(BaseModel):
    authorization_url: str


class GoogleFinishRequest(_Strict):
    state: str = Field(min_length=16, max_length=200)
    code: str = Field(min_length=1, max_length=2048)


class RazorpayKeyRequest(_Strict):
    """A Razorpay API key. The secret is write-only: it is checked, encrypted and never returned."""

    key_id: str = Field(min_length=10, max_length=64)
    key_secret: SecretStr = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)

    @field_validator("key_id")
    @classmethod
    def _key_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"rzp_(live|test)_[A-Za-z0-9]{4,}", value):
            raise ValueError("The key id starts with rzp_live_ or rzp_test_.")
        return value

    @field_validator("key_secret")
    @classmethod
    def _secret(cls, value: SecretStr) -> SecretStr:
        text = value.get_secret_value().strip()
        if not text.isascii() or " " in text:
            raise ValueError("The key secret has no spaces.")
        return SecretStr(text)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class PickerOut(BaseModel):
    access_token: str
    expires_in: int
    developer_key: str
    app_id: str
    stand_in: bool
    stand_in_files: list[SpreadsheetOut]


class GoogleFinishOut(BaseModel):
    connection: ConnectionOut
    picker: PickerOut
    reconnected: bool


class SpreadsheetsRequest(_Strict):
    spreadsheet_ids: list[str] = Field(max_length=20)

    @field_validator("spreadsheet_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            if not re.fullmatch(SPREADSHEET_ID, item):
                raise ValueError("This is not a Google spreadsheet id.")
            if item not in out:
                out.append(item)
        return out


class ShopifyStartRequest(_Strict):
    """Start connecting a Shopify store (``shop``), or signing in again for a connection (``connection_id``)."""

    shop: str | None = Field(default=None, max_length=200)
    connection_id: uuid.UUID | None = None


class ShopifyStartOut(BaseModel):
    authorization_url: str


class ShopifyFinishRequest(_Strict):
    state: str = Field(min_length=16, max_length=200)
    code: str = Field(min_length=1, max_length=2048)


class ShopifyFinishOut(BaseModel):
    connection: ConnectionOut
    reconnected: bool


class ZohoStartRequest(_Strict):
    """Start connecting Zoho Books, or signing in again for a connection (``connection_id``)."""

    connection_id: uuid.UUID | None = None


class ZohoStartOut(BaseModel):
    authorization_url: str


class ZohoFinishRequest(_Strict):
    state: str = Field(min_length=16, max_length=200)
    code: str = Field(min_length=1, max_length=2048)


class ZohoFinishOut(BaseModel):
    connection: ConnectionOut
    reconnected: bool


class ZohoOrganizationRequest(_Strict):
    organization_id: str = Field(pattern=r"^[0-9]{1,30}$")


class DatabaseRequest(_Strict):
    """Connection details of a customer database. The password is write-only (stored encrypted)."""

    engine: Literal["postgresql", "mysql"]
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)
    ssl_mode: Literal["verify-full", "require", "disable"] = "require"
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("host", "database", "username")
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        value = value.strip()
        if not value or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Remove spaces and special characters at the ends.")
        return value


class DatabaseTableOut(BaseModel):
    ref: str
    name: str
    kind: Literal["table", "view"]
    rows_estimate: int | None
    chosen: bool


class DatabaseTablesOut(BaseModel):
    tables: list[DatabaseTableOut]
    max_tables: int


class DatabaseTablesRequest(_Strict):
    tables: list[str] = Field(max_length=100)

    @field_validator("tables")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            if not item or len(item) > 300 or re.search(r"[\x00-\x1f\x7f]", item):
                raise ValueError("This is not a table name.")
            if item not in out:
                out.append(item)
        return out
