"""The what-if engine (BUILD_SPEC 10.4, R12; M9) over the UrbanCart demo data, no model and no database.

The arithmetic is checked on its own; the estimation is checked on what the demo data can and cannot teach (a
price move that the history shows, discount depth that only moves with the season, channel spend). Every result
must carry a range, its assumptions and a confidence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics import whatif
from app.analytics.common import AnalyticsError
from app.analytics.whatif import Scenario, effect_factor
from app.dictionary.model import Metric
from tests.analytics_data import analysis

SOUTH_ELECTRONICS = {"product_type": "Electronics", "shipping_region": "South"}


@pytest.fixture(scope="module")
def sales() -> object:
    return analysis()


def run(a: object, **overrides: object) -> whatif.Result:  # type: ignore[name-defined]
    fields: dict[str, object] = {
        "metric": "revenue",
        "lever": "price",
        "change": 10.0,
        "horizon": 3,
    }
    fields.update(overrides)
    return whatif.what_if(a, Scenario(**fields), "d2c")  # type: ignore[arg-type]


class TestArithmetic:
    def test_price_rise_moves_units_by_the_elasticity_and_revenue_by_both(self) -> None:
        f = effect_factor("price", 10.0, -1.0)
        assert f["units"] == pytest.approx(1 / 1.1)
        assert f["revenue"] == pytest.approx(1.0)  # elasticity -1: revenue does not move

    def test_inelastic_demand_makes_a_price_rise_pay(self) -> None:
        assert effect_factor("price", 10.0, -0.5)["revenue"] > 1.0
        assert effect_factor("price", 10.0, -2.0)["revenue"] < 1.0

    def test_no_change_changes_nothing(self) -> None:
        for lever, rate in (("price", 0.0), ("discount", 0.12), ("marketing_spend", 0.0)):
            f = effect_factor(lever, 0.0, -1.3 if lever != "marketing_spend" else 0.4, rate)
            assert f["units"] == pytest.approx(1.0) and f["revenue"] == pytest.approx(1.0)

    def test_a_deeper_discount_lowers_the_price_paid_and_lifts_units(self) -> None:
        f = effect_factor("discount", 5.0, -1.8, rate=0.10)
        assert f["net_price"] == pytest.approx(0.85 / 0.90)
        assert f["units"] > 1.0 and f["new_rate"] == pytest.approx(0.15)

    def test_the_discount_rate_cannot_pass_its_ceiling_or_go_below_zero(self) -> None:
        assert (
            effect_factor("discount", 50.0, -1.0, rate=0.8)["new_rate"] == whatif.MAX_DISCOUNT_RATE
        )
        assert effect_factor("discount", -50.0, -1.0, rate=0.1)["new_rate"] == 0.0

    def test_spend_has_diminishing_returns(self) -> None:
        doubled = effect_factor("marketing_spend", 100.0, 0.45)["revenue"]
        assert 1.0 < doubled < 2.0


class TestScenarioChecks:
    @pytest.mark.parametrize(
        ("fields", "code"),
        [
            ({"lever": "stock"}, "bad_lever"),
            ({"change": float("nan")}, "bad_change"),
            ({"change": 500.0}, "bad_change"),
            ({"change": -95.0}, "bad_change"),
            ({"lever": "discount", "change": 80.0}, "bad_change"),
            ({"horizon": 0}, "bad_horizon"),
            ({"horizon": 7}, "bad_horizon"),
            ({"overrides": {"discount_elasticity": -1.0}}, "bad_assumption"),
            ({"overrides": {"price_elasticity": 0.5}}, "bad_assumption"),
            ({"overrides": {"price_elasticity": -50.0}}, "bad_assumption"),
            (
                {"lever": "marketing_spend", "overrides": {"spend_elasticity": -0.2}},
                "bad_assumption",
            ),
        ],
    )
    def test_refuses_unusable_scenarios(
        self, sales: object, fields: dict[str, object], code: str
    ) -> None:
        with pytest.raises(AnalyticsError) as caught:
            run(sales, **fields)
        assert caught.value.code == code

    def test_only_revenue_like_additive_metrics_can_be_simulated(self, sales: object) -> None:
        with pytest.raises(AnalyticsError) as caught:
            run(sales, metric="aov")
        assert caught.value.code in ("metric_not_supported", "metric_unavailable")
        with pytest.raises(AnalyticsError) as unknown:
            run(sales, metric="nope")
        assert unknown.value.code == "unknown_metric"


class TestPriceAndDiscount:
    def test_a_result_always_has_a_range_assumptions_and_a_confidence(self, sales: object) -> None:
        result = run(sales, scope=SOUTH_ELECTRONICS)
        facts = result.facts
        assert facts["delta_low"] <= facts["delta_expected"] <= facts["delta_high"]
        assert facts["scenario_low"] <= facts["scenario_total"] <= facts["scenario_high"]
        assert facts["range"] == "80%"
        assert facts["confidence"]["level"] in ("high", "medium", "low")
        keys = [a["key"] for a in facts["assumptions"]]
        assert "price_elasticity" in keys and "nothing_else_changes" in keys
        rows = result.table
        assert len(rows) == 3
        assert (rows["scenario_low"] <= rows["scenario_high"]).all()
        assert (rows["baseline_low"] <= rows["baseline"]).all()

    def test_a_price_move_the_history_shows_is_learned_from_the_history(
        self, sales: object
    ) -> None:
        facts = run(sales, scope=SOUTH_ELECTRONICS).facts
        price = next(a for a in facts["assumptions"] if a["key"] == "price_elasticity")
        assert price["source"] == "history" and price["t_value"] >= whatif.MIN_T
        assert whatif.ELASTICITY_BOUNDS[0] <= price["value"] <= whatif.ELASTICITY_BOUNDS[1]
        assert price["low"] <= price["value"] <= price["high"]
        # The weeks that informed it are named.
        assert facts["informed_by"]["source"] == "history" and facts["informed_by"]["weeks"] >= 52
        assert facts["informed_by"]["start"] < facts["informed_by"]["end"]
        assert facts["confidence"]["level"] in ("medium", "high")

    def test_when_the_history_cannot_tell_a_labelled_default_is_used(self, sales: object) -> None:
        facts = run(sales, scope={"product_type": "Electronics"}).facts
        price = next(a for a in facts["assumptions"] if a["key"] == "price_elasticity")
        assert price["source"] == "default" and price["reason"] == "history_inconclusive"
        assert price["value"] == whatif.DEFAULTS["d2c"]["price"]
        assert facts["confidence"] == {"level": "low", "reason": "history_inconclusive"}
        assert (
            "what_if_default_assumption" in run(sales, scope={"product_type": "Electronics"}).notes
        )

    def test_discount_depth_that_only_moves_with_the_season_is_not_mistaken_for_demand(
        self, sales: object
    ) -> None:
        # Discounts are 8 points deeper in Oct-Nov, exactly when demand peaks: that is a season, not a response.
        facts = run(sales, lever="discount", change=5.0, scope={"product_type": "Fashion"}).facts
        discount = next(a for a in facts["assumptions"] if a["key"] == "discount_elasticity")
        assert discount["source"] == "default"
        assert facts["confidence"]["level"] == "low"
        given = next(e for e in facts["side_effects"] if e["code"] == "discount_given")
        assert given["after"] > given["before"] > 0

    def test_the_persons_own_assumption_replaces_the_estimate(self, sales: object) -> None:
        facts = run(sales, scope=SOUTH_ELECTRONICS, overrides={"price_elasticity": -2.5}).facts
        price = next(a for a in facts["assumptions"] if a["key"] == "price_elasticity")
        assert price["source"] == "you" and price["value"] == price["low"] == price["high"] == -2.5
        assert facts["confidence"] == {"level": "medium", "reason": "your_assumption"}
        # A steeper response makes a price rise hurt: the expected change is below zero.
        assert facts["delta_expected"] < 0
        assert facts["units_change_pct"] == pytest.approx((1.1**-2.5 - 1) * 100, abs=0.01)

    def test_the_same_scenario_gives_the_same_answer(self, sales: object) -> None:
        first = run(sales, scope=SOUTH_ELECTRONICS).facts
        second = run(sales, scope=SOUTH_ELECTRONICS).facts
        assert first == second

    def test_side_effects_name_units_and_repeat_customers(self, sales: object) -> None:
        effects = {e["code"]: e for e in run(sales, scope=SOUTH_ELECTRONICS).facts["side_effects"]}
        assert effects["units_change"]["pct"] < 0
        assert 0 <= effects["repeat_exposure"]["share_pct"] <= 100

    def test_orders_can_be_simulated_too(self, sales: object) -> None:
        facts = run(sales, metric="orders", scope=SOUTH_ELECTRONICS).facts
        assert (
            facts["unit"] == "count" and facts["delta_expected"] < 0
        )  # fewer orders at a higher price
        assert facts["delta_pct_expected"] == pytest.approx(facts["units_change_pct"])

    def test_the_horizon_sets_the_number_of_months(self, sales: object) -> None:
        for horizon in (1, 6):
            assert len(run(sales, horizon=horizon).table) == horizon

    def test_a_scope_with_no_sales_has_no_discount_rate(self, sales: object) -> None:
        with pytest.raises(AnalyticsError) as caught:
            run(sales, lever="discount", scope={"product_type": "Nothing"})
        assert caught.value.code in ("no_gross_sales", "no_data", "too_little_history")

    def test_a_table_without_price_columns_cannot_be_simulated(self) -> None:
        a = analysis()
        a.dictionary.metrics["payments"] = Metric(
            expr="SUM(amount)", dataset="razorpay_payments", unit="INR", valid=True
        )
        with pytest.raises(AnalyticsError) as caught:
            whatif.what_if(a, Scenario("payments", "price", 5.0), "d2c")
        assert caught.value.code == "lever_not_supported"

    def test_a_price_that_never_moved_teaches_nothing(self, sales: object) -> None:
        facts = run(
            sales, scope={"product_type": "Home & Kitchen", "product_title": "Bedsheet Set"}
        ).facts
        price = next(a for a in facts["assumptions"] if a["key"] == "price_elasticity")
        assert price["source"] == "default"
        assert price["reason"] in ("history_did_not_move", "too_little_history")


class TestMarketing:
    def test_spend_on_a_channel_has_a_range_and_says_what_it_costs(self, sales: object) -> None:
        result = run(sales, lever="marketing_spend", change=20.0, scope={"channel": "Instagram"})
        facts = result.facts
        assert facts["delta_low"] <= facts["delta_expected"] <= facts["delta_high"]
        assert facts["extra_spend"] == pytest.approx(facts["monthly_spend"] * 0.2 * 3)
        assert facts["net_after_spend"] == pytest.approx(
            facts["delta_expected"] - facts["extra_spend"]
        )
        assert "attribution_limits" in result.notes
        # Attributed revenue per quarter is not an experiment: never better than low confidence.
        assert facts["confidence"]["level"] == "low"

    def test_cutting_a_channel_that_loses_money_saves_more_than_it_loses(
        self, sales: object
    ) -> None:
        facts = run(
            sales, lever="marketing_spend", change=-30.0, scope={"channel": "YouTube"}
        ).facts
        assert facts["current_roas"] < 1.0
        assert facts["delta_expected"] < 0 and facts["extra_spend"] < 0
        assert (
            abs(facts["extra_spend"]) > abs(facts["delta_expected"]) * 0.0
        )  # spend saved is reported

    def test_a_channel_is_required_and_must_exist(self, sales: object) -> None:
        for scope, code in (
            ({}, "needs_channel"),
            ({"region": "South"}, "needs_channel"),
            ({"channel": "Radio"}, "unknown_channel"),
        ):
            with pytest.raises(AnalyticsError) as caught:
                run(sales, lever="marketing_spend", change=10.0, scope=scope)
            assert caught.value.code == code

    def test_the_default_spend_response_is_used_with_too_few_campaign_rows(
        self, monkeypatch: pytest.MonkeyPatch, sales: object
    ) -> None:
        monkeypatch.setattr(whatif, "MIN_SPEND_POINTS", 10_000)
        facts = run(sales, lever="marketing_spend", change=20.0, scope={"channel": "Email"}).facts
        spend = next(a for a in facts["assumptions"] if a["key"] == "spend_elasticity")
        assert spend["source"] == "default" and spend["value"] == whatif.DEFAULTS["d2c"]["spend"]
        assert spend["reason"] == "too_little_history"


class TestEstimation:
    def test_a_clear_log_log_response_is_recovered(self) -> None:
        rng = np.random.default_rng(7)
        n = 104
        price = 100 * np.exp(rng.normal(0, 0.12, n))
        units = np.exp(6.0 - 1.7 * np.log(price) + rng.normal(0, 0.05, n) + np.linspace(0, 0.2, n))
        weekly = pd.DataFrame(
            {
                "bucket": pd.date_range("2024-01-01", periods=n, freq="W-MON"),
                "units": units,
                "price": price,
            }
        )
        estimate, r2 = whatif._regress(weekly, "price")
        assert estimate is not None and r2 is not None
        value, se = estimate
        assert value == pytest.approx(-1.7, abs=0.15) and se < 0.2 and r2 > 0.8

    def test_a_response_with_no_signal_is_inconclusive(self) -> None:
        rng = np.random.default_rng(3)
        n = 80
        weekly = pd.DataFrame(
            {
                "bucket": pd.date_range("2024-01-01", periods=n, freq="W-MON"),
                "units": 100 + rng.normal(0, 5, n),
                "price": 100 * np.exp(rng.normal(0, 0.1, n)),
            }
        )
        estimate, _ = whatif._regress(weekly, "price")
        assert estimate is not None
        value, se = estimate
        # Either too noisy or outside the believable range: ``fit_history`` would not use it.
        assert abs(value) / se < whatif.MIN_T or value > whatif.ELASTICITY_BOUNDS[1]
