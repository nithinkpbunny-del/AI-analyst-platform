"""Data-quality findings on every fetch (BUILD_SPEC 7.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.connectors import file_formats
from app.connectors.tabular import ColumnInfo, parse_rows
from app.data import quality
from app.data.quality import columns_from_schema

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures"


def orders(days: int = 140, per_day: int = 40, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2026-06-01")
    for day in range(days):
        for _ in range(int(rng.poisson(per_day))):
            rows.append(
                {
                    "order_id": len(rows) + 1,
                    "created_at": start + pd.Timedelta(days=day, hours=int(rng.integers(0, 23))),
                    "channel": rng.choice(["App", "Website", "Marketplace"], p=[0.5, 0.3, 0.2]),
                    "amount": float(rng.integers(100, 5000)),
                }
            )
    return pd.DataFrame(rows)


COLUMNS = [
    ColumnInfo("order_id", "Order id", "integer"),
    ColumnInfo("created_at", "Created at", "datetime"),
    ColumnInfo("channel", "Channel", "text"),
    ColumnInfo("amount", "Amount", "number"),
]


def kinds(
    frame: pd.DataFrame, columns: list[ColumnInfo] = COLUMNS, **kwargs: object
) -> dict[str, list[quality.QualityIssue]]:
    found: dict[str, list[quality.QualityIssue]] = {}
    for issue in quality.check(frame, columns, now=NOW, timezone="Asia/Kolkata", **kwargs):  # type: ignore[arg-type]
        found.setdefault(issue.kind, []).append(issue)
    return found


def test_clean_regular_data_has_no_warnings_but_stale() -> None:
    found = kinds(orders())
    assert set(found) == {"stale"}  # data ends in October; "now" is November


def test_random_noise_is_not_reported() -> None:
    for seed in range(5):
        found = kinds(orders(per_day=3, seed=seed))  # tiny volumes vary a lot by chance
        assert "incomplete_period" not in found
        assert "missing_segment" not in found


def test_a_week_with_far_fewer_rows() -> None:
    frame = orders()
    week = (frame.created_at >= "2026-08-03") & (frame.created_at < "2026-08-10")
    frame = frame[~week | (frame.index % 3 == 0)]  # keep a third
    [issue] = kinds(frame)["incomplete_period"]
    assert issue.details["period_start"] == "2026-08-03"
    assert issue.details["share_pct"] < 50
    assert issue.message.startswith("The week of 3 Aug 2026 has ")


def test_a_missing_segment_for_one_week() -> None:
    frame = orders()
    gap = (
        (frame.channel == "Website")
        & (frame.created_at >= "2026-07-06")
        & (frame.created_at < "2026-07-13")
    )
    [issue] = kinds(frame[~gap])["missing_segment"]
    assert issue.column == "channel"
    assert issue.details == {
        "value": "Website",
        "period_start": "2026-07-06",
        "rows": 0,
        "usual_rows": issue.details["usual_rows"],
    }
    assert "Rows with channel = Website look missing for the week of 6 Jul 2026" in issue.message


def test_stale_data() -> None:
    [issue] = kinds(orders())["stale"]
    assert issue.details["newest"].startswith("2026-10")
    fresh = orders()
    fresh["created_at"] += pd.Timestamp("2026-11-09") - fresh["created_at"].max()
    assert "stale" not in kinds(fresh)


def test_monthly_data_is_judged_by_months() -> None:
    months = pd.date_range("2025-01-01", "2026-10-01", freq="MS")
    frame = pd.DataFrame(
        {"month": months.repeat(4), "region": ["N", "S", "E", "W"] * len(months), "target": 1.0}
    )
    columns = [
        ColumnInfo("month", "Month", "date"),
        ColumnInfo("region", "Region", "text"),
        ColumnInfo("target", "Target", "number"),
    ]
    found = kinds(frame, columns)
    assert "stale" not in found  # one row a month: October is recent enough
    assert "incomplete_period" not in found


def test_nulls_empty_columns_duplicates_and_copies() -> None:
    frame = pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 19],
            "email": ["a@x.in"] * 10 + [None] * 10,
            "notes": [None] * 20,
        }
    )
    frame.loc[19, "email"] = "a@x.in"
    frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    columns = [
        ColumnInfo("order_id", "Order id", "integer"),
        ColumnInfo("email", "Email", "text"),
        ColumnInfo("notes", "Notes", "text"),
    ]
    found = kinds(frame, columns)
    assert found["empty_column"][0].message == "The column Notes is empty."
    assert found["nulls"][0].details["share_pct"] == 43
    assert found["duplicate_key"][0].details["repeated"] == 2
    assert found["duplicate_rows"][0].details["rows"] == 1


def test_currency_mismatch_and_unparsed_values() -> None:
    table = parse_rows(
        [["Amount", "Paid"], *[[f"₹{i}00", str(i)] for i in range(1, 30)], ["$50", "TBD"]]
    )
    found = kinds(table.frame, table.columns)
    assert found["currency_mismatch"][0].details["currencies"] == ["$", "₹"]
    unparsed = found["unparsed_values"][0]
    assert (
        unparsed.message
        == "1 values in Paid are not numbers (for example 'TBD') and were left empty."
    )


def test_truncation_and_total_row_notes() -> None:
    found = kinds(orders(days=10), truncated=True, total_row_dropped=True, max_rows=500)
    assert found["truncated"][0].message.startswith("Only the first 500 rows were read.")
    assert found["total_row_dropped"][0].severity == "info"


def test_columns_from_schema_round_trip() -> None:
    table = parse_rows([["Date", "Sales (₹)"], ["05/01/2026", "₹1,200"]])
    assert columns_from_schema(table.schema_json()) == table.columns


@pytest.mark.skipif(not (FIXTURES / "shopify_orders.csv").exists(), reason="run `make fixtures`")
def test_planted_finding_7_the_missing_website_week() -> None:
    """ANSWER_KEY finding 7: Website orders for the week of 2026-03-09 are missing."""
    data = (FIXTURES / "shopify_orders.csv").read_bytes()
    table = parse_rows(file_formats.rows("csv", data, "shopify_orders.csv", "data"))
    found = kinds(table.frame, table.columns)
    [issue] = found["missing_segment"]
    assert issue.column == "sales_channel"
    assert issue.details["value"] == "Website"
    assert issue.details["period_start"] == "2026-03-09"
    assert issue.details["rows"] == 0
    assert "incomplete_period" not in found  # the other channels carry on as usual
