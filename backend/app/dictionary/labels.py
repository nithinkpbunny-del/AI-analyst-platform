"""Names people read, and metrics that say the same thing twice (M19 small fixes, DECISIONS D127).

* ``metric_label``: the drafted metrics are keyed ``sum_<column>`` and ``<table>_count`` (``dictionary.draft``);
  screens and sentences show "Total fee" and "Number of orders" instead, and any other key in words
  ("gross_margin" -> "Gross margin").
* ``column_label``: a column's own label, in words when the label is only the raw name ("product_type").
* ``duplicates``: a metric drafted by code (``sum_total_price``) is a duplicate when another metric of the same
  table has the same formula (the AI's or the Owner's ``revenue = SUM(total_price)``). Duplicates stay in the
  dictionary (goals, tiles and old answers may use their key), but pickers and suggestions leave them out and the
  dictionary screen folds them under the metric they repeat.
"""

from __future__ import annotations

import re

from app.dictionary.model import DatasetEntry, Dictionary

KNOWN = {"aov": "Average order value", "revenue": "Revenue", "orders": "Orders"}
_RAW = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")


def words(name: str) -> str:
    text = re.sub(r"[_\s]+", " ", name).strip()
    return text[:1].upper() + text[1:] if text else name


def column_label(entry: DatasetEntry | None, name: str) -> str:
    column = entry.columns.get(name) if entry else None
    label = column.label if column and column.label else name
    return words(label) if _RAW.match(label) or label == name else label


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() or len(text) == 1 else text


def metric_label(dictionary: Dictionary | None, key: str) -> str:
    if key in KNOWN:
        return KNOWN[key]
    metric = dictionary.metrics.get(key) if dictionary else None
    entry = (
        dictionary.datasets.get(metric.dataset)
        if dictionary and metric and metric.dataset
        else None
    )
    if metric is not None and entry is not None:
        expr = _formula(metric.expr)
        if key.startswith("sum_") and expr == f"sum({key[4:].lower()})":
            label = column_label(entry, key[4:])
            return label if label.lower().startswith("total") else f"Total {_lower_first(label)}"
        if key.endswith("_count") and expr == "count(*)":
            return f"Number of {_lower_first(words(entry.name))}"
    return words(key)


def _formula(expr: str) -> str:
    return re.sub(r"\s+", "", expr).lower()


def duplicates(dictionary: Dictionary) -> dict[str, str]:
    """``{duplicate key: the metric it repeats}``. Only code-drafted metrics are ever duplicates; the one kept is
    the AI's, the Owner's or a carried one (first in the dictionary's order when there are several)."""
    kept: dict[tuple[str | None, str], str] = {}
    for key, metric in dictionary.metrics.items():
        if metric.source != "code":
            kept.setdefault((metric.dataset, _formula(metric.expr)), key)
    out: dict[str, str] = {}
    for key, metric in dictionary.metrics.items():
        if metric.source != "code":
            continue
        other = kept.get((metric.dataset, _formula(metric.expr)))
        if other is not None and other != key:
            out[key] = other
    return out


def distinct_metrics(dictionary: Dictionary) -> list[str]:
    """The dictionary's metric keys without the duplicates, in order."""
    repeated = duplicates(dictionary)
    return [key for key in dictionary.metrics if key not in repeated]
