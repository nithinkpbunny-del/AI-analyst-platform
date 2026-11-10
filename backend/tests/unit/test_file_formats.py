"""Uploaded files are recognised by content and opened safely (BUILD_SPEC 8.2, 6.3 rule 5)."""

from __future__ import annotations

import io
import zipfile

import openpyxl
import pytest

from app.connectors import file_formats
from app.connectors.file_formats import FileFormatError, file_kind
from tests.datasources import xlsx_bytes


def test_kinds_by_content_and_name() -> None:
    assert file_kind("a.csv", b"a,b\n1,2\n") == "csv"
    assert file_kind("a.TSV", b"a\tb\n") == "csv"
    assert file_kind("plan.xlsx", xlsx_bytes()) == "xlsx"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("a.xls", b"anything", ".xls"),
        ("a.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "password-protected"),
        ("a.pdf", b"%PDF", ".xlsx) or a CSV"),
        ("a.xlsx", b"a,b\n", "not a real .xlsx"),
        ("a.csv", b"", "empty"),
        ("a.csv", b"PK\x03\x04zip", "not a text CSV"),
        ("a.csv", b"\x00\x01\x02binary", "not a text CSV"),
    ],
)
def test_refused_files(name: str, data: bytes, message: str) -> None:
    with pytest.raises(
        FileFormatError, match=message.replace(".", r"\.").replace("(", r"\(").replace(")", r"\)")
    ):
        file_kind(name, data)


def _zip(parts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in parts.items():
            package.writestr(name, content)
    return buffer.getvalue()


def test_a_zip_bomb_is_refused() -> None:
    bomb = _zip({"xl/workbook.xml": b"<x/>", "xl/worksheets/sheet1.xml": b"0" * 50_000_000})
    with pytest.raises(FileFormatError, match="too large when unpacked"):
        file_kind("bomb.xlsx", bomb)


def test_a_zip_without_a_workbook_is_refused() -> None:
    with pytest.raises(FileFormatError, match=r"not a real \.xlsx"):
        file_kind("x.xlsx", _zip({"word/document.xml": b"<x/>"}))


@pytest.mark.parametrize(
    ("data", "rows"),
    [
        (
            "Date,Sales\n01/01/2026,100\n".encode("utf-8-sig"),
            [["Date", "Sales"], ["01/01/2026", "100"]],
        ),
        (b"City;Sales\nBengaluru;100\n", [["City", "Sales"], ["Bengaluru", "100"]]),
        ("City\tSales\nPune\t5\n".encode("utf-16"), [["City", "Sales"], ["Pune", "5"]]),
        ("Caf\xe9,Sales\nx,1\n".encode("cp1252"), [["Café", "Sales"], ["x", "1"]]),
    ],
)
def test_csv_encodings_and_delimiters(data: bytes, rows: list[list[str]]) -> None:
    assert list(file_formats.csv_rows(data, "x.csv")) == rows


def test_every_visible_sheet_and_cached_formula_values() -> None:
    workbook = openpyxl.Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Sales"
    first.append(["Units", "Price", "Total"])
    first.append([2, 50, "=A2*B2"])
    hidden = workbook.create_sheet("Lookup")
    hidden.sheet_state = "hidden"
    workbook.create_sheet("Targets").append(["Region"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    data = buffer.getvalue()
    assert [s.name for s in file_formats.sheets("xlsx", data, "x.xlsx")] == ["Sales", "Targets"]
    # Formulas are never evaluated: openpyxl-written files have no cached value, so the cell is empty.
    rows = list(file_formats.rows("xlsx", data, "x.xlsx", "Sales"))
    assert rows[1] == [2, 50, None]


def test_a_missing_sheet_says_so() -> None:
    with pytest.raises(FileFormatError, match="no longer in the file"):
        list(file_formats.rows("xlsx", xlsx_bytes(), "x.xlsx", "Gone"))


def test_a_csv_is_one_dataset_named_after_the_file() -> None:
    [sheet] = file_formats.sheets("csv", b"a\n1\n", "outlet sales.csv")
    assert (sheet.ref, sheet.name) == ("data", "outlet sales")


def test_xml_entities_are_refused(tmp_path: object) -> None:
    """defusedxml protects openpyxl from entity expansion ("billion laughs")."""
    evil = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">&b;</workbook>'
    )
    data = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": evil})
    with pytest.raises(FileFormatError):
        file_formats.sheets("xlsx", data, "evil.xlsx")
