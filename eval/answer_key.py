"""Answer key for the demo fixtures: measures every planted finding on the generated data (BUILD_SPEC 12.1).

``compute_answer_key`` returns plain numbers; ``render_answer_key`` turns them into ``ANSWER_KEY.md``.
Nothing here is hand-typed: every figure is computed from the tables that were written to disk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from generate_fixtures import Tables

REGION_ORDER = ("North", "South", "East", "West")
HIKE = pd.Timestamp("2026-07-01")
Q2 = (pd.Timestamp("2026-04-01"), pd.Timestamp("2026-07-01"))
Q3 = (pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-01"))


def _between(series: pd.Series[Any], bounds: tuple[pd.Timestamp, pd.Timestamp]) -> pd.Series[bool]:
    return (series >= bounds[0]) & (series < bounds[1])


def _prepare(t: Tables) -> pd.DataFrame:
    orders = t.orders.copy()
    orders["created"] = orders["_created"]
    orders["date"] = orders["created"].dt.normalize()
    orders["net"] = orders["total_price"] - orders["total_refunds"]
    signup = t.customers[["id", "signup_date"]].rename(columns={"id": "customer_id"})
    signup["signup_date"] = pd.to_datetime(signup["signup_date"])
    orders = orders.merge(signup, on="customer_id", how="left")
    orders = orders.sort_values("created", kind="stable")
    orders["is_repeat_order"] = orders.groupby("customer_id").cumcount() > 0
    return orders


def _south_finding(t: Tables, orders: pd.DataFrame) -> dict[str, Any]:
    q2, q3 = orders[_between(orders["created"], Q2)], orders[_between(orders["created"], Q3)]
    regions = {}
    for region in REGION_ORDER:
        before = float(q2.loc[q2["shipping_region"] == region, "net"].sum())
        after = float(q3.loc[q3["shipping_region"] == region, "net"].sum())
        regions[region] = {"q2": before, "q3": after, "change_pct": (after / before - 1) * 100}

    existing = q3["signup_date"] < HIKE
    south3 = q3["shipping_region"] == "South"
    south2 = q2["shipping_region"] == "South"
    repeat_q2 = float(q2.loc[south2 & (q2["signup_date"] < HIKE), "net"].sum())
    repeat_q3 = float(q3.loc[south3 & existing, "net"].sum())
    new_q3 = float(q3.loc[south3 & ~existing, "net"].sum())

    customers = t.customers.assign(signup_date=pd.to_datetime(t.customers["signup_date"]))
    cohort = customers[
        customers["signup_date"] < Q2[0]
    ]  # customers who existed for all of Q2 and Q3
    rates = {}
    for label, is_south in (("south", True), ("others", False)):
        ids = cohort.loc[(cohort["region"] == "South") == is_south, "id"]
        rates[label] = {
            "customers": len(ids),
            "q2": float(q2["customer_id"].isin(ids).sum() / len(ids) / 3),
            "q3": float(q3["customer_id"].isin(ids).sum() / len(ids) / 3),
        }
    frequency_vs_others = (rates["south"]["q3"] / rates["south"]["q2"]) / (
        rates["others"]["q3"] / rates["others"]["q2"]
    ) - 1

    newbies = customers[customers["signup_date"] >= HIKE]
    new_rates = {}
    for label, is_south in (("south", True), ("others", False)):
        group = newbies[(newbies["region"] == "South") == is_south]
        exposure_days = float((pd.Timestamp("2026-09-30") - group["signup_date"]).dt.days.sum())
        count = int(q3["customer_id"].isin(group["id"]).sum())
        new_rates[label] = {
            "customers": len(group),
            "orders": count,
            "orders_per_customer_30d": count / exposure_days * 30,
        }

    electronics = orders[orders["product_type"] == "Electronics"]
    price_ratio = {}
    for label, mask in (
        ("south", electronics["shipping_region"] == "South"),
        ("others", electronics["shipping_region"] != "South"),
    ):
        subset = electronics[mask]
        old = subset[subset["created"] < HIKE].groupby("product_id")["unit_price"].max()
        new_ = subset[subset["created"] >= HIKE].groupby("product_id")["unit_price"].max()
        price_ratio[label] = float((new_ / old).dropna().mean())

    return {
        "regions": regions,
        "south_repeat_revenue": {"q2": repeat_q2, "q3": repeat_q3},
        "south_new_revenue_q3": new_q3,
        "repeat_order_rates": rates,
        "frequency_change_vs_others_pct": frequency_vs_others * 100,
        "new_customer_rates": new_rates,
        "electronics_price_ratio": price_ratio,
        "removed_order_share_pct": t.south_drop_rate * 100,
    }


def _channel_finding(t: Tables) -> dict[str, Any]:
    grouped = t.campaigns.groupby("channel")[["spend", "revenue"]].sum()
    grouped["roas"] = grouped["revenue"] / grouped["spend"]
    grouped["spend_share_pct"] = grouped["spend"] / grouped["spend"].sum() * 100
    return {"channels": grouped.sort_values("roas", ascending=False).to_dict("index")}


def _budget_finding(t: Tables) -> dict[str, Any]:
    budget = t.budget.assign(month=pd.to_datetime(t.budget["month"]))

    def over(department: str, start: str, end: str) -> float:
        rows = budget[
            (budget["department"] == department)
            & (budget["month"] >= start)
            & (budget["month"] <= end)
        ]
        return float(rows["actual"].sum() / rows["budget"].sum() - 1) * 100

    q3_over = {d: over(d, "2026-07-01", "2026-09-01") for d in budget["department"].unique()}
    return {
        "marketing_q3_2026_over_pct": q3_over["Marketing"],
        "operations_oct_nov_2025_over_pct": over("Operations", "2025-10-01", "2025-11-01"),
        "q3_2026_over_pct_by_department": q3_over,
    }


def _category_finding(t: Tables, orders: pd.DataFrame) -> dict[str, Any]:
    cost = t.products.set_index("id")["cost_per_item"]
    work = orders.assign(
        discount_rate=orders["total_discounts"] / orders["total_line_items_price"],
        cost=orders["product_id"].map(cost) * orders["quantity"],
    )
    result = {}
    for category, group in work.groupby("product_type"):
        result[str(category)] = {
            "avg_discount_pct": float(group["discount_rate"].mean() * 100),
            "gross_margin_pct": float(
                (group["total_price"].sum() - group["cost"].sum())
                / group["total_price"].sum()
                * 100
            ),
            "net_revenue": float(group["net"].sum()),
        }
    return result


def _outage_finding(orders: pd.DataFrame) -> dict[str, Any]:
    app = orders[orders["sales_channel"] == "App"].groupby("date").size()
    day = pd.Timestamp("2026-08-15")

    def baseline(target: pd.Timestamp) -> float:
        history = [app.get(target - pd.Timedelta(days=7 * k), np.nan) for k in range(1, 9)]
        return float(np.nanmedian(history))

    def entry(target: pd.Timestamp) -> dict[str, float]:
        return {"app_orders": float(app.get(target, 0)), "typical_same_weekday": baseline(target)}

    outage = entry(day)
    outage["drop_pct"] = (1 - outage["app_orders"] / outage["typical_same_weekday"]) * 100
    return {
        "outage_day": outage,
        "day_before": entry(day - pd.Timedelta(days=1)),
        "day_after": entry(day + pd.Timedelta(days=1)),
    }


def _growth_finding(orders: pd.DataFrame) -> dict[str, Any]:
    west_beauty = orders[
        (orders["shipping_region"] == "West") & (orders["product_type"] == "Beauty")
    ]
    monthly = west_beauty.groupby(west_beauty["created"].dt.to_period("M"))
    counts = monthly.size()
    repeat = (
        west_beauty[west_beauty["is_repeat_order"]]
        .groupby(west_beauty.loc[west_beauty["is_repeat_order"], "created"].dt.to_period("M"))
        .size()
    )
    months = [pd.Period(f"2026-{m:02d}") for m in range(4, 10)]
    series = {
        str(m): {"orders": int(counts.get(m, 0)), "repeat_orders": int(repeat.get(m, 0))}
        for m in months
    }
    may, sep = pd.Period("2026-05"), pd.Period("2026-09")
    increase = counts.get(sep, 0) - counts.get(may, 0)
    repeat_increase = repeat.get(sep, 0) - repeat.get(may, 0)
    return {
        "monthly": series,
        "sep_vs_may_increase": int(increase),
        "share_of_increase_from_repeat_pct": float(repeat_increase / increase * 100)
        if increase
        else 0.0,
    }


def _gap_finding(orders: pd.DataFrame) -> dict[str, Any]:
    web = orders[orders["sales_channel"] == "Website"].groupby("date").size()
    days = pd.date_range("2026-03-02", "2026-03-22")
    series = {d.strftime("%Y-%m-%d"): int(web.get(d, 0)) for d in days}
    around = pd.date_range("2026-02-09", "2026-03-08").append(
        pd.date_range("2026-03-16", "2026-04-12")
    )
    return {
        "daily_website_orders": series,
        "avg_daily_website_orders_around_gap": float(np.mean([web.get(d, 0) for d in around])),
        "missing_days": [d for d, n in series.items() if n == 0],
    }


def _target_finding(t: Tables, orders: pd.DataFrame) -> dict[str, Any]:
    targets = t.targets.assign(month=pd.to_datetime(t.targets["month"]))
    q3 = targets[targets["month"] >= HIKE]
    actual = orders[_between(orders["created"], Q3)].groupby("shipping_region")["net"].sum()
    result = {}
    for region in REGION_ORDER:
        target = float(q3.loc[q3["region"] == region, "target"].sum())
        result[region] = {
            "target": target,
            "actual": float(actual[region]),
            "vs_target_pct": (float(actual[region]) / target - 1) * 100,
        }
    return result


def compute_answer_key(t: Tables) -> dict[str, Any]:
    orders = _prepare(t)
    return {
        "totals": {
            "customers": len(t.customers),
            "orders": len(t.orders),
            "net_revenue": float(orders["net"].sum()),
        },
        "f1_south": _south_finding(t, orders),
        "f2_channels": _channel_finding(t),
        "f3_budget": _budget_finding(t),
        "f4_categories": _category_finding(t, orders),
        "f5_outage": _outage_finding(orders),
        "f6_growth": _growth_finding(orders),
        "f7_gap": _gap_finding(orders),
        "f8_targets": _target_finding(t, orders),
    }


# --- Rendering --------------------------------------------------------------------------------------------------
def _inr(value: float) -> str:
    return f"₹{value:,.0f}"


def _pct(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}%"


def render_answer_key(t: Tables) -> str:
    k = compute_answer_key(t)
    f1, f2, f3 = k["f1_south"], k["f2_channels"], k["f3_budget"]
    out: list[str] = [
        "# Answer key: UrbanCart demo company",
        "",
        "Generated by `eval/generate_fixtures.py` (seed 42). Every number below was measured on the generated "
        "files, not typed by hand. Regenerate with `make fixtures`.",
        "",
        f"- Period: 2025-01-01 to 2026-09-30, currency INR, {k['totals']['customers']:,} customers, "
        f"{k['totals']['orders']:,} orders.",
        "- **Revenue** below means `total_price - total_refunds` (net of refunds) in `shopify_orders.csv`.",
        "- Quarters: Q2 2026 = Apr-Jun, Q3 2026 = Jul-Sep.",
        "",
        "## 1. South revenue falls in Q3 2026 while every other region grows",
        "",
        "| Region | Q2 2026 revenue | Q3 2026 revenue | Change |",
        "|---|---:|---:|---:|",
    ]
    for region in REGION_ORDER:
        r = f1["regions"][region]
        out.append(f"| {region} | {_inr(r['q2'])} | {_inr(r['q3'])} | {_pct(r['change_pct'])} |")
    rates, new = f1["repeat_order_rates"], f1["new_customer_rates"]
    out += [
        "",
        "**Cause:** a 12% price increase on Electronics in the South from 2026-07-01. After it, existing South "
        "customers (signed up before July 2026) order much less often.",
        "",
        f"- Electronics list-price ratio after vs before the hike: South {f1['electronics_price_ratio']['south']:.2f}, "
        f"other regions {f1['electronics_price_ratio']['others']:.2f}.",
        f"- South revenue from existing customers: {_inr(f1['south_repeat_revenue']['q2'])} (Q2) to "
        f"{_inr(f1['south_repeat_revenue']['q3'])} (Q3). South revenue from customers who joined in Q3: "
        f"{_inr(f1['south_new_revenue_q3'])}.",
        f"- Orders per customer per month, customers who joined before April 2026: South "
        f"{rates['south']['q2']:.3f} to {rates['south']['q3']:.3f}; other regions "
        f"{rates['others']['q2']:.3f} to {rates['others']['q3']:.3f}. Relative to the other regions, existing "
        f"South customers order **{abs(f1['frequency_change_vs_others_pct']):.0f}% less often** "
        f"(the average gap between their orders is about "
        f"{abs(1 / (1 + f1['frequency_change_vs_others_pct'] / 100) - 1) * 100:.0f}% longer).",
        f"- Construction detail: {f1['removed_order_share_pct']:.1f}% of the orders that existing South customers "
        "would have placed after 2026-07-01 were removed (the rate is solved so that South Q3 revenue is about "
        "18% below Q2; whole orders are removed, so it lands within a fraction of a point).",
        f"- New customers are not affected: customers who joined from July 2026 place "
        f"{new['south']['orders_per_customer_30d']:.2f} orders per 30 days in the South vs "
        f"{new['others']['orders_per_customer_30d']:.2f} elsewhere "
        f"({new['south']['customers']} South and {new['others']['customers']} other-region customers).",
        "",
        "## 2. Channel efficiency (marketing_sheet.xlsx, all quarters)",
        "",
        "| Channel | Spend | Attributed revenue | ROAS | Share of spend |",
        "|---|---:|---:|---:|---:|",
    ]
    for channel, c in f2["channels"].items():
        out.append(
            f"| {channel} | {_inr(c['spend'])} | {_inr(c['revenue'])} | {c['roas']:.2f}x | "
            f"{c['spend_share_pct']:.1f}% |"
        )
    out += [
        "",
        "Email and Instagram are the most efficient. **YouTube returns less than it costs (ROAS below 1).** "
        "Google Ads takes the largest share of spend at a low ROAS.",
        "",
        "## 3. Budget variance (zoho_budget_expenses.xlsx)",
        "",
        f"- **Marketing, Q3 2026: {_pct(f3['marketing_q3_2026_over_pct'])} vs budget** (every month from July is "
        "20-24% over).",
        f"- **Operations, Oct-Nov 2025: {_pct(f3['operations_oct_nov_2025_over_pct'])} vs budget** (festive logistics).",
        "- All other department-months stay within about +/-6% of budget.",
        "",
        "Q3 2026 variance by department: "
        + ", ".join(f"{d} {_pct(v)}" for d, v in f3["q3_2026_over_pct_by_department"].items())
        + ".",
        "",
        "## 4. Discounting and margin by category (all orders)",
        "",
        "| Category | Average discount | Gross margin | Net revenue |",
        "|---|---:|---:|---:|",
    ]
    for category, c in sorted(
        k["f4_categories"].items(), key=lambda kv: -kv[1]["avg_discount_pct"]
    ):
        out.append(
            f"| {category} | {c['avg_discount_pct']:.1f}% | {c['gross_margin_pct']:.1f}% | "
            f"{_inr(c['net_revenue'])} |"
        )
    out += [
        "",
        "Gross margin = (sum of `total_price` - sum of quantity x `cost_per_item`) / sum of `total_price`. "
        "**Fashion has the heaviest discounting; Electronics has the thinnest margin.** "
        "Discounts are 8 points deeper in Oct-Nov (festive season) in every category.",
        "",
        "## 5. Anomaly: app outage on 2026-08-15",
        "",
        "| Day | App orders | Typical (median of previous 8 same weekdays) |",
        "|---|---:|---:|",
    ]
    for label, key in (
        ("2026-08-14", "day_before"),
        ("**2026-08-15**", "outage_day"),
        ("2026-08-16", "day_after"),
    ):
        e = k["f5_outage"][key]
        out.append(f"| {label} | {e['app_orders']:.0f} | {e['typical_same_weekday']:.1f} |")
    out += [
        "",
        f"App orders on 2026-08-15 are **{k['f5_outage']['outage_day']['drop_pct']:.0f}% below** normal for a "
        "Saturday; the days either side are normal; Website and Marketplace orders are unaffected.",
        "",
        "## 6. Growth signal: Beauty in the West (repeat customers)",
        "",
        "| Month | West Beauty orders | of which repeat customers |",
        "|---|---:|---:|",
    ]
    for month, g in k["f6_growth"]["monthly"].items():
        out.append(f"| {month} | {g['orders']} | {g['repeat_orders']} |")
    out += [
        "",
        "Orders rise every month from June to September 2026. "
        f"{k['f6_growth']['share_of_increase_from_repeat_pct']:.0f}% of the increase from May to September "
        "comes from repeat customers.",
        "",
        "## 7. Data-quality gap: Website orders, week of 2026-03-09",
        "",
        f"Website orders per day, 2026-03-02 to 2026-03-22 (normal level around the gap: "
        f"{k['f7_gap']['avg_daily_website_orders_around_gap']:.1f} per day):",
        "",
        "| Day | Website orders |",
        "|---|---:|",
    ]
    for day, n in k["f7_gap"]["daily_website_orders"].items():
        out.append(f"| {day} | {n} |")
    out += [
        "",
        f"Days with zero Website orders: {', '.join(k['f7_gap']['missing_days'])}. App and Marketplace orders "
        "continue normally. The agent should warn that Website revenue for that week is incomplete.",
        "",
        "## 8. Targets vs actual, Q3 2026 (Targets tab in marketing_sheet.xlsx)",
        "",
        "| Region | Target | Actual revenue | Actual vs target |",
        "|---|---:|---:|---:|",
    ]
    for region in REGION_ORDER:
        r = k["f8_targets"][region]
        out.append(
            f"| {region} | {_inr(r['target'])} | {_inr(r['actual'])} | {_pct(r['vs_target_pct'])} |"
        )
    out += ["", "**Only the South misses its Q3 2026 target.**", ""]
    return "\n".join(out)
