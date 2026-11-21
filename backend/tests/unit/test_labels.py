"""Names in words and repeated metrics (M19 small fixes, D127)."""

from __future__ import annotations

from app.dictionary import labels
from app.dictionary.model import ColumnEntry, DatasetEntry, Dictionary, Metric


def _dictionary() -> Dictionary:
    columns = {
        "total_price": ColumnEntry(label="total_price", type="DOUBLE", role="measure", unit="INR"),
        "fee": ColumnEntry(label="Fee", type="DOUBLE", role="measure", unit="INR"),
        "product_type": ColumnEntry(label="product_type", type="VARCHAR", role="dimension"),
        "city": ColumnEntry(label="City name", type="VARCHAR", role="dimension"),
    }
    return Dictionary(
        datasets={"shopify_orders": DatasetEntry(name="shopify_orders", columns=columns)},
        metrics={
            "shopify_orders_count": Metric(expr="COUNT(*)", dataset="shopify_orders"),
            "sum_total_price": Metric(expr="SUM(total_price)", dataset="shopify_orders"),
            "sum_fee": Metric(expr="SUM(fee)", dataset="shopify_orders"),
            "revenue": Metric(expr="sum( total_price )", dataset="shopify_orders", source="ai"),
            "gross_margin": Metric(expr="SUM(fee) / 2", dataset="shopify_orders", source="ai"),
        },
    )


def test_drafted_metric_keys_read_as_words() -> None:
    d = _dictionary()
    assert labels.metric_label(d, "sum_fee") == "Total fee"
    assert labels.metric_label(d, "sum_total_price") == "Total price"  # never "Total total price"
    assert labels.metric_label(d, "shopify_orders_count") == "Number of shopify orders"
    assert labels.metric_label(d, "gross_margin") == "Gross margin"
    assert labels.metric_label(d, "aov") == "Average order value"
    assert labels.metric_label(None, "sum_fee") == "Sum fee"  # no dictionary at hand: still words


def test_column_labels_read_as_words() -> None:
    entry = _dictionary().datasets["shopify_orders"]
    assert labels.column_label(entry, "product_type") == "Product type"
    assert labels.column_label(entry, "city") == "City name"  # a real label stays as written
    assert labels.column_label(None, "sales_channel") == "Sales channel"


def test_a_drafted_metric_that_repeats_the_ais_is_a_duplicate() -> None:
    d = _dictionary()
    assert labels.duplicates(d) == {"sum_total_price": "revenue"}
    assert labels.distinct_metrics(d) == [
        "shopify_orders_count",
        "sum_fee",
        "revenue",
        "gross_margin",
    ]


def test_metrics_of_other_tables_or_by_the_owner_are_never_duplicates() -> None:
    d = _dictionary()
    d.metrics["owner_total"] = Metric(expr="SUM(fee)", dataset="other_table", source="owner")
    d.metrics["revenue"].source = "owner"
    assert labels.duplicates(d) == {"sum_total_price": "revenue"}  # sum_fee is on another table
