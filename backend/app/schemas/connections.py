"""Request and response bodies for connections, datasets, uploads, Google Sheets and access (M6).

No response contains a credential. The one exception by design is the short-lived Google access token for the
Picker (``PickerOut``), which the Picker needs in the browser and which only Owners receive.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import _Strict

ConnectionTypeName = Literal["file", "google_sheets"]
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


class SourcesOut(BaseModel):
    upload_max_mb: int
    google_sheets: GoogleSourceOut


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
