"""The analytics library on the UrbanCart demo data (BUILD_SPEC 10, 12.1): each planted finding of
``eval/fixtures/ANSWER_KEY.md`` is found by the function meant to find it, with exact numbers (P4).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.analytics import anomalies, cohorts, drivers, finance, forecast, kpis, marketing, rfm
from app.analytics.common import AnalyticsError, Period, comparison_of, metric_info, period_of
from app.dictionary.model import Metric
from tests.analytics_data import analysis, dictionary

Q2 = Period(date(2026, 4, 1), date(2026, 6, 30))
Q3 = Period(date(2026, 7, 1), date(2026, 9, 30))


def by(table: object, column: str, value: str) -> dict[str, object]:
    rows = table[table[column] == value].to_dict("records")  # type: ignore[index]
    assert rows, f"{value} not found"
    found: dict[str, object] = rows[0]
    return found


class TestPeriods:
    def test_previous_period_of_a_quarter_is_the_quarter_before(self) -> None:
        assert Q3.previous() == Q2
        assert Period(date(2026, 3, 1), date(2026, 3, 31)).previous() == Period(
            date(2026, 2, 1), date(2026, 2, 28)
        )
        assert Period(date(2026, 3, 10), date(2026, 3, 16)).previous() == Period(
            date(2026, 3, 3), date(2026, 3, 9)
        )
        assert Q3.previous_year() == Period(date(2025, 7, 1), date(2025, 9, 30))

    def test_comparisons_and_bad_input(self) -> None:
        assert comparison_of(Q3, None) == Q2
        assert comparison_of(Q3, "none") is None
        assert comparison_of(Q3, {"start": "2025-01-01", "end": "2025-01-31"}) == Period(
            date(2025, 1, 1), date(2025, 1, 31)
        )
        with pytest.raises(AnalyticsError):
            period_of({"start": "2026-09-30", "end": "2026-07-01"})
        with pytest.raises(AnalyticsError):
            period_of({"start": "July"})


class TestMetrics:
    def test_metrics_resolve_through_other_metrics(self) -> None:
        info = metric_info(dictionary(), "aov")
        assert info.table == "shopify_orders" and info.time_column == "created_at"
        assert info.additive is False
        assert metric_info(dictionary(), "revenue").additive is True

    def test_broken_and_missing_metrics_say_why(self) -> None:
        d = dictionary()
        d.metrics["gone"] = Metric(expr="COUNT(*)", dataset="no_such_table")
        d.metrics["bad"] = Metric(expr="COUNT(*)", dataset="shopify_orders", valid=False)
        for key, code in (
            ("gone", "metric_unavailable"),
            ("bad", "metric_invalid"),
            ("x", "unknown_metric"),
        ):
            with pytest.raises(AnalyticsError) as caught:
                metric_info(d, key)
            assert caught.value.code == code


class TestFinding1SouthFalls:
    """South revenue -17.7% in Q3 2026 while every other region grows; repeat customers drive it."""

    def test_kpi_summary(self) -> None:
        result = kpis.kpi_summary(
            analysis(), ["revenue", "orders"], Q3, Q2, {"shipping_region": "South"}
        )
        revenue = by(result.table, "metric", "revenue")
        assert round(float(revenue["current"]), 0) == 2_801_265  # type: ignore[arg-type]
        assert round(float(revenue["previous"]), 0) == 3_404_299  # type: ignore[arg-type]
        assert round(float(revenue["change_pct"]), 1) == -17.7  # type: ignore[arg-type]

    def test_driver_analysis_names_the_south_and_repeat_customers(self) -> None:
        result = drivers.driver_analysis(analysis(), "revenue", Q3, Q2, ["shipping_region"])
        regions = result.facts["by_dimension"]["shipping_region"]
        assert regions["falling"] == ["South"]
        assert sorted(regions["rising"]) == ["East", "North", "West"]
        assert regions["largest_mover"] == "South"
        changes = {r["value"]: round(r["change_pct"], 1) for r in result.table.to_dict("records")}
        assert changes == {"South": -17.7, "West": 18.5, "East": 17.0, "North": 10.1}
        south = drivers.driver_analysis(
            analysis(), "revenue", Q3, Q2, ["product_type"], {"shipping_region": "South"}
        )
        split = south.facts["new_vs_repeat"]
        assert split["repeat_change"] < 0 and split["repeat_share_of_change"] > 80
        assert (
            split["repeat_rows_per_customer_change_pct"] < -15
        )  # existing customers order less often

    def test_a_dimension_of_a_joined_table(self) -> None:
        result = drivers.driver_analysis(
            analysis(), "revenue", Q3, Q2, ["shopify_customers.region"]
        )
        assert result.facts["by_dimension"]["shopify_customers.region"]["falling"] == ["South"]
        assert "shopify_customers" in result.tables_used

    def test_volume_and_rate_effects_add_up(self) -> None:
        result = drivers.driver_analysis(analysis(), "revenue", Q3, Q2, ["shipping_region"])
        for row in result.table.to_dict("records"):
            assert row["volume_effect"] + row["rate_effect"] == pytest.approx(row["change"])
        assert sum(r["share_of_change"] for r in result.table.to_dict("records")) == pytest.approx(
            100
        )


class TestFinding2Channels:
    def test_roas_by_channel(self) -> None:
        result = marketing.marketing_efficiency(analysis())
        roas = {r["name"]: round(r["roas"], 2) for r in result.table.to_dict("records")}
        assert roas == {
            "Email": 6.4,
            "Instagram": 4.1,
            "Influencer": 2.6,
            "Facebook": 2.1,
            "Google Ads": 1.6,
            "YouTube": 0.8,
        }
        assert result.facts["losing_money"] == ["YouTube"]
        assert result.facts["largest_spend"]["name"] == "Google Ads"
        assert round(result.facts["largest_spend"]["share_of_spend_pct"], 1) == 33.2
        assert "attribution_limits" in result.notes

    def test_a_period_limits_the_campaigns(self) -> None:
        result = marketing.marketing_efficiency(analysis(), period=Q3)
        assert (
            result.facts["total_spend"]
            < marketing.marketing_efficiency(analysis()).facts["total_spend"]
        )


class TestFinding3Budget:
    def test_marketing_is_22_percent_over_in_q3(self) -> None:
        result = finance.budget_variance(analysis(), Q3)
        marketing_row = by(result.table, "line", "Marketing")
        assert round(float(marketing_row["variance_pct"]), 1) == 22.0  # type: ignore[arg-type]
        assert [f["line"] for f in result.facts["flagged"]] == ["Marketing"]

    def test_operations_runs_over_in_the_festive_months(self) -> None:
        festive = Period(date(2025, 10, 1), date(2025, 11, 30))
        result = finance.budget_variance(analysis(), festive, by_month=True)
        ops = result.table[result.table["line"] == "Operations"]
        assert [round(v) for v in ops["variance_pct"]] == [9, 11]
        assert set(ops["flag"]) == {"over"}

    def test_pnl_needs_accounting_data(self) -> None:
        with pytest.raises(AnalyticsError) as caught:
            finance.pnl(analysis())
        assert caught.value.code == "needs_accounting_data"


class TestFinding4Discounts:
    def test_fashion_discounts_most_and_electronics_has_the_thinnest_margin(self) -> None:
        a = analysis("shopify_orders", "shopify_products")
        table = a.workspace.frame(
            "SELECT o.product_type, SUM(o.total_discounts) / SUM(o.total_line_items_price) * 100 AS discount_pct, "
            "(SUM(o.total_price) - SUM(o.quantity * p.cost_per_item)) / SUM(o.total_price) * 100 AS margin_pct "
            "FROM shopify_orders o JOIN shopify_products p ON o.product_id = p.id GROUP BY 1"
        )
        assert table.sort_values("discount_pct").iloc[-1]["product_type"] == "Fashion"
        assert table.sort_values("margin_pct").iloc[0]["product_type"] == "Electronics"


class TestFinding5AppOutage:
    def test_the_outage_day_is_flagged_and_nothing_else_in_august(self) -> None:
        result = anomalies.detect_anomalies(
            analysis(), "orders", Period(date(2026, 8, 1), date(2026, 8, 31)), "sales_channel"
        )
        flags = result.table.to_dict("records")
        assert [(f["series"], f["bucket"], f["direction"]) for f in flags] == [
            ("App", "2026-08-15", "down")
        ]
        assert flags[0]["value"] == 13 and flags[0]["baseline"] == 27
        assert result.facts["series_checked"] == ["Total", "App", "Website", "Marketplace"]


class TestFinding7MissingWeek:
    def test_the_missing_website_week_shows_as_seven_zero_days(self) -> None:
        result = anomalies.detect_anomalies(
            analysis(), "orders", Period(date(2026, 3, 1), date(2026, 3, 31)), "sales_channel"
        )
        website = result.table[result.table["series"] == "Website"]
        assert sorted(website["bucket"]) == [f"2026-03-{d:02d}" for d in range(9, 16)]
        assert set(website["value"]) == {0.0}


class TestTrendAndForecast:
    def test_monthly_orders_by_channel(self) -> None:
        result = kpis.trend(
            analysis(),
            "orders",
            Period(date(2026, 1, 1), date(2026, 9, 30)),
            "month",
            "sales_channel",
        )
        assert set(result.table["series"]) == {"App", "Website", "Marketplace"}
        assert result.facts["buckets"] == 9
        total = result.table.groupby("bucket")["value"].sum()
        assert total.iloc[0] == 1204

    def test_a_partial_last_bucket_is_noted(self) -> None:
        result = kpis.trend(
            analysis(), "revenue", Period(date(2026, 7, 1), date(2026, 9, 15)), "month"
        )
        assert "partial_last_bucket:month" in result.notes
        assert result.facts["max"]["bucket"]

    def test_forecast_has_a_range_a_method_and_a_backtest(self) -> None:
        result = forecast.forecast(analysis(), "revenue", "month", 3)
        facts = result.facts
        assert facts["method"] == "trend_x_seasonal_index"  # 21 months: less than two seasons
        assert facts["horizon"] == 3 and facts["backtest_mape_pct"] is not None
        future = result.table[result.table["kind"] == "forecast"]
        assert len(future) == 3
        assert (future["low"] < future["value"]).all() and (future["value"] < future["high"]).all()
        assert str(future["bucket"].iloc[0]) == "2026-10-01"
        # October is a festive month: higher than September.
        september = result.table[result.table["kind"] == "actual"]["value"].iloc[-1]
        assert future["value"].iloc[0] > september

    def test_holt_winters_with_two_seasons(self) -> None:
        result = forecast.forecast(analysis(), "orders", "day", 7)
        assert result.facts["method"] == "holt_winters"

    def test_too_little_history(self) -> None:
        with pytest.raises(AnalyticsError) as caught:
            forecast.forecast(
                analysis(), "revenue", "quarter", 2, {"created_at": {"from": "2026-07-01"}}
            )
        assert caught.value.code == "too_little_history"


class TestCustomers:
    def test_rfm_segments_cover_every_customer(self) -> None:
        result = rfm.rfm_segments(analysis(), "revenue")
        assert result.table["customers"].sum() == result.facts["customers"]
        assert result.table["share_of_customers_pct"].sum() == pytest.approx(100)
        frequency = dict(zip(result.table["segment"], result.table["avg_frequency"], strict=True))
        assert frequency["Champions"] > frequency["Hibernating"]

    def test_cohort_retention_leaves_the_future_empty(self) -> None:
        result = cohorts.cohort_retention(analysis(), "revenue")
        last = result.table.iloc[-1]
        assert last["cohort"] == "2026-09" and pd.isna(last["m1"])
        assert 20 < result.facts["average_month_1_retention_pct"] < 60


class TestFilters:
    def test_filters_by_value_list_and_range(self) -> None:
        one = kpis.kpi_summary(analysis(), ["orders"], Q3, None, {"sales_channel": "App"})
        two = kpis.kpi_summary(
            analysis(), ["orders"], Q3, None, {"sales_channel": ["App", "Website"]}
        )
        assert one.table["current"].iloc[0] < two.table["current"].iloc[0]
        ranged = kpis.kpi_summary(
            analysis(), ["orders"], Q3, None, {"total_price": {"from": 1000, "to": 2000}}
        )
        assert 0 < ranged.table["current"].iloc[0] < two.table["current"].iloc[0]

    def test_unknown_columns_and_joins_are_refused(self) -> None:
        with pytest.raises(AnalyticsError) as caught:
            kpis.kpi_summary(analysis(), ["orders"], Q3, None, {"no_such_column": 1})
        assert caught.value.code == "unknown_column"
        with pytest.raises(AnalyticsError) as caught:
            drivers.driver_analysis(analysis(), "revenue", Q3, Q2, ["targets.region"])
        assert caught.value.code == "no_join"

    def test_a_value_cannot_inject_sql(self) -> None:
        result = kpis.kpi_summary(
            analysis(), ["orders"], Q3, None, {"sales_channel": "App' OR '1'='1"}
        )
        assert result.table["current"].iloc[0] == 0
