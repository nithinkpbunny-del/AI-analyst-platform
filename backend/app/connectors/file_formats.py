"""Reading uploaded ``.csv`` and ``.xlsx`` files safely (BUILD_SPEC 8.2, 6.3 rule 5).

* The file type is decided from its content, not only its name: an ``.xlsx`` must be a ZIP package with a
  workbook inside; a ``.csv`` must be text.
* ``.xlsx`` packages are checked before opening: a limited number of parts and of unpacked size (a "zip bomb"
  is refused). XML is parsed by ``defusedxml`` (openpyxl uses it when installed), so entity tricks are refused.
* Formulas are never evaluated: the values Excel saved with the file are read (``data_only``).
* Old ``.xls`` files and password-protected workbooks (both OLE files) get a clear message.

Errors are ``FileFormatError`` with a plain-language message for the person who uploaded the file.
"""

from __future__ import annotations

import codecs
import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

FileKind = Literal["csv", "xlsx"]

ALLOWED_EXTENSIONS: dict[str, FileKind] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "xlsx",
}
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_ZIP_PARTS = 5_000
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024  # 1 GiB of XML for a 50 MB upload is already generous
MAX_COMPRESSION_RATIO = 200
CSV_DATASET_REF = "data"  # a CSV file has one table


class FileFormatError(Exception):
    """The file cannot be read. ``str(error)`` is safe to show to the person who uploaded it."""


@dataclass(frozen=True)
class SheetInfo:
    ref: str  # the sheet name for .xlsx, "data" for .csv
    name: str


def file_kind(filename: str, data: bytes) -> FileKind:
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".xls" or data.startswith(_OLE_MAGIC):
        raise FileFormatError(
            "This is an old Excel (.xls) file or a password-protected workbook. Open it in Excel, save it as "
            ".xlsx without a password, and upload it again."
        )
    kind = ALLOWED_EXTENSIONS.get(suffix)
    if kind is None:
        raise FileFormatError("Upload an Excel workbook (.xlsx) or a CSV file (.csv).")
    if not data:
        raise FileFormatError("The file is empty.")
    if kind == "xlsx":
        if not data.startswith(_ZIP_MAGIC):
            raise FileFormatError(
                "This file is not a real .xlsx workbook. Save it from Excel and try again."
            )
        _check_package(data)
    elif data.startswith(_ZIP_MAGIC) or (b"\x00" in data[:8192] and not _has_utf16_bom(data)):
        raise FileFormatError(
            "This file is not a text CSV file. Export it as CSV (comma separated) and try again."
        )
    return kind


def _check_package(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            parts = package.infolist()
            names = {p.filename for p in parts}
    except zipfile.BadZipFile as error:
        raise FileFormatError("This workbook is damaged and cannot be opened.") from error
    if len(parts) > MAX_ZIP_PARTS:
        raise FileFormatError("This workbook has too many parts to be read safely.")
    unpacked = sum(p.file_size for p in parts)
    packed = max(1, sum(p.compress_size for p in parts))
    if unpacked > MAX_UNPACKED_BYTES or unpacked / packed > MAX_COMPRESSION_RATIO:
        raise FileFormatError("This workbook is too large when unpacked to be read safely.")
    if "xl/workbook.xml" not in names:
        raise FileFormatError(
            "This file is not a real .xlsx workbook. Save it from Excel and try again."
        )


# -------------------------------------------------------------------------------------------------- CSV
def _has_utf16_bom(data: bytes) -> bool:
    return data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM (Excel "Unicode text"), else Windows-1252."""
    if _has_utf16_bom(data):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _delimiter(sample: str, filename: str) -> str:
    if PurePath(filename).suffix.lower() == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def csv_rows(data: bytes, filename: str = "data.csv") -> Iterator[list[str]]:
    text = decode_text(data)
    delimiter = _delimiter(text[:65536], filename)
    csv.field_size_limit(1024 * 1024)
    try:
        yield from csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    except csv.Error as error:
        raise FileFormatError(f"The CSV file could not be read ({error}).") from error


# ------------------------------------------------------------------------------------------------- XLSX
def _open_workbook(data: bytes) -> Any:
    try:
        return openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (InvalidFileException, KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise FileFormatError("This workbook is damaged and cannot be opened.") from error


def xlsx_sheets(data: bytes) -> list[SheetInfo]:
    workbook = _open_workbook(data)
    try:
        return [
            SheetInfo(ref=ws.title, name=ws.title)
            for ws in workbook.worksheets
            if getattr(ws, "sheet_state", "visible") == "visible"
        ]
    finally:
        workbook.close()


def xlsx_rows(data: bytes, sheet: str) -> Iterator[list[Any]]:
    workbook = _open_workbook(data)
    try:
        if sheet not in workbook.sheetnames:
            raise FileFormatError(f"The sheet '{sheet}' is no longer in the file.")
        for row in workbook[sheet].iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def sheets(kind: FileKind, data: bytes, filename: str) -> list[SheetInfo]:
    if kind == "csv":
        return [SheetInfo(ref=CSV_DATASET_REF, name=PurePath(filename).stem or "Data")]
    found = xlsx_sheets(data)
    if not found:
        raise FileFormatError("This workbook has no visible sheets.")
    return found


def rows(kind: FileKind, data: bytes, filename: str, sheet: str) -> Iterator[list[Any]]:
    if kind == "csv":
        return csv_rows(data, filename)
    return xlsx_rows(data, sheet)
