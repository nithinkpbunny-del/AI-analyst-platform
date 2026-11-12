"""The dictionary document (BUILD_SPEC 9) as validated data.

Stored as JSON in ``dictionary_versions.content``, edited in the web app and exportable as YAML. Every string the
Owner can edit has a length limit; ``review_reason`` values are codes (the web app writes the sentences, D41).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Role = Literal["key", "time", "measure", "dimension", "detail"]
Unit = Literal["INR", "percent"]
Source = Literal["code", "ai", "owner", "carried"]
Direction = Literal["up", "down"]
RelationType = Literal["many_to_one", "one_to_one", "many_to_many"]
INDUSTRIES = ("retail", "d2c", "restaurant", "fuel_station", "services", "manufacturing", "other")

# Why an item waits for the Owner (shown as a sentence in the web app).
REVIEW_REASONS = (
    "paise",  # looks like paise: divide by 100 to get rupees
    "paise_guess",  # an integer "amount" next to an INR currency column
    "money_name_only",  # named like money, but nothing in the data says which currency
    "other_currency",  # a single currency that is not rupees
    "ai_unit",  # the AI suggested a unit the data does not prove
    "type_changed",  # the column's type changed in the source
    "no_match",  # (replacement) the old column has no match in the new source
    "new_column",  # (replacement, schema change) a column the dictionary has not seen
    "unit_differs",  # (replacement) the new source suggests another unit than the old mapping
    "join_weak",  # a join where only some values match
    "no_time_column",
)

Short = Annotated[str, StringConstraints(max_length=300)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Tag = Annotated[str, StringConstraints(min_length=1, max_length=40)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Company(_Model):
    name: Name = "My business"
    industry: Literal[
        "retail", "d2c", "restaurant", "fuel_station", "services", "manufacturing", "other"
    ] = "other"
    currency: Literal["INR"] = "INR"
    fiscal_year_start: Annotated[
        str, StringConstraints(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    ] = "04-01"


class ColumnEntry(_Model):
    label: Name
    type: Annotated[str, StringConstraints(max_length=20)]
    meaning: Short = ""
    role: Role
    unit: Unit | None = None
    # The stored number divided by this gives the unit (Razorpay's paise: 100 gives rupees).
    divide_by: Literal[100] | None = None
    synonyms: list[Tag] = Field(default_factory=list, max_length=12)
    joins: list[Annotated[str, StringConstraints(max_length=250)]] = Field(
        default_factory=list, max_length=12
    )
    evidence: list[Tag] = Field(default_factory=list, max_length=12)
    needs_review: bool = False
    review_reason: Annotated[str, StringConstraints(max_length=40)] | None = None
    source: Source = "code"

    @field_validator("divide_by")
    @classmethod
    def _only_with_a_unit(cls, value: int | None) -> int | None:
        return value


class DatasetEntry(_Model):
    dataset_id: str | None = None
    connection_id: str | None = None
    connection_name: Annotated[str, StringConstraints(max_length=120)] = ""
    name: Name
    meaning: Short = ""
    grain: Annotated[str, StringConstraints(max_length=60)] = ""
    time_column: str | None = None
    columns: dict[str, ColumnEntry] = Field(default_factory=dict)


class Metric(_Model):
    expr: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    dataset: str | None = None
    unit: Unit | Literal["count"] | None = None
    good_direction: Direction | None = None
    meaning: Short = ""
    source: Source = "code"
    # Set by validation (DuckDB on a sample): None = not checked yet.
    valid: bool | None = None
    error: Annotated[str, StringConstraints(max_length=300)] | None = None
    needs_review: bool = False


class Relationship(_Model):
    left: Annotated[str, StringConstraints(min_length=3, max_length=250)]
    right: Annotated[str, StringConstraints(min_length=3, max_length=250)]
    type: RelationType = "many_to_one"
    match_rate: float | None = Field(default=None, ge=0, le=1)
    source: Source = "code"
    needs_review: bool = False


class Dictionary(_Model):
    company: Company = Field(default_factory=Company)
    datasets: dict[Name, DatasetEntry] = Field(default_factory=dict, max_length=200)
    metrics: dict[Name, Metric] = Field(default_factory=dict, max_length=200)
    relationships: list[Relationship] = Field(default_factory=list, max_length=200)

    def review_items(self) -> int:
        """How many things still wait for the Owner (columns, metrics, joins)."""
        count = sum(c.needs_review for d in self.datasets.values() for c in d.columns.values())
        count += sum(m.needs_review or m.valid is False for m in self.metrics.values())
        count += sum(r.needs_review for r in self.relationships)
        return count


_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """A short, stable key for a name: ``Shopify Orders (2026).csv`` -> ``shopify_orders_2026_csv``."""
    return _SLUG.sub("_", text.lower()).strip("_")[:60] or "data"


def parse(content: dict[str, Any]) -> Dictionary:
    return Dictionary.model_validate(content)


def dump(dictionary: Dictionary) -> dict[str, Any]:
    return dictionary.model_dump(mode="json")


def to_yaml(dictionary: Dictionary) -> str:
    """The dictionary in the BUILD_SPEC 9 shape, for people to read and keep."""
    data = dump(dictionary)

    def column(c: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"meaning": c["meaning"], "role": c["role"]}
        if c["unit"]:
            out["unit"] = c["unit"]
        if c["divide_by"]:
            out["divide_by"] = c["divide_by"]
        if c["synonyms"]:
            out["synonyms"] = c["synonyms"]
        if c["joins"]:
            out["joins"] = c["joins"]
        return out

    document: dict[str, Any] = {
        "company": data["company"],
        "datasets": {
            key: {
                "meaning": d["meaning"],
                **({"time_column": d["time_column"]} if d["time_column"] else {}),
                **({"grain": d["grain"]} if d["grain"] else {}),
                "columns": {name: column(c) for name, c in d["columns"].items()},
            }
            for key, d in data["datasets"].items()
        },
        "metrics": {
            key: {
                k: v
                for k, v in {
                    "expr": m["expr"],
                    "dataset": m["dataset"],
                    "unit": m["unit"],
                    "good_direction": m["good_direction"],
                    "meaning": m["meaning"] or None,
                }.items()
                if v
            }
            for key, m in data["metrics"].items()
        },
        "relationships": [
            {"left": r["left"], "right": r["right"], "type": r["type"]}
            for r in data["relationships"]
        ],
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
