"""Spend, CTR, CPA and ROAS by channel or campaign (BUILD_SPEC 10.1 ``marketing_efficiency``).

Works on any table that has a spend column and a channel or campaign column; the columns are found by their
names, labels and the dictionary's synonyms. Several tables with the same columns (one tab per quarter) are added
together. Ratios are computed from the totals (sum of revenue / sum of spend), never averaged from rows.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.analytics.common import (
    Analysis,
    AnalyticsError,
    Period,
    Result,
)
from app.dictionary.model import DatasetEntry
from app.security.sql_guard import safe_identifier as q

ROLES: dict[str, re.Pattern[str]] = {
    "spend": re.compile(r"(spend|ad_?cost|amount_spent|cost_inr|^cost$|media_cost)", re.I),
    "impressions": re.compile(r"impression", re.I),
    "clicks": re.compile(r"^clicks?$|link_clicks", re.I),
    "conversions": re.compile(r"^(conversions?|purchases|results|orders)$", re.I),
    "revenue": re.compile(
        r"(^revenue|conversion_value|purchase_value|attributed_revenue|^sales(_inr)?$)", re.I
    ),
    "channel": re.compile(r"^(channel|platform|source|medium|network)$", re.I),
    "campaign": re.compile(r"^campaign(_name)?$", re.I),
}
_NOT = re.compile(r"(target|rate|ctr|roas|avg|average|per_)", re.I)


def find_columns(entry: DatasetEntry) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, column in entry.columns.items():
        words = [name, column.label, *column.synonyms]
        for role, pattern in ROLES.items():
            if role in found:
                continue
            if any(
                pattern.search(re.sub(r"[^a-z0-9]+", "_", w.lower()).strip("_")) for w in words
            ) and not _NOT.search(name):
                found[role] = name
                break
    return found


def campaign_tables(analysis: Analysis) -> list[str]:
    return [
        key
        for key, entry in analysis.dictionary.datasets.items()
        if (cols := find_columns(entry)).get("spend") and ("channel" in cols or "campaign" in cols)
    ]


def marketing_efficiency(
    analysis: Analysis,
    tables: list[str] | None = None,
    period: Period | None = None,
    by: str = "channel",
) -> Result:
    if by not in ("channel", "campaign"):
        raise AnalyticsError("bad_group", by)
    chosen = tables or campaign_tables(analysis)
    if not chosen:
        raise AnalyticsError("no_marketing_data")
    parts: list[pd.DataFrame] = []
    notes: list[str] = []
    for key in chosen:
        entry = analysis.dictionary.datasets.get(key)
        if entry is None:
            raise AnalyticsError("unknown_table", key)
        cols = find_columns(entry)
        group = cols.get(by) or cols.get("channel") or cols.get("campaign")
        if "spend" not in cols or group is None:
            notes.append(f"skipped:{key}")
            continue
        analysis.table(key)
        select = [f"CAST({q(group)} AS VARCHAR) AS name"]
        for role in ("spend", "impressions", "clicks", "conversions", "revenue"):
            select.append(f"SUM({q(cols[role])}) AS {role}" if role in cols else f"NULL AS {role}")
        clause, params = "", []
        if period is not None and entry.time_column:
            clause = f"WHERE {q(entry.time_column)} >= ? AND {q(entry.time_column)} < ?"
            params = [period.start, period.end_exclusive]
        parts.append(
            analysis.frame(f"SELECT {', '.join(select)} FROM {q(key)} {clause} GROUP BY 1", params)  # noqa: S608
        )
    if not parts:
        raise AnalyticsError("no_marketing_data")
    data = pd.concat(parts, ignore_index=True)
    for role in ("spend", "impressions", "clicks", "conversions", "revenue"):
        data[role] = pd.to_numeric(data[role], errors="coerce").astype("float64")
    totals = data.groupby("name", dropna=True).sum(min_count=1).reset_index()
    total_spend = float(totals["spend"].sum())
    totals["share_of_spend_pct"] = totals["spend"] / total_spend * 100.0 if total_spend else None
    totals["roas"] = totals["revenue"] / totals["spend"].where(totals["spend"] > 0)
    totals["ctr_pct"] = (
        totals["clicks"] / totals["impressions"].where(totals["impressions"] > 0) * 100.0
    )
    totals["conversion_rate_pct"] = (
        totals["conversions"] / totals["clicks"].where(totals["clicks"] > 0) * 100.0
    )
    totals["cpa"] = totals["spend"] / totals["conversions"].where(totals["conversions"] > 0)
    totals = totals.sort_values("roas", ascending=False, na_position="last").reset_index(drop=True)
    with_roas = totals.dropna(subset=["roas"])
    facts: dict[str, Any] = {
        "by": by,
        "tables": chosen,
        "period": period.as_json() if period else None,
        "total_spend": total_spend,
        "total_revenue": float(totals["revenue"].sum())
        if totals["revenue"].notna().any()
        else None,
        "overall_roas": float(totals["revenue"].sum() / total_spend)
        if total_spend and totals["revenue"].notna().any()
        else None,
    }
    if len(with_roas):
        facts["best"] = {
            "name": with_roas.iloc[0]["name"],
            "roas": float(with_roas.iloc[0]["roas"]),
        }
        facts["worst"] = {
            "name": with_roas.iloc[-1]["name"],
            "roas": float(with_roas.iloc[-1]["roas"]),
        }
        facts["losing_money"] = with_roas[with_roas["roas"] < 1]["name"].tolist()
        top = totals.sort_values("spend", ascending=False).iloc[0]
        facts["largest_spend"] = {
            "name": top["name"],
            "share_of_spend_pct": float(top["share_of_spend_pct"]),
        }
    if totals["revenue"].isna().all():
        notes.append("no_revenue_column")
    notes.append("attribution_limits")
    return Result(totals, facts, notes, chosen, list(analysis.sql_log))
