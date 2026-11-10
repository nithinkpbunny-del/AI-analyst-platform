"""The table reader (BUILD_SPEC 8.2): header rows, types, Indian dates, ₹ amounts and lakh separators."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.connectors import file_formats
from app.connectors.tabular import find_header_row, parse_rows, snake_name, unique_names

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures"


def column(rows: list[list[Any]], name: str, **kwargs: Any) -> tuple[pd.Series, Any]:
    table = parse_rows(rows, **kwargs)
    info = next(c for c in table.columns if c.name == name)
    return table.frame[name], info


def one(values: list[Any], header: str = "Value", **kwargs: Any) -> tuple[pd.Series, Any]:
    return column([[header], *[[v] for v in values]], snake_name(header), **kwargs)


class TestHeader:
    def test_title_rows_above_the_header_are_skipped(self) -> None:
        rows = [["UrbanCart sales report"], [], ["Date", "Sales"], ["01/01/2026", "100"]]
        table = parse_rows(rows)
        assert table.header_row == 3
        assert [c.label for c in table.columns] == ["Date", "Sales"]
        assert len(table.frame) == 1

    def test_data_without_a_header_gets_column_numbers(self) -> None:
        table = parse_rows([["01/01/2026", 100, 2.5], ["02/01/2026", 200, 3.5]])
        assert table.header_row is None
        assert [c.label for c in table.columns] == ["Column 1", "Column 2", "Column 3"]
        assert len(table.frame) == 2

    def test_find_header_row_needs_mostly_text(self) -> None:
        assert find_header_row([["a", "b", "c"], [1, 2, 3]]) == 0
        assert find_header_row([[1, 2, 3], [4, 5, 6]]) is None
        assert find_header_row([["a", "a"], [1, 2]]) is None  # repeated names are not a header

    def test_names_are_snake_case_and_unique(self) -> None:
        assert snake_name("Sales (₹)") == "sales_inr"
        assert snake_name("Discount %") == "discount_pct"
        assert snake_name("2026 target") == "c_2026_target"
        assert unique_names(["Amount", "Amount", "", "amount"]) == [
            "amount",
            "amount_2",
            "column_3",
            "amount_3",
        ]

    def test_empty_rows_and_unnamed_empty_columns_are_dropped(self) -> None:
        rows: list[list[Any]] = [["A", None, "B"], [1, None, 2], [None, None, None], [3, None, 4]]
        table = parse_rows(rows)
        assert [c.name for c in table.columns] == ["a", "b"]
        assert len(table.frame) == 2

    @pytest.mark.parametrize("label", ["Total", "Grand total", "TOTAL:", "Sub total"])
    def test_a_total_row_at_the_end_is_left_out(self, label: str) -> None:
        table = parse_rows([["Region", "Sales"], ["South", 10], ["North", 20], [label, 30]])
        assert table.total_row_dropped
        assert table.frame["sales"].tolist() == [10, 20]

    def test_max_rows_truncates(self) -> None:
        rows: list[list[Any]] = [["n"], *[[i] for i in range(10)]]
        table = parse_rows(rows, max_rows=4)
        assert table.truncated and len(table.frame) == 4


class TestNumbers:
    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("12,34,567", 1234567),
            ("1,234,567", 1234567),
            ("₹1,20,500", 120500),
            ("Rs. 4,500", 4500),
            ("INR 900", 900),
            ("(1,200)", -1200),
            ("-75", -75),
        ],
    )
    def test_whole_numbers(self, text: str, value: int) -> None:
        series, info = one([text, "10"])
        assert info.type == "integer"
        assert series.iloc[0] == value

    def test_decimals_percent_and_lakh_words(self) -> None:
        series, info = one(["12.5%", "7%"], header="Discount")
        assert (info.type, info.unit) == ("number", "percent") or (info.type, info.unit) == (
            "integer",
            "percent",
        )
        assert series.tolist()[0] == 12.5
        series, info = one(["1.2 L", "3 lakh", "1.5 Cr"])
        assert series.tolist() == [120000.0, 300000.0, 15000000.0]

    def test_rupee_markers_set_the_unit(self) -> None:
        _, info = one(["₹100", "₹2,000"])
        assert info.unit == "INR"
        _, info = one(["100", "200"], header="Revenue (INR)")
        assert info.unit == "INR"

    def test_wrong_grouping_is_not_a_number(self) -> None:
        _, info = one(["1,2", "3,4"])
        assert info.type == "text"

    def test_leading_zeros_and_identifier_names_stay_text(self) -> None:
        _, info = one(["080012", "560001"], header="Code")
        assert info.type == "text"
        _, info = one(["9876543210", "9123456780"], header="Mobile")
        assert info.type == "text"
        _, info = one(["1234567890123456"], header="Card")
        assert info.type == "text"

    def test_a_few_bad_values_are_left_empty_and_counted(self) -> None:
        values = [str(i * 100) for i in range(1, 40)] + ["TBD"]
        series, info = one(values, header="Amount")
        assert info.type == "integer"
        assert info.unparsed_count == 1
        assert info.unparsed_examples == ("TBD",)
        assert pd.isna(series.iloc[-1])

    def test_null_tokens_are_empty_not_text(self) -> None:
        rows = [["City", "Value"], *[["Pune", v] for v in ["100", "N/A", "-", "#N/A", "200"]]]
        _, info = column(rows, "value")
        assert info.type == "integer"
        assert info.null_count == 3
        assert info.unparsed_count == 0

    def test_mixed_currencies_are_recorded(self) -> None:
        _, info = one(["₹100", "$20", "₹300"])
        assert set(info.currencies) == {"₹", "$"}
        assert info.unit is None


class TestDates:
    def test_day_first_is_the_default(self) -> None:
        series, info = one(["05/01/2026", "06/01/2026"], header="Date")
        assert info.type == "date"
        assert info.date_format == "dd/mm/yyyy"
        assert series.iloc[0] == pd.Timestamp("2026-01-05")

    def test_month_first_when_the_values_prove_it(self) -> None:
        series, info = one(["01/05/2026", "01/13/2026", "01/28/2026"], header="Date")
        assert info.date_format == "mm/dd/yyyy"
        assert series.iloc[1] == pd.Timestamp("2026-01-13")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-01-05", "2026-01-05"),
            ("05-01-2026", "2026-01-05"),
            ("05.01.2026", "2026-01-05"),
            ("5 Jan 2026", "2026-01-05"),
            ("05-Jan-26", "2026-01-05"),
            ("Jan 2026", "2026-01-01"),
        ],
    )
    def test_common_formats(self, text: str, expected: str) -> None:
        series, info = one([text], header="Date")
        assert info.type == "date"
        assert series.iloc[0] == pd.Timestamp(expected)

    def test_offsets_are_converted_to_the_workspace_time_zone(self) -> None:
        series, info = one(
            ["2026-01-01T00:25:18+05:30", "2026-01-01T18:00:00Z"], header="created_at"
        )
        assert info.type == "datetime"
        assert series.tolist() == [
            pd.Timestamp("2026-01-01 00:25:18"),
            pd.Timestamp("2026-01-01 23:30:00"),
        ]

    def test_unix_timestamps_in_a_time_column(self) -> None:
        series, info = one(["1735704852", "1735720135"], header="created_at")
        assert (info.type, info.date_format) == ("datetime", "unix time")
        assert series.iloc[0] == pd.Timestamp("2025-01-01 09:44:12")

    def test_unix_like_numbers_elsewhere_stay_numbers(self) -> None:
        _, info = one(["1735704852", "1735720135"], header="amount")
        assert info.type == "integer"

    def test_excel_dates_and_times(self) -> None:
        _, info = one([datetime(2026, 7, 1), datetime(2026, 8, 1)], header="Month")
        assert info.type == "date"
        _, info = one([datetime(2026, 7, 1, 9, 30)], header="When")
        assert info.type == "datetime"


def test_booleans() -> None:
    series, info = one(["Yes", "no", "TRUE", True])
    assert info.type == "boolean"
    assert series.tolist() == [True, False, True, True]


def test_schema_json_round_trips_every_field() -> None:
    table = parse_rows([["Date", "Sales (₹)"], ["05/01/2026", "₹1,200"]])
    schema = table.schema_json()
    assert schema["header_row"] == 1
    assert schema["columns"][1] == {
        "name": "sales_inr",
        "label": "Sales (₹)",
        "type": "integer",
        "unit": "INR",
        "date_format": None,
        "null_count": 0,
        "unparsed_count": 0,
        "unparsed_examples": [],
        "currencies": ["₹"],
    }


@pytest.mark.skipif(not (FIXTURES / "shopify_orders.csv").exists(), reason="run `make fixtures`")
class TestDemoFixtures:
    def test_shopify_orders(self) -> None:
        data = (FIXTURES / "shopify_orders.csv").read_bytes()
        table = parse_rows(file_formats.rows("csv", data, "shopify_orders.csv", "data"))
        types = {c.name: c.type for c in table.columns}
        assert types["created_at"] == "datetime"
        assert types["total_price"] == "number"
        assert types["quantity"] == "integer"
        assert types["sales_channel"] == "text"
        assert len(table.frame) > 20_000

    def test_razorpay_amounts_and_unix_times(self) -> None:
        data = (FIXTURES / "razorpay_payments.csv").read_bytes()
        table = parse_rows(file_formats.rows("csv", data, "razorpay_payments.csv", "data"))
        types = {c.name: c.type for c in table.columns}
        assert types["created_at"] == "datetime"
        assert types["captured"] == "boolean"
        assert types["amount"] == "integer"  # paise; converting is the dictionary's job (M7)

    def test_marketing_sheet_tabs(self) -> None:
        data = (FIXTURES / "marketing_sheet.xlsx").read_bytes()
        sheets = file_formats.sheets("xlsx", data, "marketing_sheet.xlsx")
        assert sheets[-1].name == "Targets"
        table = parse_rows(file_formats.rows("xlsx", data, "m.xlsx", "Targets"))
        units = {c.name: (c.type, c.unit) for c in table.columns}
        assert units == {
            "month": ("date", None),
            "region": ("text", None),
            "revenue_target_inr": ("integer", "INR"),
        }
