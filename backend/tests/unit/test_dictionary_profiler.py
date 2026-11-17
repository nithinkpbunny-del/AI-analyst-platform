"""The profiler collects evidence for money columns and units in plain code (R13, DECISIONS D59)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.dictionary.profiler import SAMPLES, profile_frame, structure_hash


def infos(frame: pd.DataFrame, **units: str) -> list[dict[str, Any]]:
    kinds = {
        "i": "integer",
        "u": "integer",
        "f": "number",
        "M": "datetime",
        "b": "boolean",
    }
    out = []
    for name in frame.columns:
        kind = kinds.get(frame[name].dtype.kind, "text")
        out.append({"name": name, "label": name, "type": kind, "unit": units.get(name)})
    return out


def column(profile: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in profile["columns"] if c["name"] == name)


def money(frame: pd.DataFrame, name: str, **units: str) -> dict[str, Any] | None:
    result: dict[str, Any] | None = column(
        profile_frame(frame, infos(frame, **units), name="t"), name
    )["money"]
    return result


ORDERS = pd.DataFrame(
    {
        "total_price": [644.86, 2322.4, 99.5],
        "total_discounts": [44.14, 76.6, 0.5],
        "quantity": [1, 3, 2],
        "orders_count": [5, 6, 7],
        "discount_pct": [5.0, 10.0, 0.0],
        "currency": ["INR", "INR", "INR"],
    }
)


class TestMoneyEvidence:
    def test_a_single_value_inr_column_makes_a_money_name_rupees(self) -> None:
        # The demo orders: no ₹ in the values, only a currency column (the M7 demo-data fix).
        result = money(ORDERS, "total_price")
        assert result is not None
        assert result["unit"] == "INR"
        assert result["divide_by"] is None
        assert result["reason"] is None
        assert result["evidence"] == ["money_name", "currency_column"]

    def test_discounts_are_money_not_a_count(self) -> None:
        assert money(ORDERS, "total_discounts")["unit"] == "INR"  # type: ignore[index]

    def test_counts_and_percentages_are_not_money(self) -> None:
        for name in ("quantity", "orders_count", "discount_pct"):
            assert money(ORDERS, name) is None, name

    def test_a_rupee_marker_is_the_strongest_evidence(self) -> None:
        frame = pd.DataFrame({"Sales": [120500.0, 98250.5]})
        result = money(frame, "Sales", Sales="INR")
        assert result is not None
        assert result["unit"] == "INR"
        assert result["evidence"][0] == "rupee_marker"
        assert result["reason"] is None

    def test_a_money_name_alone_is_a_guess_for_the_owner_to_check(self) -> None:
        frame = pd.DataFrame({"price": [1699, 2399, 999]})
        result = money(frame, "price")
        assert result == {
            "unit": "INR",
            "divide_by": None,
            "evidence": ["money_name"],
            "reason": "money_name_only",
        }

    def test_another_single_currency_is_left_without_a_unit(self) -> None:
        frame = pd.DataFrame({"amount": [10.5, 20.0], "currency": ["USD", "USD"]})
        result = money(frame, "amount")
        assert result is not None
        assert result["unit"] is None
        assert result["reason"] == "other_currency"

    def test_percent_comes_from_the_reader(self) -> None:
        frame = pd.DataFrame({"margin": [12.5, 20.0]})
        result = money(frame, "margin", margin="percent")
        assert result is not None and result["unit"] == "percent"

    def test_text_is_never_money(self) -> None:
        frame = pd.DataFrame({"price_band": ["low", "high"]})
        assert money(frame, "price_band") is None


class TestPaise:
    PAYMENTS = pd.DataFrame(
        {
            "id": ["pay_1", "pay_2", "pay_3"],
            "entity": ["payment"] * 3,
            "amount": [137280, 70209, 99900],
            "currency": ["INR"] * 3,
            "status": ["captured", "captured", "refunded"],
            "method": ["card", "upi", "upi"],
            "fee": [2746, 1404, 1998],
            "tax": [494, 253, 359],
            "amount_refunded": [0, 0, 99900],
        }
    )

    def test_a_payments_table_with_whole_amounts_is_in_paise(self) -> None:
        for name in ("amount", "fee", "tax", "amount_refunded"):
            result = money(self.PAYMENTS, name)
            assert result is not None, name
            assert (result["unit"], result["divide_by"], result["reason"]) == ("INR", 100, "paise")
            assert "paise_signature" in result["evidence"]

    def test_a_name_that_says_paise(self) -> None:
        frame = pd.DataFrame({"amount_paise": [5000, 12000]})
        result = money(frame, "amount_paise")
        assert result is not None and result["divide_by"] == 100
        assert "paise_name" in result["evidence"]

    def test_an_integer_amount_next_to_an_inr_currency_is_a_weaker_guess(self) -> None:
        frame = pd.DataFrame({"amount": [49900, 129900, 24900], "currency": ["INR"] * 3})
        result = money(frame, "amount")
        assert result is not None
        assert (result["divide_by"], result["reason"]) == (100, "paise_guess")
        assert "paise_amount" in result["evidence"]

    def test_small_amounts_or_decimals_are_not_taken_for_paise(self) -> None:
        small = pd.DataFrame({"amount": [5, 20, 40], "currency": ["INR"] * 3})
        assert money(small, "amount")["divide_by"] is None  # type: ignore[index]
        decimals = pd.DataFrame({"amount": [499.5, 1299.0], "currency": ["INR"] * 2})
        assert money(decimals, "amount")["divide_by"] is None  # type: ignore[index]

    def test_a_rupee_marker_beats_the_paise_guess(self) -> None:
        frame = pd.DataFrame({"amount": [49900, 129900], "currency": ["INR"] * 2})
        result = money(frame, "amount", amount="INR")
        assert result is not None and result["divide_by"] is None


class TestProfile:
    def test_basics(self) -> None:
        frame = pd.DataFrame(
            {
                "created_at": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                "region": ["North", "South", "North"],
                "sales": [10.0, None, 30.0],
            }
        )
        profile = profile_frame(frame, infos(frame), name="orders")
        assert profile["row_count"] == 3
        assert profile["time_column"] == "created_at"
        region = column(profile, "region")
        assert (region["distinct"], region["role"]) == (2, "dimension")
        sales = column(profile, "sales")
        assert (sales["null_pct"], sales["min"], sales["max"]) == (33.3, 10.0, 30.0)
        assert column(profile, "created_at")["role"] == "time"

    def test_at_most_five_samples_and_none_for_personal_columns(self) -> None:
        frame = pd.DataFrame(
            {
                "city": [f"City {i}" for i in range(30)],
                "email": [f"person{i}@example.com" for i in range(30)],
                "contact": [f"98450{i:05d}" for i in range(30)],
                "buyer": [f"x{i}@shop.in" for i in range(30)],
            }
        )
        profile = profile_frame(frame, infos(frame), name="people")
        assert len(column(profile, "city")["samples"]) == SAMPLES
        for name in ("email", "buyer", "contact"):
            assert column(profile, name)["samples"] == [], name
            assert column(profile, name)["personal"] is True

    def test_the_main_time_column_is_the_creation_time(self) -> None:
        frame = pd.DataFrame(
            {
                "updated_at": pd.to_datetime(["2026-01-02"]),
                "created_at": pd.to_datetime(["2026-01-01"]),
            }
        )
        assert profile_frame(frame, infos(frame), name="t")["time_column"] == "created_at"

    def test_ids_are_keys_and_money_is_never_a_key(self) -> None:
        frame = pd.DataFrame({"order_id": [1, 2, 3], "customer_id": [7, 7, 8], "price": [5, 6, 7]})
        profile = profile_frame(frame, infos(frame), name="t")
        assert column(profile, "order_id")["role"] == "key"
        assert column(profile, "customer_id")["role"] == "key"
        assert column(profile, "price")["role"] == "measure"


class TestStructureHash:
    def profiles(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {
                "dataset_id": "d1",
                "name": "t",
                "profile": profile_frame(frame, infos(frame), name="t"),
            }
        ]

    def test_new_rows_do_not_change_it_but_a_new_column_or_unit_does(self) -> None:
        a = pd.DataFrame({"price": [1, 2], "currency": ["INR", "INR"]})
        more_rows = pd.DataFrame({"price": [1, 2, 3, 4], "currency": ["INR"] * 4})
        extra = a.assign(color=["red", "blue"])
        other_currency = pd.DataFrame({"price": [1, 2], "currency": ["USD", "USD"]})
        base = structure_hash(self.profiles(a))
        assert structure_hash(self.profiles(more_rows)) == base
        assert structure_hash(self.profiles(extra)) != base
        assert structure_hash(self.profiles(other_currency)) != base


class TestSourceUnits:
    """An API source can say the unit itself (DECISIONS D91, D95): Razorpay paise, Shopify rupees."""

    def _money(self, frame: pd.DataFrame, name: str, stored_in: str, unit: str | None) -> Any:
        columns = infos(frame)
        for info in columns:
            if info["name"] == name:
                info["stored_in"] = stored_in
                info["unit"] = unit
        return column(profile_frame(frame, columns, name="t"), name)["money"]

    def test_shopify_rupees_are_never_divided(self) -> None:
        # whole numbers in a table that looks like payments would otherwise be taken for paise
        frame = pd.DataFrame(
            {
                "id": ["a", "b"],
                "amount": [49_900, 12_000],
                "currency": ["INR", "INR"],
                "status": ["paid", "paid"],
                "method": ["upi", "card"],
                "order_id": ["o1", "o2"],
            }
        )
        result = self._money(frame, "amount", "rupees", "INR")
        assert result["unit"] == "INR" and result["divide_by"] is None
        assert result["evidence"][0] == "source_rupees"
        for name in ("total_price", "unit_price", "total_spent"):
            result = self._money(pd.DataFrame({name: [689.0, 1299.5]}), name, "rupees", "INR")
            assert (result["unit"], result["divide_by"]) == ("INR", None), name

    def test_razorpay_paise_are_still_divided(self) -> None:
        result = self._money(pd.DataFrame({"amount": [49_900, 12_000]}), "amount", "paise", None)
        assert (result["unit"], result["divide_by"]) == ("INR", 100)
