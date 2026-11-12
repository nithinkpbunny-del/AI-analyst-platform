"""Finding and testing joins between tables (BUILD_SPEC 9 steps 3-4; M7).

A join is only kept when it works on the data: the share of the left column's distinct values that exist in the
right column (the match rate) is measured on the stored rows, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from app.dictionary.model import Dictionary, Relationship

KEEP_AT = 0.8
REVIEW_AT = 0.3
MAX_VALUES = 300_000


@dataclass(frozen=True)
class KeySet:
    values: frozenset[str]
    unique_ratio: float  # distinct / non-empty rows


def collect(frame: pd.DataFrame, column: str) -> KeySet | None:
    if column not in frame:
        return None
    present = frame[column].dropna().astype("string").str.strip()
    present = present[present != ""]
    if present.empty:
        return None
    distinct = present.drop_duplicates()
    if len(distinct) > MAX_VALUES:
        distinct = distinct.head(MAX_VALUES)
    return KeySet(frozenset(distinct.tolist()), len(present.drop_duplicates()) / len(present))


KeySets = dict[tuple[str, str], KeySet]


def match_rate(left: KeySet, right: KeySet) -> float:
    return len(left.values & right.values) / len(left.values)


def _split(reference: str) -> tuple[str, str] | None:
    dataset, _, column = reference.partition(".")
    return (dataset, column) if dataset and column else None


def testable(relationship: Relationship, keysets: KeySets) -> bool:
    """Whether the stored rows allow testing this join at all."""
    left, right = _split(relationship.left), _split(relationship.right)
    return left is not None and right is not None and left in keysets and right in keysets


def evaluate(relationship: Relationship, keysets: KeySets) -> Relationship | None:
    """The relationship with its measured match rate and type; ``None`` when it does not hold."""
    left, right = _split(relationship.left), _split(relationship.right)
    if left is None or right is None or left == right or left[0] == right[0]:
        return None
    a, b = keysets.get(left), keysets.get(right)
    if a is None or b is None:
        return None
    rate = round(match_rate(a, b), 3)
    if rate < REVIEW_AT:
        return None
    if b.unique_ratio >= 0.99:
        kind = "one_to_one" if a.unique_ratio >= 0.99 else "many_to_one"
    else:
        kind = "many_to_many"
    return relationship.model_copy(
        update={"match_rate": rate, "type": kind, "needs_review": rate < KEEP_AT}
    )


def _stem(column: str) -> str | None:
    found = re.match(r"^(.+?)_?id$", column, re.IGNORECASE)
    return found.group(1).lower() if found and found.group(1) else None


def _names(dataset_key: str, dataset_name: str) -> str:
    return f"{dataset_key} {dataset_name}".lower()


def discover(dictionary: Dictionary, keysets: KeySets) -> list[Relationship]:
    """Joins the names suggest (``customer_id`` -> a customers table's ``id``, or the same key name in two tables)."""
    found: list[Relationship] = []
    seen: set[frozenset[str]] = set()
    for a_key, a in dictionary.datasets.items():
        for a_col, column in a.columns.items():
            if column.role != "key" or (a_key, a_col) not in keysets:
                continue
            stem = _stem(a_col)
            for b_key, b in dictionary.datasets.items():
                if b_key == a_key:
                    continue
                targets: list[str] = []
                if stem and "id" in b.columns and b.columns["id"].role == "key":
                    names = _names(b_key, b.name)
                    if stem in names or stem.rstrip("s") in names:
                        targets.append("id")
                if a_col in b.columns and b.columns[a_col].role == "key" and a_col != "id":
                    targets.append(a_col)
                for target in targets:
                    pair = frozenset({f"{a_key}.{a_col}", f"{b_key}.{target}"})
                    if pair in seen:
                        continue
                    seen.add(pair)
                    candidate = Relationship(left=f"{a_key}.{a_col}", right=f"{b_key}.{target}")
                    checked = evaluate(candidate, keysets)
                    if checked is None:
                        # The other way round may hold (the key column is on the other side).
                        flipped = Relationship(left=candidate.right, right=candidate.left)
                        checked = evaluate(flipped, keysets)
                    if checked is not None:
                        found.append(checked)
    return found


def merge(existing: list[Relationship], additions: list[Relationship]) -> list[Relationship]:
    """``additions`` that are not already known (either direction); known joins keep what the Owner decided."""
    known = {frozenset({r.left, r.right}) for r in existing}
    merged = list(existing)
    for relationship in additions:
        pair = frozenset({relationship.left, relationship.right})
        if pair not in known:
            known.add(pair)
            merged.append(relationship)
    return merged


def link_columns(dictionary: Dictionary) -> None:
    """Write each join onto the left column's ``joins`` list (the BUILD_SPEC 9 shape)."""
    for entry in dictionary.datasets.values():
        for column in entry.columns.values():
            column.joins = []
    for relationship in dictionary.relationships:
        left = _split(relationship.left)
        if left is None:
            continue
        owner = dictionary.datasets.get(left[0])
        target = owner.columns.get(left[1]) if owner else None
        if target is not None and relationship.right not in target.joins:
            target.joins.append(relationship.right)
