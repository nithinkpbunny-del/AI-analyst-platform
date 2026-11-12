"""Replacing a connection with another kind of source and keeping the dictionary (R2, DECISIONS D57, D60).

Tables of the old connection are matched to tables of the new one by their columns and names; columns are matched
by name, then by label and the synonyms the dictionary knows. Matched tables take over the old table's key, meaning,
roles, units, synonyms, metrics and joins. Whatever has no match is flagged for the Owner: new columns, columns
whose unit the new data contradicts, and old columns or tables that found no partner (listed in the draft's diff).
No AI is used: the mappings already exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.dictionary.draft import DatasetInput
from app.dictionary.model import ColumnEntry, DatasetEntry, Dictionary

MIN_SCORE = 0.34


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _old_names(entry: DatasetEntry) -> set[str]:
    names = set()
    for name, column in entry.columns.items():
        names |= {norm(name), norm(column.label)}
    return names - {""}


def _new_names(item: DatasetInput) -> set[str]:
    names = set()
    for column in item.profile["columns"]:
        names |= {norm(column["name"]), norm(column["label"])}
    return names - {""}


def score(entry: DatasetEntry, item: DatasetInput) -> float:
    old, new = _old_names(entry), _new_names(item)
    shared = len(old & new) / len(old | new) if old | new else 0.0
    return shared + (0.2 if norm(entry.name) == norm(item.name) else 0.0)


def match_datasets(
    old: dict[str, DatasetEntry], new: list[DatasetInput], *, min_score: float = MIN_SCORE
) -> dict[str, DatasetInput]:
    """One new table per old table, best scores first."""
    pairs = sorted(
        ((score(entry, item), key, item) for key, entry in old.items() for item in new),
        key=lambda p: -p[0],
    )
    matched: dict[str, DatasetInput] = {}
    used: set[str] = set()
    for value, key, item in pairs:
        if value < min_score or key in matched or item.dataset_id in used:
            continue
        matched[key] = item
        used.add(item.dataset_id)
    return matched


def match_columns(entry: DatasetEntry, item: DatasetInput) -> dict[str, str]:
    """Old column name -> new column name, for columns whose meaning carries over."""
    new = {c["name"]: c for c in item.profile["columns"]}
    by_norm: dict[str, str] = {}
    for name, column in new.items():
        by_norm.setdefault(norm(name), name)
        by_norm.setdefault(norm(column["label"]), name)
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for name in entry.columns:
        if name in new and name not in taken:
            mapping[name] = name
            taken.add(name)
    for name, column in entry.columns.items():
        if name in mapping:
            continue
        candidates = [name, column.label, *column.synonyms]
        for candidate in candidates:
            target = by_norm.get(norm(candidate))
            if target and target not in taken:
                mapping[name] = target
                taken.add(target)
                break
    return mapping


_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_]*")


def _rename_in(expr: str, renames: dict[str, str]) -> str:
    lowered = {k.lower(): v for k, v in renames.items()}

    def swap(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token[0] in "'\"" else lowered.get(token.lower(), token)

    return _TOKEN.sub(swap, expr)


@dataclass
class Rebound:
    dictionary: Dictionary
    matched: list[tuple[str, str]] = field(default_factory=list)  # (old table, new table)
    flag_datasets: set[str] = field(default_factory=set)
    unmatched: list[str] = field(default_factory=list)


def rebind(
    baseline: Dictionary,
    *,
    from_connection_id: str,
    to_inputs: list[DatasetInput],
) -> Rebound:
    """The baseline with the old connection's tables pointed at their partners in the new connection."""
    old_keys = {k for k, e in baseline.datasets.items() if e.connection_id == from_connection_id}
    return rebind_entries(baseline, old_keys, to_inputs, min_score=MIN_SCORE)


SAME_TABLE_SCORE = 0.8


def rebind_orphans(baseline: Dictionary, inputs: list[DatasetInput]) -> Rebound:
    """A table whose source is gone and that comes back under the same name with (nearly) the same columns, for
    example a file removed and uploaded again, is the same table: the Owner's work is kept, nothing is flagged."""
    live = {i.dataset_id for i in inputs}
    bound = {e.dataset_id for e in baseline.datasets.values()}
    orphans = {k for k, e in baseline.datasets.items() if e.dataset_id not in live}
    free = [i for i in inputs if i.dataset_id not in bound]
    if not orphans or not free:
        return Rebound(baseline)
    return rebind_entries(baseline, orphans, free, min_score=SAME_TABLE_SCORE)


def rebind_entries(
    baseline: Dictionary,
    old_keys: set[str],
    to_inputs: list[DatasetInput],
    *,
    min_score: float,
) -> Rebound:
    dictionary = baseline.model_copy(deep=True)
    old = {k: e for k, e in dictionary.datasets.items() if k in old_keys}
    matches = match_datasets(old, to_inputs, min_score=min_score)
    result = Rebound(dictionary)
    for key, item in matches.items():
        # A table the new connection already got automatically is replaced by the carried one.
        for other_key, other in list(dictionary.datasets.items()):
            if other.dataset_id == item.dataset_id and other_key != key:
                del dictionary.datasets[other_key]
                for metric_key, metric in list(dictionary.metrics.items()):
                    if metric.dataset == other_key and metric.source == "code":
                        del dictionary.metrics[metric_key]
        entry = dictionary.datasets[key]
        old_name = entry.name
        renames = {o: n for o, n in match_columns(entry, item).items() if o != n}
        columns: dict[str, ColumnEntry] = {}
        mapping = match_columns(entry, item)
        for name, column in entry.columns.items():
            if name in mapping:
                columns[mapping[name]] = column
        for name, column in entry.columns.items():
            if name not in mapping:
                # Kept under its old name until the build drops it and lists it in the diff.
                columns[name] = column
        entry.columns = columns
        if entry.time_column in renames:
            entry.time_column = renames[entry.time_column]
        entry.dataset_id, entry.connection_id = item.dataset_id, item.connection_id
        entry.connection_name = item.connection_name
        entry.name = item.name
        if renames:
            for metric in dictionary.metrics.values():
                if metric.dataset == key:
                    metric.expr = _rename_in(metric.expr, renames)
            for relationship in dictionary.relationships:
                for side in ("left", "right"):
                    table, _, column_name = getattr(relationship, side).partition(".")
                    if table == key and column_name in renames:
                        setattr(relationship, side, f"{table}.{renames[column_name]}")
        result.matched.append((old_name, item.name))
        result.flag_datasets.add(item.dataset_id)
    result.unmatched = [e.name for k, e in old.items() if k not in matches]
    return result
