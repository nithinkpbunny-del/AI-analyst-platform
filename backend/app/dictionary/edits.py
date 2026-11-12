"""What an Owner may change in a draft (M7).

The structure comes from the data and is not editable: tables, columns, their types and where they come from.
Everything about meaning is: company, table and column descriptions, roles, units, synonyms, the time column,
metrics, and which joins to keep (joins can be removed, not invented: they are found by testing the data).
Anything the Owner touches becomes theirs (``source: owner``) and is never overwritten by a later draft.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dictionary.model import ColumnEntry, Dictionary


class EditError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EditSummary:
    columns: int
    metrics: int
    joins_removed: int


def _same(a: ColumnEntry, b: ColumnEntry) -> bool:
    return (
        a.meaning == b.meaning
        and a.role == b.role
        and a.unit == b.unit
        and a.divide_by == b.divide_by
        and a.synonyms == b.synonyms
        and a.needs_review == b.needs_review
    )


def apply(stored: Dictionary, submitted: Dictionary) -> tuple[Dictionary, EditSummary]:
    result = stored.model_copy(deep=True)
    result.company = submitted.company.model_copy()
    changed_columns = 0
    for key, entry in result.datasets.items():
        sent = submitted.datasets.get(key)
        if sent is None:
            continue
        entry.meaning = sent.meaning
        entry.grain = sent.grain
        entry.time_column = sent.time_column if sent.time_column in entry.columns else None
        for name, column in entry.columns.items():
            new = sent.columns.get(name)
            if new is None or _same(column, new):
                continue
            if new.divide_by and new.unit != "INR":
                raise EditError("divide_needs_rupees", f"{key}.{name}")
            column.meaning = new.meaning
            column.role = new.role
            column.unit = new.unit
            column.divide_by = new.divide_by
            column.synonyms = list(new.synonyms)
            if column.needs_review and not new.needs_review:
                column.review_reason = None
            column.needs_review = new.needs_review
            column.source = "owner"
            changed_columns += 1
    metrics_changed = 0
    for key, metric in submitted.metrics.items():
        before = stored.metrics.get(key)
        if before is None:
            metric = metric.model_copy(update={"source": "owner", "valid": None, "error": None})
            metrics_changed += 1
        elif before.model_dump() != metric.model_dump():
            metric = metric.model_copy(
                update={"source": "owner", "valid": None, "error": None, "needs_review": False}
            )
            metrics_changed += 1
        else:
            metric = before
        if metric.dataset is not None and metric.dataset not in result.datasets:
            raise EditError("unknown_table", metric.dataset)
        result.metrics[key] = metric
    metrics_removed = [k for k in stored.metrics if k not in submitted.metrics]
    for key in metrics_removed:
        result.metrics.pop(key, None)
    metrics_changed += len(metrics_removed)
    known = {(r.left, r.right): r for r in stored.relationships}
    kept = []
    for relationship in submitted.relationships:
        original = known.get((relationship.left, relationship.right))
        if original is None:
            raise EditError("unknown_join", f"{relationship.left} -> {relationship.right}")
        kept.append(original.model_copy(update={"needs_review": relationship.needs_review}))
    removed = len(stored.relationships) - len(kept)
    result.relationships = kept
    from app.dictionary import links

    links.link_columns(result)
    return result, EditSummary(changed_columns, metrics_changed, removed)
