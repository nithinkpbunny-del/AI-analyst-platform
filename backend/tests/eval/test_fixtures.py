"""The demo company must contain every planted finding (BUILD_SPEC 12.1).

These tests read the *written files* (CSV/XLSX) and measure each finding with plain pandas, independently of the
generator and of ``answer_key.py``. They protect the eval harness: if a later change to the generator hides a
finding, a test here fails.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

import generate_fixtures as gf
import pandas as pd
import pytest

pytestmark = pytest.mark.slow

Frames = dict[str, Any]
REGIONS = ("North", "South", "East", "West")


def _read_all(directory: Path) -> Frames:
    orders = pd.read_csv(directory / "shopify_orders.csv")
    orders["ts"] = pd.to_datetime(orders["created_at"].str[:19])
    orders["date"] = orders["ts"].dt.normalize()
    orders["net"] = orders["total_price"] - orders["total_refunds"]
    customers = pd.read_csv(directory / "shopify_customers.csv")
    customers["signup"] = pd.to_datetime(customers["created_at"].str[:19]).dt.normalize()
    sheets = pd.read_excel(directory / "marketing_sheet.xlsx", sheet_name=None)
    targets = sheets.pop("Targets")
    campaigns = pd.concat(sheets.values(), keys=sheets.keys(), names=["quarter"]).reset_index(
        level=0
    )
    return {
        "orders": orders,
        "customers": customers,
        "products": pd.read_csv(directory / "shopify_products.csv"),
        "payments": pd.read_csv(directory / "razorpay_payments.csv"),
        "campaigns": campaigns,
        "targets": targets,
        "budget": pd.read_excel(
            directory / "zoho_budget_expenses.xlsx", sheet_name="Budget vs Actual"
        ),
        "sheet_names": list(sheets.keys()),
        "key": (directory / "ANSWER_KEY.md").read_text(encoding="utf-8"),
    }


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("fixtures")
    gf.generate(directory)
    return directory


@pytest.fixture(scope="module")
def data(fixture_dir: Path) -> Frames:
    return _read_all(fixture_dir)


def _quarter_revenue(orders: pd.DataFrame, start: str, end: str) -> pd.Series[Any]:
    window = orders[(orders["ts"] >= start) & (orders["ts"] < end)]
    return window.groupby("shipping_region")["net"].sum()


# --------------------------------------------------------------------------------------------------------------
# Structure and integrity
# --------------------------------------------------------------------------------------------------------------
def test_every_source_file_is_written(fixture_dir: Path) -> None:
    for name in (
        "shopify_orders.csv",
        "shopify_customers.csv",
        "shopify_products.csv",
        "razorpay_payments.csv",
        "marketing_sheet.xlsx",
        "zoho_budget_expenses.xlsx",
        "ANSWER_KEY.md",
    ):
        assert (fixture_dir / name).stat().st_size > 0, name


def test_generation_is_deterministic(data: Frames, tmp_path: Path, fixture_dir: Path) -> None:
    gf.generate(tmp_path)
    for name in (
        "shopify_orders.csv",
        "shopify_customers.csv",
        "shopify_products.csv",
        "razorpay_payments.csv",
        "ANSWER_KEY.md",
    ):
        assert (tmp_path / name).read_bytes() == (fixture_dir / name).read_bytes(), name
    for name in ("marketing_sheet.xlsx", "zoho_budget_expenses.xlsx"):
        first = pd.read_excel(tmp_path / name, sheet_name=None)
        second = pd.read_excel(fixture_dir / name, sheet_name=None)
        assert first.keys() == second.keys()
        for sheet in first:
            pd.testing.assert_frame_equal(first[sheet], second[sheet])


def test_a_different_seed_gives_different_data(tmp_path: Path, fixture_dir: Path) -> None:
    gf.generate(tmp_path, seed=7, write_key=False)
    assert (tmp_path / "shopify_orders.csv").read_bytes() != (
        fixture_dir / "shopify_orders.csv"
    ).read_bytes()


def test_customer_mix_matches_the_spec(data: Frames) -> None:
    customers = data["customers"]
    assert len(customers) == 3000
    assert customers["id"].is_unique
    shares = customers["region"].value_counts(normalize=True)
    assert {k: round(v, 2) for k, v in shares.items()} == {
        "South": 0.30,
        "North": 0.27,
        "West": 0.25,
        "East": 0.18,
    }
    assert customers["segment"].value_counts(normalize=True)["Small Business"] == pytest.approx(
        0.15, abs=0.001
    )
    channel = customers["acquisition_channel"].value_counts(normalize=True)
    assert channel["Organic"] == pytest.approx(0.30, abs=0.001)
    assert channel["YouTube"] == pytest.approx(0.05, abs=0.001)
    assert customers["signup"].min() >= pd.Timestamp("2024-06-01")
    assert customers["signup"].max() <= pd.Timestamp("2026-09-30")
    cities = customers.groupby("region")["city"].unique()
    for region, expected in gf.REGIONS.items():
        assert set(cities[region]) == {city for city, _ in expected}


def test_product_catalogue_matches_the_spec(data: Frames) -> None:
    products = data["products"]
    assert len(products) == 40
    assert products["sku"].is_unique
    for category in gf.CATEGORIES:
        rows = products[products["product_type"] == category.name]
        assert len(rows) == 8
        assert rows["price"].between(category.price_min, category.price_max).all()
        assert (rows["cost_per_item"] / rows["price"]).round(2).eq(category.cost_pct).all()


def test_orders_are_consistent(data: Frames) -> None:
    orders, customers, products = data["orders"], data["customers"], data["products"]
    assert 24_000 < len(orders) < 34_000
    assert orders["id"].is_unique and orders["order_number"].is_unique
    assert orders["ts"].is_monotonic_increasing
    assert orders["ts"].min() >= pd.Timestamp("2025-01-01") and orders["ts"].max() < pd.Timestamp(
        "2026-10-01"
    )
    assert orders["customer_id"].isin(customers["id"]).all()
    assert orders["product_id"].isin(products["id"]).all()
    assert (
        orders["total_line_items_price"] - orders["total_discounts"] - orders["total_price"]
    ).abs().max() < 0.011
    assert (
        orders["quantity"] * orders["unit_price"] - orders["total_line_items_price"]
    ).abs().max() < 0.011
    assert (orders["total_refunds"] <= orders["total_price"]).all()
    assert set(orders["sales_channel"]) == {"App", "Website", "Marketplace"}
    channel_share = orders["sales_channel"].value_counts(normalize=True)
    assert channel_share["App"] == pytest.approx(0.50, abs=0.03)
    assert channel_share["Marketplace"] == pytest.approx(0.20, abs=0.03)
    merged = orders.merge(
        customers[["id", "signup", "region", "city"]],
        left_on="customer_id",
        right_on="id",
        suffixes=("", "_c"),
    )
    assert (merged["date"] > merged["signup"]).all(), "customers order only after signing up"
    assert (merged["shipping_region"] == merged["region"]).all()
    assert (merged["shipping_city"] == merged["city"]).all()


def test_order_volume_follows_growth_and_seasonality(data: Frames) -> None:
    monthly = data["orders"].groupby(data["orders"]["ts"].dt.to_period("M")).size()
    assert len(monthly) == 21
    for month, expected in (
        (pd.Period("2025-01"), 935),
        (pd.Period("2025-10"), 1100 * 1.02**9 * 1.35),
        (pd.Period("2026-05"), 1100 * 1.02**16),
    ):
        assert monthly[month] == pytest.approx(expected, rel=0.15), month
    assert monthly[pd.Period("2025-11")] > monthly[pd.Period("2025-09")]


def test_small_business_customers_order_more_and_buy_more(data: Frames) -> None:
    orders = data["orders"].merge(
        data["customers"][["id", "segment"]],
        left_on="customer_id",
        right_on="id",
        suffixes=("", "_c"),
    )
    per_customer = orders.groupby("segment").size() / data["customers"]["segment"].value_counts()
    assert per_customer["Small Business"] / per_customer["Consumer"] == pytest.approx(1.6, rel=0.2)
    quantity = orders.groupby("segment")["quantity"].mean()
    assert quantity["Small Business"] / quantity["Consumer"] == pytest.approx(2.0, rel=0.15)


def test_payments_reconcile_with_orders(data: Frames) -> None:
    payments, orders = data["payments"], data["orders"]
    assert payments["id"].is_unique
    assert set(payments["status"]) == {"captured", "refunded"}
    joined = payments.merge(orders, left_on="notes_order_name", right_on="name")
    assert len(joined) == len(payments)
    assert (joined["amount"] == (joined["total_price"] * 100).round().astype(int)).all(), (
        "amounts are in paise"
    )
    assert set(joined["payment_gateway"]) == {"razorpay"}
    assert (payments["created_at"] > 1_700_000_000).all(), "created_at is unix time"
    assert (payments["amount_refunded"] > 0).sum() == (payments["status"] == "refunded").sum()


def test_answer_key_is_generated_from_the_data(data: Frames) -> None:
    key = data["key"]
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8."):
        assert heading in key
    q2 = _quarter_revenue(data["orders"], "2026-04-01", "2026-07-01")
    q3 = _quarter_revenue(data["orders"], "2026-07-01", "2026-10-01")
    south_change = (q3["South"] / q2["South"] - 1) * 100
    assert f"{south_change:+.1f}%" in key


# --------------------------------------------------------------------------------------------------------------
# Finding 1: South falls ~18% in Q3 2026 while the others grow; the cause is repeat customers after a price hike
# --------------------------------------------------------------------------------------------------------------
def test_finding_1_south_is_the_only_region_that_shrinks(data: Frames) -> None:
    q2 = _quarter_revenue(data["orders"], "2026-04-01", "2026-07-01")
    q3 = _quarter_revenue(data["orders"], "2026-07-01", "2026-10-01")
    change = (q3 / q2 - 1) * 100
    assert -20.5 < change["South"] < -15.5
    for region in ("North", "East", "West"):
        assert change[region] > 5, region


def test_finding_1_electronics_price_rose_12_percent_in_the_south_only(data: Frames) -> None:
    electronics = data["orders"][data["orders"]["product_type"] == "Electronics"]
    ratios = {}
    for label, subset in (
        ("south", electronics[electronics["shipping_region"] == "South"]),
        ("others", electronics[electronics["shipping_region"] != "South"]),
    ):
        before = subset[subset["ts"] < "2026-07-01"].groupby("product_id")["unit_price"].max()
        after = subset[subset["ts"] >= "2026-07-01"].groupby("product_id")["unit_price"].max()
        ratios[label] = (after / before).dropna()
    assert ratios["south"].between(1.11, 1.13).all()
    assert (ratios["others"] == 1.0).all()
    first_hiked_day = electronics[
        (electronics["shipping_region"] == "South")
        & (
            electronics["unit_price"]
            > electronics["product_id"].map(data["products"].set_index("id")["price"])
        )
    ]["date"].min()
    assert first_hiked_day == pd.Timestamp("2026-07-01")


def test_finding_1_existing_south_customers_order_less_often_new_ones_do_not(data: Frames) -> None:
    orders, customers = data["orders"], data["customers"]
    q2 = orders[(orders["ts"] >= "2026-04-01") & (orders["ts"] < "2026-07-01")]
    q3 = orders[(orders["ts"] >= "2026-07-01") & (orders["ts"] < "2026-10-01")]

    cohort = customers[customers["signup"] < "2026-04-01"]
    south_ids = cohort.loc[cohort["region"] == "South", "id"]
    other_ids = cohort.loc[cohort["region"] != "South", "id"]
    south_trend = q3["customer_id"].isin(south_ids).sum() / q2["customer_id"].isin(south_ids).sum()
    other_trend = q3["customer_id"].isin(other_ids).sum() / q2["customer_id"].isin(other_ids).sum()
    assert south_trend / other_trend < 0.75, (
        "existing South customers order at least 25% less often"
    )
    assert 1 / (south_trend / other_trend) - 1 > 0.35, (
        "their average gap between orders is clearly longer"
    )

    new_customers = customers[customers["signup"] >= "2026-07-01"]
    exposure = (pd.Timestamp("2026-09-30") - new_customers["signup"]).dt.days
    rates = {}
    for label, is_south in (("south", True), ("others", False)):
        mask = (new_customers["region"] == "South") == is_south
        rates[label] = (
            q3["customer_id"].isin(new_customers.loc[mask, "id"]).sum() / exposure[mask].sum()
        )
    assert 0.7 < rates["south"] / rates["others"] < 1.3, (
        "new-customer ordering is roughly unchanged"
    )


def test_finding_1_the_south_drop_comes_from_existing_customers(data: Frames) -> None:
    orders = data["orders"].merge(
        data["customers"][["id", "signup"]],
        left_on="customer_id",
        right_on="id",
        suffixes=("", "_c"),
    )
    south = orders[orders["shipping_region"] == "South"]
    q2 = south[(south["ts"] >= "2026-04-01") & (south["ts"] < "2026-07-01")]
    q3 = south[(south["ts"] >= "2026-07-01") & (south["ts"] < "2026-10-01")]
    existing_drop = q3.loc[q3["signup"] < "2026-07-01", "net"].sum() - q2["net"].sum()
    total_drop = q3["net"].sum() - q2["net"].sum()
    assert total_drop < 0
    assert existing_drop < 0 and existing_drop / total_drop > 1.0, (
        "new customers add revenue; existing lose more"
    )


# --------------------------------------------------------------------------------------------------------------
# Finding 2: channel efficiency
# --------------------------------------------------------------------------------------------------------------
def test_finding_2_channel_roas(data: Frames) -> None:
    assert data["sheet_names"] == [
        "Q1 2025",
        "Q2 2025",
        "Q3 2025",
        "Q4 2025",
        "Q1 2026",
        "Q2 2026",
        "Q3 2026",
    ]
    campaigns = data["campaigns"]
    assert len(campaigns) == 7 * 6, "one campaign per channel per quarter"
    by_channel = campaigns.groupby("Channel")[["Spend (INR)", "Revenue (INR)"]].sum()
    roas = by_channel["Revenue (INR)"] / by_channel["Spend (INR)"]
    expected = {"Email": 6.4, "Instagram": 4.1, "Google Ads": 1.6, "YouTube": 0.8}
    for channel, value in expected.items():
        assert roas[channel] == pytest.approx(value, abs=0.05), channel
    assert roas.idxmax() == "Email" and roas.nlargest(2).index[1] == "Instagram"
    assert roas["YouTube"] < 1.0 and roas.idxmin() == "YouTube", "YouTube loses money"
    assert by_channel["Spend (INR)"].idxmax() == "Google Ads", "Google Ads has the largest spend"


def test_finding_2_funnel_numbers_are_internally_consistent(data: Frames) -> None:
    c = data["campaigns"]
    assert (c["Clicks"] <= c["Impressions"]).all() and (c["Conversions"] <= c["Clicks"]).all()
    assert ((c["Revenue (INR)"] / c["Spend (INR)"]).round(2) == c["ROAS"]).all()
    assert (c["CTR"] - c["Clicks"] / c["Impressions"]).abs().max() < 0.0001


# --------------------------------------------------------------------------------------------------------------
# Finding 3: budget variance
# --------------------------------------------------------------------------------------------------------------
def test_finding_3_marketing_is_over_budget_in_q3_2026_and_operations_in_the_festive_months(
    data: Frames,
) -> None:
    budget = data["budget"]
    assert len(budget) == 21 * 5
    budget["ratio"] = budget["Actual (INR)"] / budget["Budget (INR)"]

    festive_months = [pd.Timestamp("2025-10-01"), pd.Timestamp("2025-11-01")]
    marketing = budget[(budget["Department"] == "Marketing") & (budget["Month"] >= "2026-07-01")]
    assert len(marketing) == 3
    assert (marketing["ratio"].between(1.19, 1.31)).all()
    quarter = marketing["Actual (INR)"].sum() / marketing["Budget (INR)"].sum() - 1
    assert quarter == pytest.approx(0.22, abs=0.01)

    festive = budget[(budget["Department"] == "Operations") & budget["Month"].isin(festive_months)]
    assert len(festive) == 2
    assert festive["ratio"].between(1.08, 1.12).all()

    planted = ((budget["Department"] == "Marketing") & (budget["Month"] >= "2026-07-01")) | (
        (budget["Department"] == "Operations") & budget["Month"].isin(festive_months)
    )
    assert budget.loc[~planted, "ratio"].between(0.93, 1.07).all(), (
        "nothing else is a large variance"
    )


def test_finding_3_budgets_grow_one_and_a_half_percent_a_month(data: Frames) -> None:
    marketing = data["budget"][data["budget"]["Department"] == "Marketing"].sort_values("Month")
    assert marketing["Budget (INR)"].iloc[0] == 700_000
    growth = marketing["Budget (INR)"].iloc[-1] / marketing["Budget (INR)"].iloc[0]
    assert growth == pytest.approx(1.015**20, rel=0.002)


# --------------------------------------------------------------------------------------------------------------
# Finding 4: discounting and margin by category
# --------------------------------------------------------------------------------------------------------------
def test_finding_4_fashion_discounts_most_and_electronics_has_the_thinnest_margin(
    data: Frames,
) -> None:
    orders = data["orders"].merge(
        data["products"][["id", "cost_per_item"]],
        left_on="product_id",
        right_on="id",
        suffixes=("", "_p"),
    )
    orders["discount_rate"] = orders["total_discounts"] / orders["total_line_items_price"]
    orders["cost"] = orders["cost_per_item"] * orders["quantity"]
    discount = orders.groupby("product_type")["discount_rate"].mean()
    assert discount.idxmax() == "Fashion"
    assert discount["Fashion"] == pytest.approx(0.23, abs=0.012)
    assert discount.drop("Fashion").max() < 0.15

    grouped = orders.groupby("product_type")[["total_price", "cost"]].sum()
    margin = 1 - grouped["cost"] / grouped["total_price"]
    assert margin.idxmin() == "Electronics"
    assert margin["Electronics"] < margin.drop("Electronics").min() - 0.10


def test_finding_4_festive_season_adds_eight_points_of_discount(data: Frames) -> None:
    orders = data["orders"].copy()
    orders["rate"] = orders["total_discounts"] / orders["total_line_items_price"]
    festive = orders[orders["ts"].dt.month.isin([10, 11])].groupby("product_type")["rate"].mean()
    normal = orders[~orders["ts"].dt.month.isin([10, 11])].groupby("product_type")["rate"].mean()
    assert ((festive - normal) > 0.06).all() and ((festive - normal) < 0.10).all()


# --------------------------------------------------------------------------------------------------------------
# Finding 5: one anomaly day (app outage on 2026-08-15)
# --------------------------------------------------------------------------------------------------------------
def _ratio_to_neighbours(series: pd.Series[Any]) -> pd.Series[Any]:
    """Daily count divided by the median of the same weekday in the 4 weeks before and the 4 weeks after.

    A centred window copes with seasonal level shifts (e.g. December to January) that a trailing 8-week window
    would mistake for anomalies.
    """
    daily = series.reindex(pd.date_range("2025-01-01", "2026-09-30"), fill_value=0).astype(float)
    neighbours = pd.concat([daily.shift(7 * k) for k in (-4, -3, -2, -1, 1, 2, 3, 4)], axis=1)
    ratio = (daily / neighbours.median(axis=1)).dropna()
    return ratio[(ratio.index >= "2025-02-01") & (ratio.index <= "2026-09-01")]


def test_finding_5_app_orders_collapse_on_one_day_only(data: Frames) -> None:
    app = data["orders"][data["orders"]["sales_channel"] == "App"].groupby("date").size()
    ratio = _ratio_to_neighbours(app)
    outage = pd.Timestamp("2026-08-15")
    assert ratio[outage] < 0.55, "App orders fall by roughly 60%"
    z = (ratio[outage] - ratio.drop(outage).median()) / ratio.drop(outage).std()
    assert z < -4, "a clear statistical outlier"
    assert ratio.drop(outage).min() > 0.6, "no other day in 19 months comes close"
    assert ratio.idxmin() == outage


def test_finding_5_other_channels_are_unaffected(data: Frames) -> None:
    orders = data["orders"]
    for channel in ("Website", "Marketplace"):
        series = orders[orders["sales_channel"] == channel].groupby("date").size()
        assert _ratio_to_neighbours(series)[pd.Timestamp("2026-08-15")] > 0.7, channel


# --------------------------------------------------------------------------------------------------------------
# Finding 6: growth signal, West Beauty for four months
# --------------------------------------------------------------------------------------------------------------
def test_finding_6_west_beauty_grows_four_months_in_a_row(data: Frames) -> None:
    orders = data["orders"].sort_values("ts")
    orders["is_repeat"] = orders.groupby("customer_id").cumcount() > 0
    beauty = orders[orders["product_type"] == "Beauty"]
    west = beauty[beauty["shipping_region"] == "West"]
    counts = west.groupby(west["ts"].dt.to_period("M")).size()
    months = [pd.Period(f"2026-{m:02d}") for m in range(5, 10)]
    values = [int(counts[m]) for m in months]
    assert all(b > a for a, b in pairwise(values)), values

    repeat = (
        west[west["is_repeat"]].groupby(west.loc[west["is_repeat"], "ts"].dt.to_period("M")).size()
    )
    increase = values[-1] - values[0]
    repeat_increase = int(repeat[months[-1]]) - int(repeat[months[0]])
    assert repeat_increase / increase > 0.8, "the growth comes from repeat customers"


def test_finding_6_no_other_region_shows_the_same_streak(data: Frames) -> None:
    beauty = data["orders"][data["orders"]["product_type"] == "Beauty"]
    for region in ("North", "South", "East"):
        subset = beauty[beauty["shipping_region"] == region]
        counts = subset.groupby(subset["ts"].dt.to_period("M")).size()
        values = [int(counts[pd.Period(f"2026-{m:02d}")]) for m in range(5, 10)]
        assert not all(b > a for a, b in pairwise(values)), (region, values)


# --------------------------------------------------------------------------------------------------------------
# Finding 7: data-quality gap
# --------------------------------------------------------------------------------------------------------------
def test_finding_7_website_orders_are_missing_for_the_week_of_9_march_2026(data: Frames) -> None:
    orders = data["orders"]
    website = orders[orders["sales_channel"] == "Website"].groupby("date").size()
    for day in pd.date_range("2026-03-09", "2026-03-15"):
        assert website.get(day, 0) == 0, day
    for day in pd.date_range("2026-03-02", "2026-03-08").append(
        pd.date_range("2026-03-16", "2026-03-22")
    ):
        assert website.get(day, 0) > 0, day

    other_channels = orders[
        (orders["sales_channel"] != "Website")
        & (orders["date"] >= "2026-03-09")
        & (orders["date"] <= "2026-03-15")
    ]
    assert other_channels.groupby("date").size().min() > 10, (
        "App and Marketplace carry on as normal"
    )

    zero_days = [d for d in pd.date_range("2025-01-02", "2026-09-30") if website.get(d, 0) == 0]
    assert zero_days == list(pd.date_range("2026-03-09", "2026-03-15")), (
        "no other Website gaps exist"
    )


# --------------------------------------------------------------------------------------------------------------
# Finding 8: cross-source targets
# --------------------------------------------------------------------------------------------------------------
def test_finding_8_only_the_south_misses_its_q3_2026_target(data: Frames) -> None:
    targets = data["targets"]
    assert list(targets.columns) == ["Month", "Region", "Revenue target (INR)"]
    assert len(targets) == 21 * 4
    q3_targets = (
        targets[targets["Month"] >= "2026-07-01"].groupby("Region")["Revenue target (INR)"].sum()
    )
    actual = _quarter_revenue(data["orders"], "2026-07-01", "2026-10-01")
    for region in ("North", "East", "West"):
        assert actual[region] >= q3_targets[region], region
    assert actual["South"] < q3_targets["South"] * 0.85


def test_finding_8_other_regions_meet_each_q3_month(data: Frames) -> None:
    orders = data["orders"]
    monthly = orders.groupby([orders["ts"].dt.to_period("M").dt.to_timestamp(), "shipping_region"])[
        "net"
    ].sum()
    targets = data["targets"]
    for row in targets[
        (targets["Month"] >= "2026-07-01") & (targets["Region"] != "South")
    ].itertuples():
        assert monthly[(row.Month, row.Region)] >= row[3], (row.Month, row.Region)


def test_targets_before_q3_are_a_realistic_mix_of_hits_and_misses(data: Frames) -> None:
    orders = data["orders"]
    monthly = orders.groupby([orders["ts"].dt.to_period("M").dt.to_timestamp(), "shipping_region"])[
        "net"
    ].sum()
    earlier = data["targets"][data["targets"]["Month"] < "2026-07-01"]
    hit: Callable[[Any], bool] = lambda r: monthly[(r.Month, r.Region)] >= r[3]  # noqa: E731
    outcomes = [hit(r) for r in earlier.itertuples()]
    assert 0.3 < sum(outcomes) / len(outcomes) < 0.9
