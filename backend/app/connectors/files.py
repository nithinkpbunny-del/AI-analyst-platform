"""Excel / CSV connector (BUILD_SPEC 8.2).

The uploaded file is the source: it is stored unchanged in object storage (never modified, BUILD_SPEC 6.3 rule 5)
and each sheet is one dataset (a CSV file has one). Re-uploading creates a new file version; the newest readable
version is used, and its id is the data's ``source_version``, so snapshots stay valid until a new upload.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.connectors import file_formats, tabular
from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.file_formats import FileFormatError
from app.data.objects import ObjectNotFound, ObjectStore


@dataclass(frozen=True)
class StoredFile:
    version_id: uuid.UUID
    version: int
    storage_key: str
    original_name: str


class FileConnector:
    type = "file"

    def __init__(
        self, file: StoredFile | None, *, objects: ObjectStore, timezone: str, max_rows: int
    ) -> None:
        self._file = file
        self._objects = objects
        self._timezone = timezone
        self._max_rows = max_rows
        self._data: bytes | None = None

    def _bytes(self) -> tuple[StoredFile, bytes]:
        if self._file is None:
            raise ConnectorError(
                "not_found", "No readable file has been uploaded yet. Upload the file again."
            )
        if self._data is None:
            try:
                self._data = self._objects.get(self._file.storage_key)
            except ObjectNotFound as error:
                raise ConnectorError(
                    "not_found",
                    "The uploaded file is missing from storage. Upload it again.",
                    detail=str(error),
                ) from error
        return self._file, self._data

    def _kind(self) -> tuple[StoredFile, bytes, file_formats.FileKind]:
        file, data = self._bytes()
        try:
            return file, data, file_formats.file_kind(file.original_name, data)
        except FileFormatError as error:
            raise ConnectorError("invalid", str(error)) from error

    def list_datasets(self) -> list[DatasetInfo]:
        file, data, kind = self._kind()
        try:
            found = file_formats.sheets(kind, data, file.original_name)
        except FileFormatError as error:
            raise ConnectorError("invalid", str(error)) from error
        return [DatasetInfo(ref=s.ref, name=s.name) for s in found]

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        file, data, kind = self._kind()
        try:
            rows = file_formats.rows(kind, data, file.original_name, dataset.ref)
            table = tabular.parse_rows(rows, timezone=self._timezone, max_rows=self._max_rows)
        except FileFormatError as error:
            raise ConnectorError(
                "not_found" if "no longer" in str(error) else "invalid", str(error)
            ) from error
        return FetchResult.from_table(table, source_version=str(file.version_id))

    def source_version(self, dataset: DatasetInfo) -> str | None:
        return str(self._file.version_id) if self._file else None

    def test(self) -> ConnectionTestResult:
        try:
            found = self.list_datasets()
        except ConnectorError as error:
            return ConnectionTestResult(
                ok=False, message=error.message, checks=[TestCheck("file_readable", False)]
            )
        assert self._file is not None
        return ConnectionTestResult(
            ok=True,
            message=f"{self._file.original_name} (version {self._file.version}) opens and has "
            f"{len(found)} {'sheet' if len(found) == 1 else 'sheets'}.",
            checks=[TestCheck("file_readable", True), TestCheck("read_only", True)],
            details={"datasets": len(found), "version": self._file.version},
        )

    def supports_incremental(self) -> bool:
        return False

    def supports_pushdown(self) -> bool:
        return False
