"""Reading Tally's text into typed tables (``app.connectors.tally``, M16): amounts with Tally's signs, quantities,
rates, dates, voucher classes through custom voucher types, primary groups, and the tables' shapes."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.connectors.tally import (
    TABLES,
    Raw,
    Shaper,
    _frame,
    column_infos,
    debit_positive,
    quantity,
    rate,
    tally_date,
    tally_number,
    yes,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-1281.72", -1281.72),
        ("1281.72", 1281.72),
        ("1,23,456.50", 123456.5),
        ("-1,23,456.50", -123456.5),
        ("₹ 500.00", 500.0),
        ("1000.00 Dr", -1000.0),
        ("1000.00 Cr", 1000.0),
        ("(250.00)", -250.0),
        ("$ 10.00 @ ₹ 83.00/$ = ₹ 830.00", 830.0),
        ("-$ 10.00 @ ₹ 83.00/$ = -₹ 830.00", -830.0),
        ("", None),
        (None, None),
        ("n/a", None),
    ],
)
def test_amounts(text: Any, expected: float | None) -> None:
    assert tally_number(text) == expected


def test_debit_is_positive() -> None:
    assert debit_positive("-1281.72") == 1281.72
    assert debit_positive("50.00") == -50.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" 2 Nos", (2.0, "Nos")),
        ("2.500 kg = 2 Box", (2.5, "kg")),
        ("-3 Nos", (-3.0, "Nos")),
        ("1,200 Pcs", (1200.0, "Pcs")),
        ("12", (12.0, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_quantities(text: Any, expected: tuple[float | None, str | None]) -> None:
    assert quantity(text) == expected


def test_rates_dates_and_flags() -> None:
    assert rate("499.00/Nos") == 499.0
    assert rate("") is None
    assert tally_date("20260701") == date(2026, 7, 1)
    assert tally_date("1-Jul-2026") == date(2026, 7, 1)
    assert tally_date("01-07-2026") == date(2026, 7, 1)
    assert tally_date("soon") is None
    assert yes("Yes") is True and yes("No") is False and yes("") is None


def raw(**datasets: list[tuple[str, str | None, dict[str, Any]]]) -> Raw:
    return Raw(dict(datasets))


def test_custom_voucher_types_and_groups_are_followed_to_their_base() -> None:
    shaper = Shaper(
        raw(
            voucher_types=[
                ("t1", None, {"NAME": "Sales", "PARENT": "Sales"}),
                ("t2", None, {"NAME": "Online Sales", "PARENT": "Sales"}),
                ("t3", None, {"NAME": "Shop Counter", "PARENT": "Online Sales"}),
                ("t4", None, {"NAME": "Loop A", "PARENT": "Loop B"}),
                ("t5", None, {"NAME": "Loop B", "PARENT": "Loop A"}),
            ],
            groups=[
                ("g1", None, {"NAME": "Sundry Debtors", "PARENT": "Current Assets"}),
                ("g2", None, {"NAME": "Current Assets", "PARENT": "Primary"}),
                ("g3", None, {"NAME": "Pune Debtors", "PARENT": "Sundry Debtors"}),
            ],
        )
    )
    assert shaper.voucher_class("Shop Counter") == "sales"
    assert shaper.voucher_class("Loop A") == "other"
    assert shaper.primary_group("Pune Debtors") == "Current Assets"
    assert shaper.nature("Current Assets") == "Assets"


def test_a_voucher_amount_is_its_debits_and_lines_carry_the_header() -> None:
    shaper = Shaper(
        raw(
            vouchers=[
                (
                    "V1",
                    None,
                    {"DATE": "20260701", "VOUCHERTYPENAME": "Sales", "PARTYLEDGERNAME": "Asha"},
                ),
                (
                    "V2",
                    None,
                    {"DATE": "20260702", "VOUCHERTYPENAME": "Sales", "ISCANCELLED": "Yes"},
                ),
            ],
            voucher_ledger_entries=[
                ("V1#L0", "V1", {"LEDGERNAME": "Asha", "AMOUNT": "-1180.00"}),
                ("V1#L1", "V1", {"LEDGERNAME": "Sales", "AMOUNT": "1000.00"}),
                ("V1#L2", "V1", {"LEDGERNAME": "Output GST", "AMOUNT": "180.00"}),
                ("V2#L0", "V2", {"LEDGERNAME": "Asha", "AMOUNT": "-5.00"}),
            ],
            voucher_inventory_entries=[
                (
                    "V1#I0",
                    "V1",
                    {
                        "STOCKITEMNAME": "Kurta",
                        "BILLEDQTY": " 2 Nos",
                        "RATE": "500.00/Nos",
                        "AMOUNT": "1000.00",
                    },
                )
            ],
        )
    )
    vouchers = shaper.table("vouchers")
    assert [v["guid"] for v in vouchers] == ["V1"]  # the cancelled one is left out
    assert vouchers[0]["amount"] == 1180.0
    assert vouchers[0]["voucher_class"] == "sales"
    lines = shaper.table("voucher_ledger_entries")
    assert [(line["line"], line["debit"], line["credit"]) for line in lines] == [
        (0, 1180.0, 0.0),
        (1, 0.0, 1000.0),
        (2, 0.0, 180.0),
    ]
    assert all(line["party_ledger"] == "Asha" for line in lines)
    items = shaper.table("voucher_inventory_entries")
    assert items[0]["quantity"] == 2.0 and items[0]["unit"] == "Nos" and items[0]["rate"] == 500.0


def test_balances_and_the_trial_balance() -> None:
    shaper = Shaper(
        raw(
            groups=[
                (
                    "g",
                    None,
                    {
                        "NAME": "Bank Accounts",
                        "PARENT": "Current Assets",
                        "PRIMARYGROUP": "Current Assets",
                    },
                )
            ],
            ledgers=[
                (
                    "l1",
                    None,
                    {
                        "NAME": "HDFC",
                        "PARENT": "Bank Accounts",
                        "CLOSINGBALANCE": "-5000.00",
                        "OPENINGBALANCE": "-100.00",
                        "PERIODFROM": "20260401",
                        "PERIODTO": "20261120",
                    },
                ),
                (
                    "l2",
                    None,
                    {
                        "NAME": "Capital",
                        "PARENT": "Capital Account",
                        "PRIMARYGROUP": "Capital Account",
                        "CLOSINGBALANCE": "5000.00",
                    },
                ),
            ],
        )
    )
    ledgers = {row["name"]: row for row in shaper.table("ledgers")}
    assert ledgers["HDFC"]["closing_balance"] == 5000.0
    assert ledgers["HDFC"]["closing_dr_cr"] == "Dr"
    assert ledgers["HDFC"]["nature"] == "Assets"
    assert ledgers["HDFC"]["period_from"] == date(2026, 4, 1)
    assert ledgers["Capital"]["closing_dr_cr"] == "Cr"
    trial = shaper.table("trial_balance")
    assert (
        sum(r["closing_debit"] for r in trial) == sum(r["closing_credit"] for r in trial) == 5000.0
    )


@pytest.mark.parametrize("table", list(TABLES))
def test_every_table_builds_even_when_empty_and_declares_rupees(table: str) -> None:
    frame = _frame(table, Shaper(raw()).table(table))
    assert list(frame.columns) == [c.name for c in TABLES[table]]
    for info, column in zip(column_infos(table, frame), TABLES[table], strict=True):
        assert (info.stored_in == "rupees") is column.money
        assert (info.unit == "INR") is column.money
