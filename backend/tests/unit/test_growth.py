"""Growth signals (BUILD_SPEC 10.3) against the demo company's answer key (planted finding 6) and synthetic data."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.analytics import growth
from app.analytics.common import Analysis, AnalyticsError, Period, metric_info
from app.data.workspace import Workspace
from app.dictionary.model import ColumnEntry, DatasetEntry, Dictionary, Metric
from tests import analytics_data as demo

YEAR = Period(date(2025, 10, 1), date(2026, 9, 30))


@pytest.fixture(scope="module")
def orders() -> Analysis:
    return demo.analysis("shopify_orders", "shopify_customers", "shopify_products")


class TestPlantedFinding:
    def test_west_beauty_is_the_top_signal(self, orders: Analysis) -> None:
        result = growth.growth_signals(orders, "orders", YEAR)
        top = result.facts["top"]
        assert top["segment"] == "West × Beauty"  # noqa: RUF001
        # Orders rose every month from May to September 2026 (ANSWER_KEY.md, finding 6): at least 4 in a row.
        assert top["periods"] >= 4
        assert top["start_value"] < top["end_value"] == 210
        # "driven by repeat customers"
        assert top["repeat_share_of_increase_pct"] > 50

    def test_revenue_finds_it_too(self, orders: Analysis) -> None:
        result = growth.growth_signals(orders, "revenue", YEAR)
        assert result.facts["top"]["segment"] == "West × Beauty"  # noqa: RUF001
        assert result.facts["unit"] == "INR"

    def test_the_season_alone_is_not_a_streak(self, orders: Analysis) -> None:
        """Every segment sells more from June to September; only a segment that gains share is a signal."""
        result = growth.growth_signals(orders, "orders", YEAR)
        streaks = result.table[result.table["kind"] == "streak"]
        assert len(streaks) <= 3
        assert "South" not in " ".join(streaks["segment"])  # the South fell in Q3 (finding 1)

    def test_beating_last_year_is_reported_for_the_whole(self, orders: Analysis) -> None:
        result = growth.growth_signals(orders, "orders", YEAR)
        yearly = result.table[result.table["kind"] == "beats_last_year"]
        assert len(yearly) == 1 and int(yearly["periods"].iloc[0]) >= 3

    def test_a_running_month_is_left_out(self, orders: Analysis) -> None:
        result = growth.growth_signals(
            orders, "orders", Period(date(2025, 10, 1), date(2026, 9, 20))
        )
        assert result.facts["last_period"] == "2026-08-01"

    def test_default_dimensions_look_like_business_segments(self, orders: Analysis) -> None:
        info = metric_info(orders.dictionary, "orders")
        dims = growth.default_dimensions(orders, info)
        assert dims[:2] == ["shipping_region", "product_type"]
        assert "financial_status" not in dims and "currency" not in dims


def _synthetic(values: dict[str, list[float]]) -> Analysis:
    months = pd.date_range("2025-01-01", periods=len(next(iter(values.values()))), freq="MS")
    rows = []
    for segment, series in values.items():
        for month, value in zip(months, series, strict=True):
            rows.append({"day": month, "segment": segment, "amount": value})
    frame = pd.DataFrame(rows)
    workspace = Workspace(timeout_seconds=10)
    workspace.load("sales", frame, {})
    dictionary = Dictionary(
        datasets={
            "sales": DatasetEntry(
                name="Sales",
                time_column="day",
                columns={
                    "day": ColumnEntry(label="Day", type="datetime", role="time"),
                    "segment": ColumnEntry(label="Segment", type="text", role="dimension"),
                    "amount": ColumnEntry(
                        label="Amount", type="number", role="measure", unit="INR"
                    ),
                },
            )
        },
        metrics={"sales": Metric(expr="SUM(amount)", dataset="sales", unit="INR", valid=True)},
    )
    return Analysis(workspace, dictionary)


class TestSynthetic:
    def test_a_flat_business_has_no_signals(self) -> None:
        analysis = _synthetic({"A": [100.0] * 14, "B": [200.0] * 14})
        result = growth.growth_signals(
            analysis, "sales", Period(date(2025, 1, 1), date(2026, 2, 28))
        )
        assert result.facts["signals"] == 0
        assert "no_growth_signals" in result.notes

    def test_a_segment_gaining_share_is_found_with_its_upside(self) -> None:
        analysis = _synthetic({"A": [100, 100, 100, 100, 120, 150, 190], "B": [500] * 7})
        result = growth.growth_signals(
            analysis, "sales", Period(date(2025, 1, 1), date(2025, 7, 31)), ["segment"]
        )
        top = result.facts["top"]
        assert top["segment"] == "A" and top["periods"] == 3
        assert top["upside_next_period"] == pytest.approx(30.0)

    def test_a_streak_needs_three_periods(self) -> None:
        analysis = _synthetic({"A": [100, 100, 100, 100, 100, 150, 190], "B": [500] * 7})
        result = growth.growth_signals(
            analysis, "sales", Period(date(2025, 1, 1), date(2025, 7, 31)), ["segment"]
        )
        assert "top" not in result.facts

    def test_daily_grain_is_refused(self) -> None:
        analysis = _synthetic({"A": [1.0] * 6})
        with pytest.raises(AnalyticsError) as error:
            growth.growth_signals(
                analysis, "sales", Period(date(2025, 1, 1), date(2025, 6, 30)), grain="day"
            )
        assert error.value.code == "bad_grain"
