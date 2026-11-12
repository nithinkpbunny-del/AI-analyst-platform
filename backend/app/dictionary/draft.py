"""The code-only part of drafting a dictionary (BUILD_SPEC 9 steps 1-2; M7).

``build`` turns profiles into a dictionary document **without any AI**: roles, units (from the profiler's
evidence), the time column, standard metrics and a diff against the dictionary it continues. When a dictionary
already exists (a confirmed version or the Owner's draft), everything the Owner or the AI already said is
carried over and only what is new is drafted. The AI step (``app.dictionary.ai``) then adds meanings, synonyms
and metrics, and ``links`` and ``validate`` check joins and metric expressions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.dictionary.model import (
    ColumnEntry,
    Company,
    DatasetEntry,
    Dictionary,
    Metric,
    slug,
)

# Higher is better for the business; the rest are costs or leakage.
_COST_NAME = re.compile(r"(discount|refund|cost|expense|fee|tax|charge|commission|returns?)", re.I)
MAX_MONEY_METRICS = 4
_TYPE_FAMILY = {"integer": "number", "number": "number", "date": "date", "datetime": "date"}


@dataclass(frozen=True)
class DatasetInput:
    dataset_id: str
    connection_id: str
    connection_name: str
    name: str
    profile: dict[str, Any]


@dataclass
class Built:
    dictionary: Dictionary
    diff: list[dict[str, Any]] = field(default_factory=list)
    # Keys of the datasets and (dataset key, column) pairs the AI should describe.
    new_datasets: list[str] = field(default_factory=list)
    new_columns: list[tuple[str, str]] = field(default_factory=list)


def humanize(name: str) -> str:
    text = re.sub(r"[_\s]+", " ", name).strip()
    return text[:1].upper() + text[1:] if text else name


def family(type_name: str) -> str:
    return _TYPE_FAMILY.get(type_name, type_name)


def dataset_keys(inputs: list[DatasetInput], taken: set[str] | None = None) -> dict[str, str]:
    """A short stable key per dataset (its name), made unique by the connection name, then a number."""
    used = set(taken or ())
    keys: dict[str, str] = {}
    by_base: dict[str, int] = {}
    for item in inputs:
        by_base[slug(item.name)] = by_base.get(slug(item.name), 0) + 1
    for item in inputs:
        base = slug(item.name)
        key = base
        if key in used or by_base[base] > 1:
            key = f"{slug(item.connection_name)}_{base}"[:60]
        number = 2
        unique = key
        while unique in used:
            unique = f"{key}_{number}"
            number += 1
        used.add(unique)
        keys[item.dataset_id] = unique
    return keys


def draft_column(profile_column: dict[str, Any]) -> ColumnEntry:
    money = profile_column.get("money") or {}
    role = profile_column["role"]
    unit = money.get("unit")
    reason = money.get("reason")
    if role == "key" and unit == "INR" and "rupee_marker" not in money.get("evidence", []):
        role = "measure"
    if unit is not None and role in ("dimension", "detail"):
        role = "measure"
    return ColumnEntry(
        label=profile_column["label"],
        type=profile_column["type"],
        meaning=humanize(profile_column["label"]),
        role=role,
        unit=unit,
        divide_by=money.get("divide_by"),
        evidence=list(money.get("evidence", [])),
        needs_review=reason is not None,
        review_reason=reason,
        source="code",
    )


def draft_dataset(item: DatasetInput) -> DatasetEntry:
    columns = {c["name"]: draft_column(c) for c in item.profile["columns"]}
    time_column = item.profile.get("time_column")
    return DatasetEntry(
        dataset_id=item.dataset_id,
        connection_id=item.connection_id,
        connection_name=item.connection_name,
        name=item.name,
        meaning=f"Rows of {item.name}",
        time_column=time_column,
        columns=columns,
    )


def standard_metrics(key: str, entry: DatasetEntry, taken: set[str]) -> dict[str, Metric]:
    """A row count and a total for the first few money columns (the AI adds the business's own metrics)."""
    metrics: dict[str, Metric] = {}
    count_name = f"{key}_count"[:100]
    if count_name not in taken:
        metrics[count_name] = Metric(
            expr="COUNT(*)",
            dataset=key,
            unit="count",
            good_direction=None,
            meaning=f"Number of rows in {entry.name}",
        )
    money = [
        (name, c) for name, c in entry.columns.items() if c.role == "measure" and c.unit == "INR"
    ]
    for name, column in money[:MAX_MONEY_METRICS]:
        metric_name = f"sum_{name}"[:100]
        if metric_name in taken or metric_name in metrics:
            continue
        metrics[metric_name] = Metric(
            expr=f"SUM({name})",
            dataset=key,
            unit="INR",
            good_direction="down" if _COST_NAME.search(name) else "up",
            meaning=f"Total of {column.label}",
        )
    return metrics


def _carry_dataset(
    key: str,
    old: DatasetEntry,
    item: DatasetInput,
    built: Built,
    *,
    flag_unmatched: bool,
) -> DatasetEntry:  # flag_unmatched: this table replaces another source's table (R2)
    fresh = {c["name"]: c for c in item.profile["columns"]}
    columns: dict[str, ColumnEntry] = {}
    for name, profile_column in fresh.items():
        known = old.columns.get(name)
        if known is None:
            entry = draft_column(profile_column)
            if flag_unmatched:
                entry.needs_review, entry.review_reason = True, entry.review_reason or "new_column"
            columns[name] = entry
            built.new_columns.append((key, name))
            built.diff.append({"kind": "column_added", "dataset": item.name, "column": name})
            continue
        entry = known.model_copy(deep=True)
        entry.label = profile_column["label"]
        if entry.type != profile_column["type"]:
            if family(entry.type) != family(profile_column["type"]):
                built.diff.append(
                    {
                        "kind": "type_changed",
                        "dataset": item.name,
                        "column": name,
                        "from": entry.type,
                        "to": profile_column["type"],
                    }
                )
                entry.needs_review, entry.review_reason = True, "type_changed"
            entry.type = profile_column["type"]
        fresh_money = profile_column.get("money") or {}
        if entry.source == "code" and not (
            entry.needs_review and entry.review_reason == "type_changed"
        ):
            drafted = draft_column(profile_column)
            entry.unit, entry.divide_by = drafted.unit, drafted.divide_by
            entry.evidence = drafted.evidence
        elif flag_unmatched and (
            fresh_money.get("unit") != entry.unit or fresh_money.get("divide_by") != entry.divide_by
        ):
            # Replacing a source: the carried mapping says one thing, the new data's evidence another.
            entry.needs_review, entry.review_reason = True, "unit_differs"
            entry.evidence = list(fresh_money.get("evidence", []))
        columns[name] = entry
    for name in old.columns:
        if name not in fresh:
            built.diff.append(
                {
                    "kind": "column_unmatched" if flag_unmatched else "column_removed",
                    "dataset": item.name,
                    "column": old.columns[name].label,
                    "meaning": old.columns[name].meaning,
                }
            )
    time_column = old.time_column if old.time_column in columns else item.profile.get("time_column")
    return old.model_copy(
        update={
            "dataset_id": item.dataset_id,
            "connection_id": item.connection_id,
            "connection_name": item.connection_name,
            "name": item.name,
            "columns": columns,
            "time_column": time_column,
        }
    )


def _restore_metrics(built: Built) -> None:
    """A table came back under the key of a removed one (the same file uploaded again): metrics that were flagged
    because their table was gone point at it again, so the flag goes. Whether they still run is tested as for every
    draft, and a metric that does not run still blocks confirming."""
    restored: dict[str, list[str]] = {}
    for name, metric in built.dictionary.metrics.items():
        if metric.needs_review and metric.dataset in built.new_datasets:
            metric.needs_review = False
            restored.setdefault(built.dictionary.datasets[metric.dataset].name, []).append(name)
    for table, names in restored.items():
        built.diff.append({"kind": "metrics_restored", "dataset": table, "metrics": names})


def _drop_references(built: Built, gone: dict[str, DatasetEntry]) -> None:
    """Nothing may point at a table that is gone (D64): its automatic metrics and its joins go; metrics the Owner
    or the AI wrote stay, flagged, so the Owner points them at another table or removes them."""
    if not gone:
        return
    dictionary = built.dictionary
    for name, metric in list(dictionary.metrics.items()):
        if metric.dataset not in gone:
            continue
        if metric.source == "code":
            del dictionary.metrics[name]
            continue
        metric.needs_review = True
        built.diff.append(
            {"kind": "metric_orphaned", "metric": name, "dataset": gone[metric.dataset].name}
        )
    kept = []
    for relationship in dictionary.relationships:
        tables = {relationship.left.partition(".")[0], relationship.right.partition(".")[0]}
        lost = tables & set(gone)
        if not lost:
            kept.append(relationship)
            continue
        built.diff.append(
            {
                "kind": "join_removed",
                "dataset": gone[sorted(lost)[0]].name,
                "from": relationship.left,
                "to": relationship.right,
            }
        )
    dictionary.relationships = kept
    for entry in dictionary.datasets.values():
        for column in entry.columns.values():
            column.joins = [j for j in column.joins if j.partition(".")[0] not in gone]


def build(
    inputs: list[DatasetInput],
    baseline: Dictionary | None,
    *,
    company_name: str,
    flag_datasets: frozenset[str] = frozenset(),
    replaced_connection_id: str | None = None,
) -> Built:
    """The dictionary for these datasets, continuing ``baseline`` (matched by dataset id) when there is one."""
    old_by_id = {
        e.dataset_id: (key, e)
        for key, e in (baseline.datasets.items() if baseline else [])
        if e.dataset_id
    }
    taken = set(baseline.datasets) if baseline else set()
    built = Built(
        dictionary=Dictionary(
            company=baseline.company
            if baseline
            else Company(name=company_name[:120] or "My business")
        )
    )
    fresh_ids = {item.dataset_id for item in inputs}
    new_inputs = [i for i in inputs if i.dataset_id not in old_by_id]
    new_keys = dataset_keys(new_inputs, taken)
    for item in inputs:
        if item.dataset_id in old_by_id:
            key, old = old_by_id[item.dataset_id]
            built.dictionary.datasets[key] = _carry_dataset(
                key, old, item, built, flag_unmatched=item.dataset_id in flag_datasets
            )
            continue
        key = new_keys[item.dataset_id]
        entry = draft_dataset(item)
        built.dictionary.datasets[key] = entry
        built.new_datasets.append(key)
        if baseline is not None:
            built.diff.append({"kind": "dataset_added", "dataset": item.name})
    for dataset_id, (_key, old) in old_by_id.items():
        if dataset_id not in fresh_ids:
            built.diff.append(
                {
                    "kind": "dataset_unmatched"
                    if replaced_connection_id and old.connection_id == replaced_connection_id
                    else "dataset_removed",
                    "dataset": old.name,
                    "columns": len(old.columns),
                }
            )
    if baseline is not None:
        built.dictionary.metrics = {k: m.model_copy(deep=True) for k, m in baseline.metrics.items()}
        built.dictionary.relationships = [r.model_copy(deep=True) for r in baseline.relationships]
        gone = {key: old for did, (key, old) in old_by_id.items() if did not in fresh_ids}
        _drop_references(built, gone)
        _restore_metrics(built)
    names = set(built.dictionary.metrics)
    for key in built.new_datasets:
        added = standard_metrics(key, built.dictionary.datasets[key], names)
        built.dictionary.metrics.update(added)
        names |= set(added)
    return built
