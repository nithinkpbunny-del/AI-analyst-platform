"""The AI step of drafting a dictionary (BUILD_SPEC 9 step 3; M7).

* The model sees **only the profile**: names, types, counts, ranges and at most five sample values per column
  (none for columns that look personal). Never rows.
* One call describes up to ``dictionary_ai_tables_per_call`` tables, with no tools. The answer is JSON, parsed
  leniently: anything that does not fit (an unknown table or column, a bad role, a long text) is dropped.
* The model's units never override evidence in the data (DECISIONS D59): a unit it adds without evidence is
  marked for the Owner's review.
* The same input is never paid for twice (``input_hash``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.dictionary.draft import Built
from app.dictionary.model import INDUSTRIES, Dictionary, Metric, Relationship
from app.templates_industry import metric_hints

_SYSTEM = """You help a small Indian business set up its data dictionary.
You are given a profile of its tables: column names, types, counts, ranges and a few sample values (never full rows).
Reply with ONE JSON object and nothing else (no markdown), in this shape:
{
  "industry": one of %(industries)s,
  "tables": {
    "<table key>": {
      "meaning": "one plain sentence: what one row is",
      "grain": "one or two words, e.g. order, customer, payment",
      "time_column": "the column that dates a row, or null",
      "columns": {
        "<column name>": {
          "meaning": "short plain description for the business owner",
          "role": "key | time | measure | dimension | detail",
          "unit": "INR | percent | null",
          "synonyms": ["words an owner may use for it"]
        }
      }
    }
  },
  "metrics": {
    "<metric name in snake_case>": {
      "expr": "a DuckDB SQL aggregate over ONE table's columns, e.g. SUM(total_price) - SUM(total_refunds)",
      "table": "<table key>",
      "unit": "INR | count | percent | null",
      "good_direction": "up | down | null",
      "meaning": "short"
    }
  },
  "relationships": [{"left": "<table key>.<column>", "right": "<table key>.<column>"}]
}
Rules:
- Use only the table keys and column names you were given. Describe every column you were given.
- Money columns marked "rupees" are already converted to rupees for you; write metrics on them as they are.
- "typical_metrics", when given, are the names owners of this kind of business watch: use those names for your metrics
  where the columns exist (do not invent columns to fit them).
- A metric may use other metrics by name (e.g. revenue / orders). Write 3 to 8 metrics an owner of this kind of business
  watches (revenue, orders, average order value, refunds, ...), only where the columns exist.
- relationships: only joins you are sure about between key-like columns of different tables.
- Never invent a unit: use "INR" only where the profile says rupees or the column is clearly money.
- Keep every text under 120 characters."""
SYSTEM = _SYSTEM.replace("%(industries)s", ", ".join(INDUSTRIES))


def _column_payload(
    column: dict[str, Any], entry_unit: str | None, divide_by: int | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": column["name"],
        "type": column["type"],
        "role_guess": column["role"],
        "distinct": column["distinct"],
        "empty_pct": column["null_pct"],
    }
    if entry_unit:
        payload["unit"] = "rupees" if entry_unit == "INR" else entry_unit
    if column["min"] is not None:
        payload["range"] = [column["min"], column["max"]]
        if divide_by:
            payload["range"] = [column["min"] / divide_by, column["max"] / divide_by]
    if column["samples"]:
        payload["samples"] = column["samples"][:5]
    return payload


def build_payloads(
    built: Built,
    profiles: dict[str, dict[str, Any]],
    *,
    tables_per_call: int,
    force_all: bool = False,
    industry: str | None = None,
) -> list[dict[str, Any]]:
    """What to send, in batches. ``profiles`` are the datasets' profiles by dictionary key. Only tables and
    columns the dictionary has not described yet are included (everything on a first draft, or ``force_all``)."""
    dictionary = built.dictionary
    wanted: dict[str, set[str] | None] = {}
    for key in dictionary.datasets:
        if force_all or key in built.new_datasets:
            wanted[key] = None
    if not force_all:
        for key, column in built.new_columns:
            if key not in built.new_datasets and wanted.get(key, set()) is not None:
                wanted.setdefault(key, set()).add(column)  # type: ignore[union-attr]
    tables: list[dict[str, Any]] = []
    for key, only in wanted.items():
        profile = profiles.get(key)
        entry = dictionary.datasets[key]
        if profile is None:
            continue
        columns = [
            _column_payload(c, entry.columns[c["name"]].unit, entry.columns[c["name"]].divide_by)
            for c in profile["columns"][:80]
            if c["name"] in entry.columns and (only is None or c["name"] in only)
        ]
        tables.append(
            {
                "key": key,
                "name": entry.name,
                "rows": profile["row_count"],
                "time_column_guess": entry.time_column,
                "columns": columns,
            }
        )
    others = sorted(dictionary.datasets)
    hints = {"typical_metrics": metric_hints(industry)} if metric_hints(industry) else {}
    return [
        {"tables": tables[i : i + tables_per_call], "all_table_keys": others, **hints}
        for i in range(0, len(tables), tables_per_call)
    ]


def input_hash(payloads: list[dict[str, Any]], model_family: str) -> str:
    return hashlib.sha256(
        json.dumps([payloads, model_family], sort_keys=True, default=str).encode()
    ).hexdigest()


def parse_answer(text: str) -> dict[str, Any] | None:
    """The JSON object in the model's reply (fences and chatter around it are tolerated)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _text(value: Any, limit: int = 200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _merge_answers(answers: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"industry": None, "tables": {}, "metrics": {}, "relationships": []}
    for answer in answers:
        if merged["industry"] is None and answer.get("industry") in INDUSTRIES:
            merged["industry"] = answer["industry"]
        if isinstance(answer.get("tables"), dict):
            merged["tables"].update(answer["tables"])
        if isinstance(answer.get("metrics"), dict):
            merged["metrics"].update(answer["metrics"])
        if isinstance(answer.get("relationships"), list):
            merged["relationships"].extend(answer["relationships"])
    return merged


def apply_answers(built: Built, answers: list[dict[str, Any]]) -> dict[str, int]:
    """Merge the model's answers into the draft. Returns counts of what was used and what was dropped."""
    dictionary: Dictionary = built.dictionary
    data = _merge_answers(answers)
    used = {"columns": 0, "metrics": 0, "relationships": 0, "dropped": 0}
    if data["industry"] and dictionary.company.industry == "other":
        dictionary.company.industry = data[
            "industry"
        ]  # (an industry the Owner chose is never replaced)
    for key, table in data["tables"].items():
        entry = dictionary.datasets.get(key)
        if entry is None or not isinstance(table, dict):
            used["dropped"] += 1
            continue
        if key in built.new_datasets:
            entry.meaning = _text(table.get("meaning"), 300) or entry.meaning
            entry.grain = _text(table.get("grain"), 60)
            time_column = table.get("time_column")
            if time_column in entry.columns and entry.columns[time_column].role == "time":
                entry.time_column = time_column
        for name, answer in (table.get("columns") or {}).items():
            column = entry.columns.get(name) if isinstance(answer, dict) else None
            if column is None or column.source != "code":
                continue
            if (key, name) not in built.new_columns and key not in built.new_datasets:
                continue
            meaning = _text(answer.get("meaning"), 300)
            if meaning:
                column.meaning = meaning
            column.synonyms = [
                s for s in (_text(x, 40) for x in (answer.get("synonyms") or [])[:8]) if s
            ]
            role = answer.get("role")
            if role in ("dimension", "detail", "key") and column.role in (
                "dimension",
                "detail",
                "key",
            ):
                column.role = role
            elif role == "dimension" and column.role == "measure" and column.unit is None:
                column.role = "dimension"
            unit = answer.get("unit")
            if unit in ("INR", "percent") and column.unit is None and column.role == "measure":
                column.unit = unit
                column.needs_review, column.review_reason = True, "ai_unit"
            column.source = "ai"
            used["columns"] += 1
    taken = set(dictionary.metrics)
    for name, raw in data["metrics"].items():
        if not isinstance(raw, dict) or not isinstance(name, str):
            continue
        key = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:60]
        table = raw.get("table") if raw.get("table") in dictionary.datasets else None
        expr = _text(raw.get("expr"), 400)
        if not key or not expr or key in taken:
            used["dropped"] += 1
            continue
        unit = raw.get("unit") if raw.get("unit") in ("INR", "count", "percent") else None
        direction = (
            raw.get("good_direction") if raw.get("good_direction") in ("up", "down") else None
        )
        dictionary.metrics[key] = Metric(
            expr=expr,
            dataset=table,
            unit=unit,
            good_direction=direction,
            meaning=_text(raw.get("meaning"), 300),
            source="ai",
        )
        taken.add(key)
        used["metrics"] += 1
    for raw in data["relationships"]:
        if (
            isinstance(raw, dict)
            and isinstance(raw.get("left"), str)
            and isinstance(raw.get("right"), str)
        ):
            dictionary.relationships.append(
                Relationship(left=raw["left"][:250], right=raw["right"][:250], source="ai")
            )
            used["relationships"] += 1
    return used
