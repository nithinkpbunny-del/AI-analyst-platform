"""The connector interface every data source implements (BUILD_SPEC 8.1).

A connector reads; it never writes (P2). It is built for one connection with everything it needs (a file
version, a decrypted token), lists the source's tables as datasets, and fetches one dataset as a typed table.
Errors are ``ConnectorError`` with a ``kind`` the platform acts on (e.g. ``needs_reauth`` sets the connection's
status) and a message that says what happened and how to fix it (BUILD_SPEC 11a.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import pandas as pd

from app.connectors.tabular import ColumnInfo, ParsedTable

ErrorKind = Literal["needs_reauth", "not_found", "unavailable", "rate_limited", "invalid", "busy"]


class ReadOnlyViolation(Exception):
    """Something tried to change data in a customer's source (BUILD_SPEC 6.3). Always a bug: never caught."""


class ConnectorError(Exception):
    def __init__(
        self, kind: ErrorKind, message: str, *, detail: str = "", stale_ok: bool = False
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail  # for the log only; never shown, never contains tokens
        # The source refused because of its own usage limits: the copy already stored may be used instead
        # (Zoho's daily API quota, DECISIONS D97).
        self.stale_ok = stale_ok


@dataclass(frozen=True)
class DatasetInfo:
    ref: str  # becomes ``datasets.external_ref``
    name: str


@dataclass
class FetchResult:
    frame: pd.DataFrame
    columns: list[ColumnInfo]
    source_version: str | None = None
    # Incremental sources: where the next fetch continues, and the column that identifies a row for merging.
    cursor: str | None = None
    # True when ``frame`` holds only rows new or changed since ``since_cursor``.
    incremental: bool = False
    key_column: str | None = None
    truncated: bool = False
    total_row_dropped: bool = False
    # Sources pushed to the platform (the Tally bridge): when the rows left the source. Freshness and provenance
    # use it instead of the time the table was built, so data from an offline PC never looks new (D111).
    data_as_of: datetime | None = None

    @classmethod
    def from_table(cls, table: ParsedTable, *, source_version: str | None) -> FetchResult:
        return cls(
            frame=table.frame,
            columns=table.columns,
            source_version=source_version,
            truncated=table.truncated,
            total_row_dropped=table.total_row_dropped,
        )


@dataclass(frozen=True)
class TestCheck:
    label: str
    ok: bool


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
    checks: list[TestCheck] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    type: str

    def test(self) -> ConnectionTestResult: ...

    def list_datasets(self) -> list[DatasetInfo]: ...

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult: ...

    def source_version(self, dataset: DatasetInfo) -> str | None:
        """A cheap check of the source's version of the data; None if the source cannot tell."""
        ...

    def supports_incremental(self) -> bool: ...

    def supports_pushdown(self) -> bool: ...
