#!/usr/bin/env python3
"""Deterministic demo-company fixtures: "UrbanCart", an Indian D2C e-commerce brand (BUILD_SPEC section 12.1).

The data contains eight *planted findings* so analytics and the AI agent can be checked against an answer key:

 1. South revenue falls ~18% in Q3 2026 vs Q2 2026 (Electronics price hike on 2026-07-01; existing South
    customers order much less often afterwards) while every other region grows.
 2. Channel efficiency: Email and Instagram have the best ROAS, YouTube loses money, Google Ads is the largest spend.
 3. Marketing runs ~22% over budget in Q3 2026; Operations runs 8-12% over in Oct-Nov 2025.
 4. Fashion has the heaviest discounting; Electronics has the thinnest gross margin.
 5. One anomaly day: 2026-08-15, App orders fall ~60% (app outage).
 6. Growth signal: Beauty orders in the West grow four months in a row (Jun-Sep 2026) driven by repeat customers.
 7. Data-quality gap: Website orders for the week of 2026-03-09 are missing.
 8. Cross-source check: monthly regional targets (in the Sheets-like file) are missed in Q3 2026 by the South only.

Usage:  python eval/generate_fixtures.py [--out eval/fixtures] [--seed 42]

Everything is a pure function of the seed (numpy ``default_rng(42)``): CSV files are byte-identical between runs.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

Arr = npt.NDArray[Any]

SEED = 42
START = dt.date(2025, 1, 1)
END = dt.date(2026, 9, 30)
FIRST_SIGNUP_MONTH = dt.date(2024, 6, 1)
N_CUSTOMERS = 3000
IST = "+05:30"
OUT_DIR_DEFAULT = Path(__file__).resolve().parent / "fixtures"

# --- Planted-finding parameters --------------------------------------------------------------------------------
HIKE_DATE = dt.date(2026, 7, 1)
HIKE_PCT = 0.12  # Electronics list price, South only
# After the hike, a share of the orders of existing South customers (signed up before HIKE_DATE) no longer
# happens. The share is *solved for* (bisection, see ``apply_south_decline``) so that South revenue in Q3 2026 is
# 18% below Q2 2026 (to within one order); the solved rate is reported in ANSWER_KEY.md. See docs/DECISIONS.md.
SOUTH_Q3_CHANGE = -0.18
APP_OUTAGE = dt.date(2026, 8, 15)
APP_OUTAGE_DROP = 0.60
GAP_START = dt.date(2026, 3, 9)
GAP_END = dt.date(2026, 3, 15)
# Extra West-region Beauty orders from repeat customers, as a multiple of the normal expectation.
WEST_BEAUTY_BOOST = {(2026, 6): 0.35, (2026, 7): 0.80, (2026, 8): 1.15, (2026, 9): 1.55}
# Aggregate ROAS per channel over the whole period (answer key values for finding 2).
CHANNEL_ROAS_TARGET = {
    "Email": 6.4,
    "Instagram": 4.1,
    "Facebook": 2.1,
    "Influencer": 2.6,
    "Google Ads": 1.6,
    "YouTube": 0.8,
}
MARKETING_Q3_2026_OVERSPEND = {(2026, 7): 1.20, (2026, 8): 1.22, (2026, 9): 1.24}
OPERATIONS_FESTIVE_OVERSPEND = {(2025, 10): 1.09, (2025, 11): 1.11}

# --- Business model ---------------------------------------------------------------------------------------------
BASE_ORDERS_PER_MONTH = 1100
MONTHLY_GROWTH = 0.02
SEASON = {
    1: 0.85,
    2: 0.90,
    3: 0.95,
    4: 0.95,
    5: 1.0,
    6: 0.95,
    7: 1.0,
    8: 1.05,
    9: 1.05,
    10: 1.35,
    11: 1.40,
    12: 1.15,
}
DOW_FACTOR = (0.96, 0.95, 0.97, 1.00, 1.05, 1.10, 0.97)  # Monday..Sunday, mean 1.0
DAILY_NOISE = 0.06  # relative sd of the daily order count (sub-Poisson on purpose: keeps planted anomalies clear)
FESTIVE_EXTRA_DISCOUNT = 0.08  # Oct-Nov, all categories

CHANNELS = ("App", "Website", "Marketplace")
CHANNEL_P = (0.50, 0.30, 0.20)
ACQUISITION = ("Organic", "Google Ads", "Instagram", "Referral", "Email", "YouTube")
ACQUISITION_SHARE = (0.30, 0.25, 0.20, 0.12, 0.08, 0.05)
SEGMENTS = ("Consumer", "Small Business")
SEGMENT_SHARE = (0.85, 0.15)
SMALL_BUSINESS_FREQUENCY = 1.6  # buys 1.6x more often, and double quantities
REGION_SHARE = {"North": 0.27, "South": 0.30, "East": 0.18, "West": 0.25}
REGIONS: dict[str, list[tuple[str, str]]] = {
    "North": [
        ("Delhi", "Delhi"),
        ("Chandigarh", "Chandigarh"),
        ("Lucknow", "Uttar Pradesh"),
        ("Jaipur", "Rajasthan"),
    ],
    "South": [
        ("Bengaluru", "Karnataka"),
        ("Chennai", "Tamil Nadu"),
        ("Hyderabad", "Telangana"),
        ("Kochi", "Kerala"),
    ],
    "East": [
        ("Kolkata", "West Bengal"),
        ("Bhubaneswar", "Odisha"),
        ("Patna", "Bihar"),
        ("Guwahati", "Assam"),
    ],
    "West": [
        ("Mumbai", "Maharashtra"),
        ("Pune", "Maharashtra"),
        ("Ahmedabad", "Gujarat"),
        ("Goa", "Goa"),
    ],
}
HOUR_WEIGHT = np.array(
    [
        1,
        0.5,
        0.3,
        0.2,
        0.2,
        0.4,
        0.8,
        1.5,
        2.5,
        3.5,
        4,
        4.5,
        5,
        4.5,
        4,
        4,
        4,
        4.5,
        5.5,
        6.5,
        7,
        6,
        4,
        2,
    ]
)
HOUR_P = HOUR_WEIGHT / HOUR_WEIGHT.sum()
PRODUCT_P = np.array(
    [0.22, 0.18, 0.15, 0.13, 0.11, 0.09, 0.07, 0.05]
)  # popularity of the 8 products in a category


@dataclass(frozen=True)
class Category:
    name: str
    code: str
    cost_pct: float
    discount: float
    price_min: int
    price_max: int
    order_mix: float
    refund_rate: float


CATEGORIES = (
    Category("Electronics", "ELE", 0.74, 0.05, 1500, 15000, 0.22, 0.03),
    Category("Fashion", "FAS", 0.42, 0.22, 499, 4999, 0.28, 0.07),
    Category("Home & Kitchen", "HOM", 0.55, 0.08, 399, 8999, 0.20, 0.03),
    Category("Beauty", "BEA", 0.38, 0.10, 199, 2499, 0.18, 0.03),
    Category("Sports", "SPO", 0.57, 0.07, 599, 12999, 0.12, 0.03),
)
ELECTRONICS, BEAUTY = 0, 3
CATEGORY_P = np.array([c.order_mix for c in CATEGORIES])

PRODUCT_NAMES = (
    (
        "Wireless Earbuds",
        "Smartwatch Fit",
        "Bluetooth Speaker",
        "Power Bank 20000mAh",
        "Noise-Cancelling Headphones",
        "Tablet 10-inch",
        "Action Camera",
        "Mechanical Keyboard",
    ),
    (
        "Cotton Kurta",
        "Slim-Fit Jeans",
        "Linen Shirt",
        "Running Shoes",
        "Silk Saree",
        "Winter Jacket",
        "Leather Wallet",
        "Canvas Backpack",
    ),
    (
        "Storage Containers",
        "Vacuum Flask",
        "LED Table Lamp",
        "Bedsheet Set",
        "Mixer Grinder",
        "Non-stick Cookware Set",
        "Dinner Set",
        "Air Fryer",
    ),
    (
        "Face Wash",
        "Hair Oil",
        "Matte Lipstick",
        "Sunscreen SPF 50",
        "Moisturiser",
        "Vitamin C Serum",
        "Hair Dryer",
        "Perfume 50ml",
    ),
    (
        "Resistance Bands",
        "Yoga Mat",
        "Football",
        "Badminton Racket",
        "Dumbbell Set",
        "Cricket Bat",
        "Cycling Helmet",
        "Fitness Tracker Band",
    ),
)

FIRST_NAMES = (
    "Aarav",
    "Vivaan",
    "Aditya",
    "Arjun",
    "Rohan",
    "Ishaan",
    "Kabir",
    "Rahul",
    "Karan",
    "Siddharth",
    "Ananya",
    "Diya",
    "Isha",
    "Kavya",
    "Meera",
    "Neha",
    "Priya",
    "Riya",
    "Sneha",
    "Tanvi",
)
LAST_NAMES = (
    "Sharma",
    "Verma",
    "Iyer",
    "Nair",
    "Reddy",
    "Rao",
    "Patel",
    "Shah",
    "Mehta",
    "Das",
    "Banerjee",
    "Chatterjee",
    "Singh",
    "Kumar",
    "Gupta",
    "Joshi",
    "Pillai",
    "Menon",
    "Kulkarni",
    "Bose",
)

# (channel, spend per month INR, CTR, CVR, AOV INR, target ROAS)
MARKETING_CHANNELS = (
    ("Google Ads", 230_000, 0.035, 0.030, 2400, 1.7),
    ("Instagram", 130_000, 0.022, 0.040, 1900, 4.2),
    ("Facebook", 90_000, 0.015, 0.025, 1800, 2.1),
    ("Email", 20_000, 0.060, 0.050, 2100, 6.5),
    ("YouTube", 110_000, 0.008, 0.012, 2600, 0.8),
    ("Influencer", 75_000, 0.030, 0.020, 2200, 2.6),
)
DEPARTMENT_BUDGET = (
    ("Marketing", 700_000),
    ("Operations", 900_000),
    ("Technology", 550_000),
    ("Sales", 450_000),
    ("HR", 250_000),
)
BUDGET_GROWTH = 0.015


# --- Small helpers ----------------------------------------------------------------------------------------------
def month_starts(first: dt.date, last: dt.date) -> list[dt.date]:
    months: list[dt.date] = []
    current = dt.date(first.year, first.month, 1)
    while current <= last:
        months.append(current)
        current = dt.date(current.year + (current.month // 12), current.month % 12 + 1, 1)
    return months


def quarter_label(d: dt.date) -> str:
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def split_counts(total: int, shares: Any) -> Arr:
    """Largest-remainder allocation: integer counts summing exactly to ``total``."""
    raw = np.asarray(shares, dtype=float)
    raw = raw / raw.sum() * total
    counts = np.floor(raw).astype(int)
    order = np.argsort(-(raw - counts), kind="stable")
    counts[order[: total - counts.sum()]] += 1
    return counts


def exact_allocation(rng: np.random.Generator, total: int, labels: Any, shares: Any) -> Arr:
    values = np.repeat(np.array(list(labels), dtype=object), split_counts(total, shares))
    rng.shuffle(values)
    return values


def monthly_order_target(day: dt.date) -> float:
    months_in = (day.year - START.year) * 12 + day.month - START.month
    return BASE_ORDERS_PER_MONTH * (1 + MONTHLY_GROWTH) ** months_in * SEASON[day.month]


def price_point(x: float) -> int:
    step = 100 if x >= 1000 else 10
    return int(round(x / step) * step - 1)


def ist_stamp(ts: pd.Series[Any]) -> pd.Series[Any]:
    return ts.dt.strftime("%Y-%m-%dT%H:%M:%S") + IST


# --- Customers and products -------------------------------------------------------------------------------------
def make_customers(rng: np.random.Generator) -> pd.DataFrame:
    n = N_CUSTOMERS
    region = exact_allocation(rng, n, REGION_SHARE.keys(), list(REGION_SHARE.values()))
    city_pick = rng.integers(0, 4, n)
    city = np.array(
        [REGIONS[r][i][0] for r, i in zip(region, city_pick, strict=True)], dtype=object
    )
    state = np.array(
        [REGIONS[r][i][1] for r, i in zip(region, city_pick, strict=True)], dtype=object
    )
    segment = exact_allocation(rng, n, SEGMENTS, SEGMENT_SHARE)
    channel = exact_allocation(rng, n, ACQUISITION, ACQUISITION_SHARE)

    # 1,500 customers signed up Jun-Dec 2024 (before the order data starts); the other 1,500 arrive Jan 2025
    # to Sep 2026 at a rate growing 2% a month. This keeps orders per active customer roughly constant.
    early = month_starts(FIRST_SIGNUP_MONTH, dt.date(2024, 12, 1))
    late = month_starts(START, END)
    weights = [1.0] * len(early) + [(1 + MONTHLY_GROWTH) ** i for i in range(len(late))]
    counts = np.concatenate(
        [split_counts(1500, [1.0] * len(early)), split_counts(1500, weights[len(early) :])]
    )
    signup: list[dt.date] = []
    for month, count in zip(early + late, counts, strict=True):
        days_in_month = calendar.monthrange(month.year, month.month)[1]
        picked = np.sort(rng.integers(1, days_in_month + 1, int(count)))
        signup.extend(dt.date(month.year, month.month, int(d)) for d in picked)

    propensity = rng.gamma(2.0, 1.0, n)
    is_small_business = segment == "Small Business"
    weight = np.maximum(propensity, 0.05) * np.where(
        is_small_business, SMALL_BUSINESS_FREQUENCY, 1.0
    )
    first = np.array(FIRST_NAMES, dtype=object)[rng.integers(0, len(FIRST_NAMES), n)]
    last = np.array(LAST_NAMES, dtype=object)[rng.integers(0, len(LAST_NAMES), n)]
    signup_seconds = rng.integers(0, 86_400, n)
    accepts_marketing = rng.random(n) < 0.62

    customer_id = 7_000_000_001 + np.arange(n)
    created = pd.to_datetime(pd.Series(signup)) + pd.to_timedelta(signup_seconds, unit="s")
    return pd.DataFrame(
        {
            "id": customer_id,
            "first_name": first,
            "last_name": last,
            "email": [
                f"{f}.{la}.{i}@example.com".lower()
                for f, la, i in zip(first, last, customer_id, strict=True)
            ],
            "created_at": ist_stamp(created),
            "signup_date": pd.Series(signup),
            "city": city,
            "state": state,
            "region": region,
            "segment": segment,
            "acquisition_channel": channel,
            "accepts_marketing": accepts_marketing,
            "weight": weight,
        }
    )


def make_products(rng: np.random.Generator) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for category, names in zip(CATEGORIES, PRODUCT_NAMES, strict=True):
        raw = np.sort(
            np.exp(rng.uniform(np.log(category.price_min), np.log(category.price_max), 8))
        )
        prices = [
            min(max(price_point(float(x)), category.price_min), category.price_max) for x in raw
        ]
        for slot, (name, price) in enumerate(zip(names, prices, strict=True)):
            rows.append(
                {
                    "id": 8_000_000_001 + len(rows),
                    "title": name,
                    "product_type": category.name,
                    "sku": f"{category.code}-{slot + 1:03d}",
                    "vendor": "UrbanCart",
                    "price": price,
                    "cost_per_item": round(price * category.cost_pct, 2),
                    "status": "active",
                    "created_at": "2024-05-15T10:00:00" + IST,
                    "inventory_quantity": int(rng.integers(20, 600)),
                }
            )
    return pd.DataFrame(rows)


def apply_south_decline(
    orders: pd.DataFrame,
) -> tuple[pd.DataFrame, float, dict[tuple[int, int], float]]:
    """Finding 1: remove a share of existing South customers' orders after the price hike.

    The share is found by bisection so that South net revenue in Q3 2026 is ``SOUTH_Q3_CHANGE`` (to within one order) versus
    Q2 2026. Returns the remaining orders, the solved rate and the net revenue removed per (year, month), which
    the targets use as the "pre-hike run rate" counterfactual.
    """
    created = orders["_created"]
    net = orders["total_price"] - orders["total_refunds"]
    south = orders["shipping_region"] == "South"
    candidate = orders["_south_candidate"]
    roll = orders["_roll_south"]
    q2_revenue = float(net[south & (created >= "2026-04-01") & (created < "2026-07-01")].sum())
    in_q3 = south & (created >= "2026-07-01") & (created < "2026-10-01")
    wanted = q2_revenue * (1 + SOUTH_Q3_CHANGE)

    low, high = 0.0, 1.0
    for _ in range(60):
        rate = (low + high) / 2
        q3_revenue = float(net[in_q3 & ~(candidate & (roll < rate))].sum())
        low, high = (rate, high) if q3_revenue > wanted else (low, rate)
    rate = (low + high) / 2

    lost = candidate & (roll < rate)
    by_month = net[lost].groupby(created[lost].dt.to_period("M")).sum()
    dropped = {(int(p.year), int(p.month)): float(v) for p, v in by_month.items()}  # type: ignore[attr-defined]
    remaining = orders[~lost].drop(columns=["_south_candidate", "_roll_south"])
    return remaining.reset_index(drop=True), rate, dropped


# --- Order simulation -------------------------------------------------------------------------------------------
class OrderSimulator:
    """Generates orders day by day and applies the planted effects."""

    def __init__(
        self, rng: np.random.Generator, customers: pd.DataFrame, products: pd.DataFrame
    ) -> None:
        self.rng = rng
        self.weight = customers["weight"].to_numpy(dtype=float)
        self.cum_weight = np.cumsum(self.weight)
        self.signup = np.array([d.toordinal() for d in customers["signup_date"]])
        region = customers["region"].to_numpy(dtype=object)
        self.is_south: Arr = region == "South"
        self.is_west: Arr = region == "West"
        self.is_small_business: Arr = (
            customers["segment"].to_numpy(dtype=object) == "Small Business"
        )
        self.existing_before_hike: Arr = self.signup < HIKE_DATE.toordinal()
        self.ordered: Arr = np.zeros(len(customers), dtype=bool)
        self.product_price = products["price"].to_numpy(dtype=float)
        self.category_discount = np.array([c.discount for c in CATEGORIES])
        self.category_refund = np.array([c.refund_rate for c in CATEGORIES])
        self.parts: list[dict[str, Arr]] = []
        self.dropped_south_net: dict[tuple[int, int], float] = {}
        self.south_drop_rate = 0.0
        self.extra_carry = (
            0.0  # fractional extra orders carried to the next day (keeps the boost deterministic)
        )

    def run(self) -> None:
        for offset in range((END - START).days + 1):
            self._simulate_day(START + dt.timedelta(days=offset))

    def _simulate_day(self, day: dt.date) -> None:
        rng = self.rng
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        expected = monthly_order_target(day) / days_in_month * DOW_FACTOR[day.weekday()]
        n = max(0, round(expected * (1 + rng.normal(0, DAILY_NOISE))))
        active = int(
            np.searchsorted(self.signup, day.toordinal(), side="left")
        )  # signed up before today
        if active == 0:
            return
        customers = self._pick_customers(n, np.arange(active))
        self._draw(day, customers)

        boost = WEST_BEAUTY_BOOST.get((day.year, day.month))
        if boost:
            west_share = (
                self.weight[:active][self.is_west[:active]].sum() / self.cum_weight[active - 1]
            )
            wanted = n * CATEGORY_P[BEAUTY] * west_share * boost + self.extra_carry
            extra = int(wanted)
            self.extra_carry = wanted - extra
            repeat_west = np.flatnonzero(self.is_west[:active] & self.ordered[:active])
            if extra and repeat_west.size:
                self._draw(day, self._pick_customers(extra, repeat_west), forced_category=BEAUTY)

    def _pick_customers(self, n: int, candidates: Arr) -> Arr:
        cumulative = np.cumsum(self.weight[candidates])
        picks = np.searchsorted(cumulative, self.rng.random(n) * cumulative[-1], side="right")
        return candidates[np.minimum(picks, candidates.size - 1)]

    def _draw(self, day: dt.date, cust: Arr, forced_category: int | None = None) -> None:
        rng = self.rng
        m = int(cust.size)
        if m == 0:
            return
        if forced_category is None:
            # Stratified: each day's channel counts follow 50/30/20 almost exactly, so a real outage stands out.
            channel = exact_allocation(rng, m, range(3), CHANNEL_P).astype(int)
        else:
            channel = rng.choice(3, m, p=CHANNEL_P)
        if forced_category is None:
            category = exact_allocation(rng, m, range(len(CATEGORIES)), CATEGORY_P).astype(int)
        else:
            category = np.full(m, forced_category)
        product = category * 8 + rng.choice(8, m, p=PRODUCT_P)
        u_qty = rng.random(m)
        base_qty = np.where(
            category == ELECTRONICS,
            1 + (u_qty > 0.90).astype(int) + (u_qty > 0.98).astype(int),
            1 + (u_qty > 0.72).astype(int) + (u_qty > 0.92).astype(int),
        )
        quantity = base_qty * np.where(self.is_small_business[cust], 2, 1)

        price = self.product_price[product]
        hiked = (day >= HIKE_DATE) & self.is_south[cust] & (category == ELECTRONICS)
        price = np.where(hiked, np.round(price * (1 + HIKE_PCT)), price)

        festive = FESTIVE_EXTRA_DISCOUNT if day.month in (10, 11) else 0.0
        discount_rate = np.clip(
            self.category_discount[category] + festive + rng.normal(0, 0.03, m), 0, 0.6
        )
        line_total = quantity * price
        discount = np.round(line_total * discount_rate, 2)
        total = np.round(line_total - discount, 2)

        refunded = rng.random(m) < self.category_refund[category]
        refund = np.where(refunded, total, 0.0)
        hour = rng.choice(24, m, p=HOUR_P)
        seconds = hour * 3600 + rng.integers(0, 3600, m)
        u_gateway = rng.random(m)
        gateway = np.where(
            channel == 2, "marketplace", np.where(u_gateway < 0.8, "razorpay", "cod")
        )
        u_method = rng.random(m)
        method = np.where(
            u_method < 0.55,
            "upi",
            np.where(u_method < 0.85, "card", np.where(u_method < 0.95, "netbanking", "wallet")),
        )
        roll_outage = rng.random(m)
        roll_south = rng.random(
            m
        )  # used later by apply_south_decline (the stream must not depend on it)
        updated_delay = rng.integers(0, 72 * 3600, m) + np.where(
            refunded, rng.integers(1, 10, m) * 86_400, 0
        )

        keep = np.ones(m, dtype=bool)
        if day == APP_OUTAGE:  # finding 5: exactly 60% of that day's App orders never happen
            app_orders = np.flatnonzero(channel == 0)
            lost = app_orders[np.argsort(roll_outage[app_orders], kind="stable")][
                : round(APP_OUTAGE_DROP * app_orders.size)
            ]
            keep[lost] = False
        if GAP_START <= day <= GAP_END:  # finding 7
            keep &= channel != 1
        # Finding 1 is applied afterwards (apply_south_decline) to orders flagged here.
        south_candidate = (
            np.full(m, day >= HIKE_DATE) & self.is_south[cust] & self.existing_before_hike[cust]
        )

        self.ordered[cust[keep]] = True
        self.parts.append(
            {
                "day": np.full(int(keep.sum()), day.toordinal()),
                "seconds": seconds[keep],
                "updated_delay": updated_delay[keep],
                "customer": cust[keep],
                "channel": channel[keep],
                "category": category[keep],
                "product": product[keep],
                "quantity": quantity[keep],
                "unit_price": price[keep],
                "line_total": line_total[keep],
                "discount": discount[keep],
                "total": total[keep],
                "refund": refund[keep],
                "gateway": gateway[keep],
                "method": method[keep],
                "south_candidate": south_candidate[keep],
                "roll_south": roll_south[keep],
            }
        )

    def to_frame(self, customers: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
        cols = {k: np.concatenate([p[k] for p in self.parts]) for k in self.parts[0]}
        day = pd.to_datetime(pd.Series([dt.date.fromordinal(int(o)) for o in cols["day"]]))
        created = day + pd.to_timedelta(cols["seconds"], unit="s")
        end_of_data = pd.Timestamp(END) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        updated = created + pd.to_timedelta(cols["updated_delay"], unit="s")
        updated = updated.where(updated <= end_of_data, end_of_data)

        cust = customers.iloc[cols["customer"]].reset_index(drop=True)
        prod = products.iloc[cols["product"]].reset_index(drop=True)
        frame = pd.DataFrame(
            {
                "_created": created,
                "created_at": ist_stamp(created),
                "updated_at": ist_stamp(updated),
                "customer_id": cust["id"],
                "sales_channel": np.array(CHANNELS, dtype=object)[cols["channel"]],
                "financial_status": np.where(cols["refund"] > 0, "refunded", "paid"),
                "fulfillment_status": "fulfilled",
                "payment_gateway": cols["gateway"],
                "_method": cols["method"],
                "_south_candidate": cols["south_candidate"],
                "_roll_south": cols["roll_south"],
                "product_id": prod["id"],
                "product_title": prod["title"],
                "product_type": prod["product_type"],
                "sku": prod["sku"],
                "quantity": cols["quantity"],
                "unit_price": cols["unit_price"].astype(int),
                "total_line_items_price": cols["line_total"].round(2),
                "total_discounts": cols["discount"],
                "total_price": cols["total"],
                "total_refunds": cols["refund"].round(2),
                "currency": "INR",
                "shipping_city": cust["city"],
                "shipping_state": cust["state"],
                "shipping_region": cust["region"],
            }
        )
        frame, self.south_drop_rate, self.dropped_south_net = apply_south_decline(frame)
        frame = frame.sort_values("_created", kind="stable").reset_index(drop=True)
        frame.insert(0, "order_number", 1001 + np.arange(len(frame)))
        frame.insert(0, "name", "#" + frame["order_number"].astype(str))
        frame.insert(0, "id", 5_000_000_001 + np.arange(len(frame)))
        return frame


# --- Derived sources --------------------------------------------------------------------------------------------
_ALPHABET = np.array(list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"))


def random_ids(rng: np.random.Generator, prefix: str, n: int) -> list[str]:
    chars = _ALPHABET[rng.integers(0, len(_ALPHABET), (n, 14))]
    return [prefix + "".join(row) for row in chars]


def make_payments(rng: np.random.Generator, orders: pd.DataFrame) -> pd.DataFrame:
    paid = orders[orders["payment_gateway"] == "razorpay"].reset_index(drop=True)
    n = len(paid)
    amount = np.round(paid["total_price"].to_numpy() * 100).astype(int)
    refunded = np.round(paid["total_refunds"].to_numpy() * 100).astype(int)
    fee = np.round(amount * 0.02).astype(int)
    unix = (
        (paid["_created"] - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)
    ).to_numpy() - 19_800
    return pd.DataFrame(
        {
            "id": random_ids(rng, "pay_", n),
            "entity": "payment",
            "amount": amount,
            "currency": "INR",
            "status": np.where(refunded > 0, "refunded", "captured"),
            "order_id": random_ids(rng, "order_", n),
            "method": paid["_method"],
            "captured": True,
            "description": "Order " + paid["name"],
            "amount_refunded": refunded,
            "fee": fee,
            "tax": np.round(fee * 0.18).astype(int),
            "notes_order_name": paid["name"],
            "created_at": unix + rng.integers(5, 120, n),
        }
    )


def make_campaigns(rng: np.random.Generator) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for quarter_start in [m for m in month_starts(START, END) if m.month in (1, 4, 7, 10)]:
        quarter_end = dt.date(
            quarter_start.year,
            quarter_start.month + 2,
            calendar.monthrange(quarter_start.year, quarter_start.month + 2)[1],
        )
        for channel, monthly_spend, ctr, cvr, aov, roas in MARKETING_CHANNELS:
            amplitude = rng.uniform(0.15, 0.25)

            def noisy(value: float, amp: float = amplitude) -> float:
                return value * (1 + rng.uniform(-amp, amp))

            spend = noisy(monthly_spend * 3)
            revenue = spend * noisy(roas)
            order_value = noisy(aov)
            conversions = revenue / order_value
            clicks = conversions / noisy(cvr)
            impressions = clicks / noisy(ctr)
            rows.append(
                {
                    "quarter": quarter_label(quarter_start),
                    "channel": channel,
                    "start": quarter_start,
                    "end": quarter_end,
                    "spend": spend,
                    "impressions": impressions,
                    "clicks": clicks,
                    "conversions": conversions,
                    "revenue": revenue,
                    "aov": order_value,
                    "target_roas": roas,
                }
            )
    frame = pd.DataFrame(rows)
    # Scale volumes per channel so the aggregate ROAS equals the answer-key value (ratios CTR/CVR/AOV unchanged).
    for channel, wanted in CHANNEL_ROAS_TARGET.items():
        mask = frame["channel"] == channel
        factor = wanted * frame.loc[mask, "spend"].sum() / frame.loc[mask, "revenue"].sum()
        for column in ("impressions", "clicks", "conversions", "revenue"):
            frame.loc[mask, column] *= factor
    frame["spend"] = frame["spend"].round(0).astype(int)
    for column in ("impressions", "clicks", "conversions", "revenue"):
        frame[column] = frame[column].round(0).astype(int)
    frame["aov"] = frame["aov"].round(0).astype(int)
    frame["ctr"] = (frame["clicks"] / frame["impressions"]).round(4)
    frame["conversion_rate"] = (frame["conversions"] / frame["clicks"]).round(4)
    frame["roas"] = (frame["revenue"] / frame["spend"]).round(2)
    frame["campaign"] = frame["channel"] + " always-on " + frame["quarter"]
    return frame


def make_budget(rng: np.random.Generator) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, month in enumerate(month_starts(START, END)):
        for department, base in DEPARTMENT_BUDGET:
            budget = round(base * (1 + BUDGET_GROWTH) ** index, -2)
            factor = float(np.clip(rng.normal(1.0, 0.035), 0.94, 1.06))
            key = (month.year, month.month)
            if department == "Marketing" and key in MARKETING_Q3_2026_OVERSPEND:
                factor = MARKETING_Q3_2026_OVERSPEND[key]
            if department == "Operations" and key in OPERATIONS_FESTIVE_OVERSPEND:
                factor = OPERATIONS_FESTIVE_OVERSPEND[key]
            rows.append(
                {
                    "month": month,
                    "department": department,
                    "budget": int(budget),
                    "actual": int(round(budget * factor, -2)),
                }
            )
    return pd.DataFrame(rows)


def make_targets(
    rng: np.random.Generator, orders: pd.DataFrame, dropped_south_net: dict[tuple[int, int], float]
) -> pd.DataFrame:
    net = orders["total_price"] - orders["total_refunds"]
    month = orders["_created"].dt.to_period("M")
    actual = net.groupby([orders["shipping_region"], month]).sum()
    rows: list[dict[str, Any]] = []
    for region in REGIONS:
        for start in month_starts(START, END):
            period = pd.Period(start, "M")
            value = float(actual.get((region, period), 0.0))
            in_q3 = start >= dt.date(2026, 7, 1)
            if in_q3 and region == "South":
                # Targets were set on the pre-hike run rate, so the lost South orders become a miss.
                value += dropped_south_net.get((start.year, start.month), 0.0)
                factor = rng.uniform(0.93, 0.98)
            elif in_q3:
                factor = rng.uniform(0.92, 0.97)
            else:
                factor = rng.uniform(0.90, 1.06)
            rows.append(
                {"month": start, "region": region, "target": int(round(value * factor, -3))}
            )
    return pd.DataFrame(rows)


# --- Writers ----------------------------------------------------------------------------------------------------
_FIXED_TIME = dt.datetime(2026, 11, 9, 0, 0, 0)


def _workbook() -> Workbook:
    """An empty workbook (the default sheet is removed) with fixed metadata so output is reproducible."""
    wb = Workbook()
    wb.remove(wb[wb.sheetnames[0]])
    wb.properties.creator = "UrbanCart (synthetic demo data)"
    wb.properties.created = _FIXED_TIME
    wb.properties.modified = _FIXED_TIME
    return wb


def _write_sheet(
    ws: Any,
    headers: list[str],
    rows: list[list[Any]],
    money_columns: tuple[int, ...] = (),
    date_columns: tuple[int, ...] = (),
    percent_columns: tuple[int, ...] = (),
) -> None:
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for column_index, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(column_index)].width = max(12, len(header) + 2)
    for index in money_columns:
        for cell in ws.iter_cols(min_col=index, max_col=index, min_row=2):
            for c in cell:
                c.number_format = "#,##0"
    for index in date_columns:
        for cell in ws.iter_cols(min_col=index, max_col=index, min_row=2):
            for c in cell:
                c.number_format = "dd/mm/yyyy"
    for index in percent_columns:
        for cell in ws.iter_cols(min_col=index, max_col=index, min_row=2):
            for c in cell:
                c.number_format = "0.0%"
    ws.freeze_panes = "A2"


def write_marketing_workbook(path: Path, campaigns: pd.DataFrame, targets: pd.DataFrame) -> None:
    wb = _workbook()
    headers = [
        "Channel",
        "Campaign",
        "Start date",
        "End date",
        "Spend (INR)",
        "Impressions",
        "Clicks",
        "CTR",
        "Conversions",
        "Conversion rate",
        "Avg order value (INR)",
        "Revenue (INR)",
        "ROAS",
        "Target ROAS",
    ]
    for quarter, group in campaigns.groupby("quarter", sort=False):
        rows = [
            [
                r.channel,
                r.campaign,
                r.start,
                r.end,
                r.spend,
                r.impressions,
                r.clicks,
                r.ctr,
                r.conversions,
                r.conversion_rate,
                r.aov,
                r.revenue,
                r.roas,
                r.target_roas,
            ]
            for r in group.itertuples()
        ]
        _write_sheet(
            wb.create_sheet(str(quarter)),
            headers,
            rows,
            money_columns=(5, 6, 7, 9, 11, 12),
            date_columns=(3, 4),
            percent_columns=(8, 10),
        )
    target_rows = [[r.month, r.region, r.target] for r in targets.itertuples()]
    _write_sheet(
        wb.create_sheet("Targets"),
        ["Month", "Region", "Revenue target (INR)"],
        target_rows,
        money_columns=(3,),
        date_columns=(1,),
    )
    wb.save(path)


def write_budget_workbook(path: Path, budget: pd.DataFrame) -> None:
    wb = _workbook()
    ws = wb.create_sheet("Budget vs Actual")
    rows = [[r.month, r.department, r.budget, r.actual] for r in budget.itertuples()]
    _write_sheet(
        ws,
        ["Month", "Department", "Budget (INR)", "Actual (INR)"],
        rows,
        money_columns=(3, 4),
        date_columns=(1,),
    )
    wb.save(path)


@dataclass
class Tables:
    customers: pd.DataFrame
    products: pd.DataFrame
    orders: pd.DataFrame
    payments: pd.DataFrame
    campaigns: pd.DataFrame
    targets: pd.DataFrame
    budget: pd.DataFrame
    south_drop_rate: float


def generate(out_dir: Path = OUT_DIR_DEFAULT, seed: int = SEED, write_key: bool = True) -> Tables:
    """Build every source file in ``out_dir`` and return the tables (including the answer-key inputs)."""
    rng = np.random.default_rng(seed)
    customers = make_customers(rng)
    products = make_products(rng)
    simulator = OrderSimulator(rng, customers, products)
    simulator.run()
    orders = simulator.to_frame(customers, products)
    payments = make_payments(rng, orders)
    campaigns = make_campaigns(rng)
    budget = make_budget(rng)
    targets = make_targets(rng, orders, simulator.dropped_south_net)

    totals = orders.groupby("customer_id").agg(
        orders_count=("id", "size"),
        total_spent=("total_price", "sum"),
    )
    customer_out = customers.drop(columns=["weight", "signup_date"]).merge(
        totals, how="left", left_on="id", right_index=True
    )
    customer_out["orders_count"] = customer_out["orders_count"].fillna(0).astype(int)
    customer_out["total_spent"] = customer_out["total_spent"].fillna(0.0).round(2)

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_args: dict[str, Any] = {"index": False, "lineterminator": "\n"}
    orders.drop(columns=["_created", "_method"]).to_csv(out_dir / "shopify_orders.csv", **csv_args)
    customer_out.to_csv(out_dir / "shopify_customers.csv", **csv_args)
    products.to_csv(out_dir / "shopify_products.csv", **csv_args)
    payments.to_csv(out_dir / "razorpay_payments.csv", **csv_args)
    write_marketing_workbook(out_dir / "marketing_sheet.xlsx", campaigns, targets)
    write_budget_workbook(out_dir / "zoho_budget_expenses.xlsx", budget)

    tables = Tables(
        customers, products, orders, payments, campaigns, targets, budget, simulator.south_drop_rate
    )
    if write_key:
        from answer_key import render_answer_key  # local module next to this script

        (out_dir / "ANSWER_KEY.md").write_text(render_answer_key(tables), encoding="utf-8")
    return tables


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the UrbanCart demo sources and ANSWER_KEY.md"
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR_DEFAULT, help="output directory")
    parser.add_argument("--seed", type=int, default=SEED, help="random seed (default 42)")
    args = parser.parse_args()
    tables = generate(args.out, args.seed)
    print(f"Wrote demo sources to {args.out}")
    print(
        f"  customers {len(tables.customers):>7,}   orders {len(tables.orders):>7,}   "
        f"payments {len(tables.payments):>7,}   campaigns {len(tables.campaigns):>4,}"
    )


if __name__ == "__main__":
    main()
