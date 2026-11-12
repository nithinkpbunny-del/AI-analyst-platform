"""The UrbanCart demo sources (``eval/fixtures``) read exactly as the file connector reads them, with a dictionary
drafted in code and the metrics an Owner would confirm. Shared by the analytics and agent tests.

Reading the files takes a few seconds, so the parsed tables are kept for the whole test session.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from app.analytics.common import Analysis
from app.connectors import file_formats, tabular
from app.data.workspace import Workspace
from app.dictionary.draft import DatasetInput, build
from app.dictionary.model import Dictionary, Metric, Relationship
from app.dictionary.profiler import profile_frame
from app.dictionary.units import to_units

if TYPE_CHECKING:
    from app.agent.tools import ToolContext

FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "fixtures"
FILES = {
    "shopify_orders": ("shopify_orders.csv", None),
    "shopify_customers": ("shopify_customers.csv", None),
    "shopify_products": ("shopify_products.csv", None),
    "razorpay_payments": ("razorpay_payments.csv", None),
    "budget_vs_actual": ("zoho_budget_expenses.xlsx", "Budget vs Actual"),
    "targets": ("marketing_sheet.xlsx", "Targets"),
    **{
        f"q{q}_{y}": ("marketing_sheet.xlsx", f"Q{q} {y}")
        for y, quarters in ((2025, (1, 2, 3, 4)), (2026, (1, 2, 3)))
        for q in quarters
    },
}


@cache
def parsed(key: str) -> tabular.ParsedTable:
    filename, sheet = FILES[key]
    data = (FIXTURES / filename).read_bytes()
    kind = file_formats.file_kind(filename, data)
    rows = file_formats.rows(kind, data, filename, sheet or filename)
    return tabular.parse_rows(rows)


def _info(column: tabular.ColumnInfo) -> dict[str, object]:
    return {
        "name": column.name,
        "label": column.label,
        "type": column.type,
        "unit": column.unit,
        "currencies": list(column.currencies),
    }


@cache
def _dictionary() -> Dictionary:
    inputs = []
    for key in FILES:
        table = parsed(key)
        filename = FILES[key][0]
        profile = profile_frame(table.frame, [_info(c) for c in table.columns], name=key)
        inputs.append(DatasetInput(f"ds-{key}", f"conn-{filename}", filename, key, profile))
    dictionary = build(inputs, None, company_name="UrbanCart").dictionary
    dictionary.company.industry = "d2c"
    dictionary.metrics.update(
        {
            "revenue": Metric(
                expr="SUM(total_price) - SUM(total_refunds)",
                dataset="shopify_orders",
                unit="INR",
                good_direction="up",
                meaning="Sales after refunds",
                source="owner",
                valid=True,
            ),
            "orders": Metric(
                expr="COUNT(*)",
                dataset="shopify_orders",
                unit="count",
                good_direction="up",
                valid=True,
            ),
            "aov": Metric(expr="revenue / orders", unit="INR", good_direction="up", valid=True),
            "discounts": Metric(
                expr="SUM(total_discounts)",
                dataset="shopify_orders",
                unit="INR",
                good_direction="down",
                valid=True,
            ),
        }
    )
    dictionary.relationships = [
        Relationship(
            left="shopify_orders.customer_id", right="shopify_customers.id", match_rate=1.0
        ),
        Relationship(left="shopify_orders.product_id", right="shopify_products.id", match_rate=1.0),
    ]
    return dictionary


def dictionary() -> Dictionary:
    return _dictionary().model_copy(deep=True)


def frame(key: str) -> pd.DataFrame:
    return to_units(parsed(key).frame, _dictionary().datasets[key])


def workspace(*keys: str, max_rows: int = 200) -> Workspace:
    ws = Workspace(timeout_seconds=30, max_rows=max_rows)
    for key in keys or tuple(FILES):
        ws.load(key, frame(key), {"dataset": key})
    return ws


def analysis(*keys: str) -> Analysis:
    """An analysis over the demo tables; tables not loaded up front are loaded when first used."""
    ws = workspace(*keys) if keys else Workspace(timeout_seconds=30)
    d = dictionary()

    def ensure(key: str) -> None:
        ws.load(key, frame(key), {"dataset": key})

    return Analysis(ws, d, ensure)


def agent_context(*, hidden: tuple[str, ...] = (), max_rows: int = 200) -> ToolContext:
    """A ``ToolContext`` over the demo tables, as the agent sees them (no database, no live sources): every table
    is a visible dataset of a connection named after its file, except those in ``hidden``."""
    import uuid
    from datetime import date as _date

    from app.agent.access import AccessContext, DatasetRef, visible_dictionary
    from app.agent.provenance import ResultStore
    from app.agent.tools import ToolContext

    d = dictionary()
    refs: dict[uuid.UUID, DatasetRef] = {}
    existing: set[uuid.UUID] = set()
    for key, entry in d.datasets.items():
        dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, key)
        connection_id = uuid.uuid5(uuid.NAMESPACE_URL, FILES[key][0])
        entry.dataset_id, entry.connection_id = str(dataset_id), str(connection_id)
        existing.add(dataset_id)
        if key in hidden:
            continue
        refs[dataset_id] = DatasetRef(
            dataset_id, connection_id, FILES[key][0], "file", key, len(parsed(key).frame), {}
        )
    access = AccessContext(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="analyst",
        org_name="UrbanCart",
        industry="d2c",
        timezone="Asia/Kolkata",
        impact_threshold_pct=5.0,
        impact_threshold_inr=100_000.0,
        connection_ids=frozenset(r.connection_id for r in refs.values()),
        datasets=refs,
        existing_dataset_ids=frozenset(existing),
    )
    visible = visible_dictionary(d, access)
    ws = Workspace(timeout_seconds=30, max_rows=max_rows)
    for key in visible.dictionary.datasets:
        ws.load(key, frame(key), {"dataset": key})
    return ToolContext(
        access=access,
        visible=visible,
        workspace=ws,
        cache=None,
        store=ResultStore(),
        today=_date(2026, 11, 12),
        max_rows=max_rows,
    )
