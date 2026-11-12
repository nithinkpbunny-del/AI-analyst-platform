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
from typing import Any

from app.dictionary.draft import DatasetInput
from app.dictionary.model import ColumnEntry, DatasetEntry, Dictionary, slug

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


def _as_input(entry: DatasetEntry) -> DatasetInput:
    columns = [{"name": name, "label": c.label} for name, c in entry.columns.items()]
    return DatasetInput(
        entry.dataset_id or "",
        entry.connection_id or "",
        entry.connection_name,
        entry.name,
        {"columns": columns},
    )


@dataclass
class TakeOver:
    dictionary: Dictionary
    # One diff item per table that took over a removed table's metrics and joins (kind ``metrics_moved``).
    moves: list[dict[str, Any]] = field(default_factory=list)


def take_over_orphans(baseline: Dictionary, live: set[str]) -> TakeOver:
    """Two sources had a table of the same name: the second got a prefixed key (``draft.dataset_keys``). When the
    first source goes, the remaining table with the same name and (nearly) the same columns takes over the removed
    table's metrics and joins, under the plain key, so nothing points at a table that no longer exists (D64).

    ``live`` holds the dataset ids that still exist. Tables with no such partner are left alone; ``draft.build``
    drops them, removes their automatic metrics and joins, and flags the metrics the Owner or the AI wrote."""
    dictionary = baseline.model_copy(deep=True)
    result = TakeOver(dictionary)
    taken: set[str] = set()
    for orphan_key in [k for k, e in dictionary.datasets.items() if e.dataset_id not in live]:
        orphan = dictionary.datasets[orphan_key]
        candidates = sorted(
            (
                (score(orphan, _as_input(entry)), key)
                for key, entry in dictionary.datasets.items()
                if entry.dataset_id in live
                and key not in taken
                and norm(entry.name) == norm(orphan.name)
            ),
            key=lambda c: -c[0],
        )
        if not candidates or candidates[0][0] < SAME_TABLE_SCORE:
            continue
        survivor_key = candidates[0][1]
        survivor = dictionary.datasets[survivor_key]
        renames = {o: n for o, n in match_columns(orphan, _as_input(survivor)).items() if o != n}
        _carry_descriptions(orphan, survivor, match_columns(orphan, _as_input(survivor)))
        del dictionary.datasets[orphan_key]
        plain = slug(survivor.name)
        final = plain if plain not in dictionary.datasets or plain == survivor_key else survivor_key
        moved = _move_references(dictionary, orphan_key, survivor_key, final, renames)
        dictionary.datasets = {
            (final if k == survivor_key else k): e for k, e in dictionary.datasets.items()
        }
        taken.add(final)
        if not moved:
            continue
        result.moves.append(
            {
                "kind": "metrics_moved",
                "dataset": survivor.name,
                "from": orphan.connection_name,
                "to": survivor.connection_name,
                "metrics": moved,
            }
        )
    return result


def _carry_descriptions(
    orphan: DatasetEntry, survivor: DatasetEntry, mapping: dict[str, str]
) -> None:
    """Words the Owner or the AI wrote for the removed table are kept where the remaining table only has the code
    draft's. Units and roles stay as the remaining table's data shows them."""
    if orphan.meaning and survivor.meaning == f"Rows of {survivor.name}":
        survivor.meaning, survivor.grain = orphan.meaning, survivor.grain or orphan.grain
    for old_name, new_name in mapping.items():
        old, new = orphan.columns[old_name], survivor.columns.get(new_name)
        if new is None or new.source != "code" or old.source == "code":
            continue
        new.meaning = old.meaning
        new.synonyms = list(old.synonyms)
        new.source = "carried"


def _move_references(
    dictionary: Dictionary,
    orphan_key: str,
    survivor_key: str,
    final: str,
    renames: dict[str, str],
) -> list[str]:
    """Point metrics and joins of both tables at ``final``. Returns the metrics that moved from the removed table."""
    moved: list[str] = []
    for name, metric in dictionary.metrics.items():
        if metric.dataset == orphan_key:
            metric.expr = _rename_in(metric.expr, renames) if renames else metric.expr
            metric.dataset = final
            moved.append(name)
        elif metric.dataset == survivor_key:
            metric.dataset = final
    # Two automatic metrics that now say the same thing (two COUNT(*) of one table): the shorter name stays.
    by_expr: dict[str, list[str]] = {}
    for name, metric in dictionary.metrics.items():
        if metric.dataset == final and metric.source == "code":
            by_expr.setdefault(norm(metric.expr), []).append(name)
    dropped: dict[str, str] = {}
    for names in by_expr.values():
        keep = min(names, key=lambda n: (len(n), n))
        for name in names:
            if name != keep:
                dropped[name] = keep
                del dictionary.metrics[name]
    if dropped:
        for metric in dictionary.metrics.values():
            metric.expr = _rename_in(metric.expr, dropped)
    moved = [m for m in moved if m not in dropped]
    relationships = []
    seen: set[tuple[str, str]] = set()
    for relationship in dictionary.relationships:
        for side in ("left", "right"):
            table, _, column_name = getattr(relationship, side).partition(".")
            if table == orphan_key:
                setattr(relationship, side, f"{final}.{renames.get(column_name, column_name)}")
            elif table == survivor_key:
                setattr(relationship, side, f"{final}.{column_name}")
        pair = (relationship.left, relationship.right)
        same_table = relationship.left.partition(".")[0] == relationship.right.partition(".")[0]
        if pair in seen or same_table:
            continue
        seen.add(pair)
        relationships.append(relationship)
    dictionary.relationships = relationships
    for entry in dictionary.datasets.values():
        for column in entry.columns.values():
            column.joins = [
                _rekey_join(j, orphan_key, survivor_key, final, renames) for j in column.joins
            ]
    return sorted(moved)


def _rekey_join(
    target: str, orphan_key: str, survivor_key: str, final: str, renames: dict[str, str]
) -> str:
    table, _, column_name = target.partition(".")
    if table == orphan_key:
        return f"{final}.{renames.get(column_name, column_name)}"
    if table == survivor_key:
        return f"{final}.{column_name}"
    return target


def removal_impact(dictionary: Dictionary, connection_id: str) -> dict[str, Any]:
    """What removing a connection would do to the dictionary, for the warning before removing it (D64).

    ``moves``: tables whose metrics another source's same-named table takes over. ``orphaned``: metrics the Owner
    or the AI wrote that would then point at nothing and wait for the Owner. ``joins``: joins that would go."""
    live = {
        e.dataset_id
        for e in dictionary.datasets.values()
        if e.dataset_id and e.connection_id != connection_id
    }
    over = take_over_orphans(dictionary, live)
    gone = {k for k, e in over.dictionary.datasets.items() if e.dataset_id not in live}
    orphaned = sorted(
        name
        for name, m in over.dictionary.metrics.items()
        if m.dataset in gone and m.source != "code"
    )
    joins = sum(
        r.left.partition(".")[0] in gone or r.right.partition(".")[0] in gone
        for r in over.dictionary.relationships
    )
    moves = [
        {"table": m["dataset"], "to": m["to"], "metrics": len(m["metrics"])} for m in over.moves
    ]
    return {"moves": moves, "orphaned_metrics": orphaned, "joins": joins}


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
