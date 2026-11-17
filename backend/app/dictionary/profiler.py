"""Profiling a dataset in plain code, no AI (BUILD_SPEC 9 step 1; M7).

``profile_frame`` looks at a table once and records, per column: type, empty share, distinct count, range, up to
five sample values and a role guess; for the table: the likely time column and keys. It also collects the
**evidence** for money columns and their unit (DECISIONS D59):

* ``rupee_marker``: values or the header carry ₹ / Rs / INR;
* ``currency_column``: a sibling column such as ``currency`` has one value, INR;
* ``money_name``: the name says money (price, amount, revenue, discount, refund, ...);
* ``paise_name`` / ``paise_signature`` / ``paise_amount``: whole numbers that are most likely paise
  (a name saying so, a Razorpay-style payments table, or an integer ``amount`` beside an INR currency column).

Samples of columns that look personal (email, phone, names, addresses, ...) are never kept: they are not shown in
the web app and never sent to a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import numpy as np
import pandas as pd

PROFILE_VERSION = 1
SAMPLES = 5
SAMPLE_LENGTH = 40
# Distinct counting and ranges look at the whole table up to this many rows (a random sample beyond that).
MAX_PROFILE_ROWS = 500_000

MONEY_NAME = re.compile(
    r"(price|amount|revenue|sales|discount|refund|total|fee|tax|cost|payout|salary|spend|budget|"
    r"balance|mrp|gmv|income|expense|profit|margin|charge|commission|paid|payment)",
    re.IGNORECASE,
)
NOT_MONEY_NAME = re.compile(
    r"(^|_)(rate|pct|percent|ratio|share|count|qty|quantity|num|number|days|hours|score|index|id)(_|$)",
    re.IGNORECASE,
)
CURRENCY_COLUMN = re.compile(r"^(currency|currency_code|curr)$", re.IGNORECASE)
RUPEE_VALUES = frozenset({"inr", "₹", "rs", "rs.", "rupee", "rupees"})
PII_NAME = re.compile(
    r"(email|e_mail|phone|mobile|contact_no|first_name|last_name|full_name|customer_name|"
    r"address|street|gstin|aadhaar|aadhar|(^|_)pan($|_)|vpa|(^|_)card($|_)|ip_address|password)",
    re.IGNORECASE,
)
PII_VALUE = re.compile(r"(?:@[\w-]+\.[\w.-]+|^\+?\d[\d\s-]{8,13}$)")
KEY_NAME = re.compile(r"(^|_)(id|sku|code|ref|number|no|uuid)$", re.IGNORECASE)
TIME_RANK = ("created", "order", "date", "timestamp", "time", "paid", "updated")
# Columns a payments export has; five or more (with amount and currency) is a Razorpay-style table.
PAYMENT_COLUMNS = frozenset(
    {
        "entity",
        "status",
        "order_id",
        "method",
        "captured",
        "amount_refunded",
        "fee",
        "tax",
        "international",
        "refund_status",
        "card_id",
        "vpa",
        "bank",
        "wallet",
    }
)
PAISE_COLUMN = re.compile(
    r"(^amount$|_amount$|^amount_|^fee$|^tax$|^fees$|refunded$)", re.IGNORECASE
)


def _short(value: Any) -> str:
    return str(value)[:SAMPLE_LENGTH]


def _json_number(value: Any) -> float | int | None:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    return float(value)


def _is_personal(name: str, samples: pd.Series) -> bool:
    if PII_NAME.search(name):
        return True
    text = samples.astype("string").dropna().head(50)
    return bool(len(text)) and bool(text.str.contains(PII_VALUE, regex=True).any())


def _range(
    series: pd.Series, kind: str
) -> tuple[str | float | int | None, str | float | int | None]:
    present = series.dropna()
    if present.empty:
        return None, None
    if kind in ("integer", "number"):
        return _json_number(present.min()), _json_number(present.max())
    if kind in ("date", "datetime"):
        return str(present.min()), str(present.max())
    return None, None


def currency_of(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    """(column name, its single value) when a currency column has exactly one value."""
    for name in frame.columns:
        if not CURRENCY_COLUMN.match(str(name)):
            continue
        values = frame[name].dropna().astype("string").str.strip().str.lower().unique()
        if len(values) == 1:
            return str(name), str(values[0])
        return str(name), None
    return None, None


def is_payments_table(names: set[str]) -> bool:
    lowered = {n.lower() for n in names}
    return {"amount", "currency"} <= lowered and len(lowered & PAYMENT_COLUMNS) >= 3


def money_evidence(
    name: str,
    info: dict[str, Any],
    series: pd.Series,
    *,
    currency_column: str | None,
    currency_value: str | None,
    payments: bool,
) -> dict[str, Any] | None:
    """What says this numeric column is money, in which unit, and whether it is stored in paise."""
    kind = info.get("type")
    if kind not in ("integer", "number"):
        return None
    evidence: list[str] = []
    unit: str | None = None
    divide_by: int | None = None
    reason: str | None = None
    declared = info.get("unit")
    if declared == "percent":
        return {
            "unit": "percent",
            "divide_by": None,
            "evidence": ["percent_marker"],
            "reason": None,
        }
    named_money = bool(MONEY_NAME.search(name)) and not NOT_MONEY_NAME.search(name)
    if info.get("stored_in") == "rupees":
        # The source (Shopify's API) says the amount is in rupees: nothing to divide (D95).
        evidence.append("source_rupees")
        unit = "INR"
    elif declared == "INR":
        evidence.append("rupee_marker")
        unit = "INR"
    if named_money:
        evidence.append("money_name")
    if currency_column is not None and currency_value in RUPEE_VALUES and (named_money or unit):
        evidence.append("currency_column")
        unit = "INR"
    present = series.dropna()
    whole = bool(len(present)) and bool((present == present.round()).all())
    if "rupee_marker" not in evidence and "source_rupees" not in evidence:
        # No ₹ in the data: whole numbers in a payments table (or named "paise") are most likely paise.
        if info.get("stored_in") == "paise" and kind == "integer":
            evidence.append("source_paise")  # the source (Razorpay's API) says so itself
            unit, divide_by, reason = "INR", 100, "paise"
        elif "paise" in name.lower():
            evidence.append("paise_name")
            unit, divide_by, reason = "INR", 100, "paise"
        elif payments and kind == "integer" and PAISE_COLUMN.search(name) and whole:
            evidence.append("paise_signature")
            unit, divide_by, reason = "INR", 100, "paise"
        elif (
            kind == "integer"
            and re.search(r"(^amount$|_amount$)", name, re.IGNORECASE)
            and "currency_column" in evidence
            and whole
            and float(present.min()) >= 100
        ):
            evidence.append("paise_amount")
            divide_by, reason = 100, "paise_guess"
    if unit is None and named_money:
        if currency_value is not None and currency_value not in RUPEE_VALUES:
            # One currency, and it is not rupees: leave the unit for the Owner.
            evidence.append("other_currency")
            reason = "other_currency"
        else:
            # Named like money but nothing in the data names a currency: assume rupees, ask the Owner.
            unit, reason = "INR", "money_name_only"
    if unit is None and reason is None:
        return None
    return {"unit": unit, "divide_by": divide_by, "evidence": evidence, "reason": reason}


def _role_guess(
    name: str, kind: str, distinct: int, rows: int, money: dict[str, Any] | None
) -> str:
    unique_ratio = distinct / rows if rows else 0.0
    if kind in ("date", "datetime"):
        return "time"
    if kind == "boolean":
        return "dimension"
    if KEY_NAME.search(name) and not (money and money.get("unit") == "INR"):
        return "key"
    if kind in ("integer", "number"):
        return "measure"
    if unique_ratio > 0.9 and rows > 20:
        return "detail"
    if distinct <= max(50, rows * 0.05):
        return "dimension"
    return "detail"


def _time_column(columns: list[dict[str, Any]]) -> str | None:
    times = [c for c in columns if c["type"] in ("date", "datetime")]
    if not times:
        return None

    def rank(column: dict[str, Any]) -> int:
        lowered = column["name"].lower()
        for position, word in enumerate(TIME_RANK):
            if word in lowered:
                return position
        return len(TIME_RANK)

    return str(min(times, key=rank)["name"])


def profile_frame(
    frame: pd.DataFrame, columns: list[dict[str, Any]], *, name: str
) -> dict[str, Any]:
    """The profile of one table. ``columns`` are the reader's column infos (``datasets.schema_json``)."""
    rows = len(frame)
    work = frame if rows <= MAX_PROFILE_ROWS else frame.sample(MAX_PROFILE_ROWS, random_state=1)
    names = {str(c["name"]) for c in columns}
    cur_col, cur_val = currency_of(work)
    payments = is_payments_table(names)
    out: list[dict[str, Any]] = []
    for info in columns:
        column = str(info["name"])
        if column not in work:
            continue
        series = work[column]
        kind = str(info["type"])
        scale = rows / len(work) if len(work) else 1
        nulls = int(series.isna().sum())
        distinct = int(series.nunique(dropna=True))
        personal = kind == "text" and _is_personal(column, series)
        personal = personal or bool(PII_NAME.search(column))
        low, high = _range(series, kind)
        present = series.dropna()
        samples = (
            []
            if personal
            else [_short(v) for v in present.drop_duplicates().head(SAMPLES).tolist()]
        )
        money = money_evidence(
            column,
            info,
            series,
            currency_column=cur_col,
            currency_value=cur_val,
            payments=payments,
        )
        out.append(
            {
                "name": column,
                "label": str(info.get("label") or column),
                "type": kind,
                "null_pct": round(nulls / len(work) * 100, 1) if len(work) else 0.0,
                "distinct": round(distinct * scale) if distinct < len(work) * 0.9 else distinct,
                "unique_ratio": round(distinct / max(len(present), 1), 3),
                "min": low,
                "max": high,
                "samples": samples,
                "personal": personal,
                "role": _role_guess(column, kind, distinct, len(present), money),
                "money": money,
            }
        )
    return {
        "version": PROFILE_VERSION,
        "name": name,
        "row_count": rows,
        "time_column": _time_column(out),
        "payments_table": payments,
        "columns": out,
    }


def structure_hash(profiles: list[dict[str, Any]]) -> str:
    """Identifies the *structure* behind a dictionary: tables, column names and types, units. New rows do not
    change it; a new column, a different type or a different unit does."""
    parts = [
        {
            "dataset": p["dataset_id"],
            "name": p["name"],
            "columns": [
                [
                    c["name"],
                    c["type"],
                    (c["money"] or {}).get("unit"),
                    (c["money"] or {}).get("divide_by"),
                ]
                for c in p["profile"]["columns"]
            ],
        }
        for p in sorted(profiles, key=lambda p: str(p["dataset_id"]))
    ]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
