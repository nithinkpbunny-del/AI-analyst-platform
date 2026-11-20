"""Requests and answers of the Tally bridge API (``/bridge/tally``) and the Owner's Tally routes."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.tally import SENT_DATASETS

MAX_FIELDS = 40
MAX_FIELD_NAME = 40
MAX_VALUE = 512
MAX_LINES = 1000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FIELD_NAME = re.compile(r"^[A-Z][A-Z0-9_.]{0,39}$")

Dataset = Literal["voucher_types", "groups", "ledgers", "stock_items", "vouchers"]
assert set(Dataset.__args__) == set(SENT_DATASETS)  # type: ignore[attr-defined]


def _clean_fields(value: dict[str, str]) -> dict[str, str]:
    if len(value) > MAX_FIELDS:
        raise ValueError("Too many fields.")
    for name, text in value.items():
        if not _FIELD_NAME.match(name):
            raise ValueError("A field name is not valid.")
        if len(text) > MAX_VALUE:
            raise ValueError("A value is too long.")
    return value


class BridgeSettingsOut(BaseModel):
    interval_minutes: int
    full_sync_hours: int
    max_batch_rows: int
    max_batch_bytes: int
    history_months: int
    poll_wait_seconds: int


# ----------------------------------------------------------------------------------------------- pairing
class PairIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20)
    device_name: str = Field(min_length=1, max_length=80)
    bridge_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.+-]+$")

    @field_validator("device_name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = _CONTROL.sub("", value).strip()
        if not cleaned:
            raise ValueError("Give this PC a name.")
        return cleaned


class PairOut(BaseModel):
    device_id: uuid.UUID
    token: str
    connection_name: str
    workspace_name: str
    settings: BridgeSettingsOut


# --------------------------------------------------------------------------------------------- heartbeat
class BridgeStatusIn(BaseModel):
    """What the bridge reports; unknown fields are ignored (newer bridges), known ones are bounded."""

    model_config = ConfigDict(extra="ignore")

    bridge_version: str | None = Field(default=None, max_length=32)
    tally: Literal["ok", "unreachable", "no_company", "error", "unknown"] = "unknown"
    tally_error: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_]+$")
    company: str | None = Field(default=None, max_length=200)
    company_guid: str | None = Field(default=None, max_length=64)
    syncing: bool = False
    last_sync_at: datetime | None = None
    last_sync_mode: Literal["full", "incremental"] | None = None
    last_error: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_]+$")
    next_sync_at: datetime | None = None

    @field_validator("company")
    @classmethod
    def _company(cls, value: str | None) -> str | None:
        return None if value is None else _CONTROL.sub("", value).strip()


class PollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wait_seconds: int = Field(default=0, ge=0, le=60)
    status: BridgeStatusIn = Field(default_factory=BridgeStatusIn)


class PollOut(BaseModel):
    refresh: bool
    full: bool
    settings: BridgeSettingsOut


# ------------------------------------------------------------------------------------------------ batches
class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    fields: dict[str, str]
    ledger_entries: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_LINES)
    inventory_entries: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_LINES)

    _fields = field_validator("fields")(_clean_fields)

    @field_validator("ledger_entries", "inventory_entries")
    @classmethod
    def _lines(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        return [_clean_fields(line) for line in value]


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_id: uuid.UUID
    seq: int = Field(ge=0, le=1_000_000)
    dataset: Dataset
    mode: Literal["full", "incremental"]
    company: str = Field(min_length=1, max_length=200)
    company_guid: str = Field(min_length=1, max_length=64)
    records: list[RecordIn] = Field(max_length=10_000)


class BatchOut(BaseModel):
    accepted: int
    duplicate: bool


class CompleteDatasetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batches: int = Field(ge=0, le=1_000_000)
    records: int = Field(ge=0)
    whole: bool


class CompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["full", "incremental"]
    company: str = Field(min_length=1, max_length=200)
    company_guid: str = Field(min_length=1, max_length=64)
    alter_id_max: int = Field(ge=0)
    datasets: dict[Dataset, CompleteDatasetIn]


class CompleteOut(BaseModel):
    status: Literal["complete", "already_complete"]
    removed: int


# ---------------------------------------------------------------------------------------- Owner routes
class TallyAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)


class PairingCodeOut(BaseModel):
    code: str
    expires_at: datetime
    server_url: str
    download_url: str | None
