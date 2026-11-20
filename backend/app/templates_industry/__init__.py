"""Industry templates (BUILD_SPEC X9, 9 step 2, 10.4; M17; DECISIONS D114).

A template is plain data in a YAML file next to this module: what to watch (KPIs), how the standard playbooks
change for this kind of business, questions to start with, the business's own words and the what-if defaults
(price, discount and marketing-spend elasticities, always shown as assumptions). No AI call is made here, and
nothing in a template touches a customer's data: it only shapes the prompt, the suggestions and the defaults.

``INDUSTRIES`` is the list the workspace's ``industry_template`` may hold (the same words as the dictionary's).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

HERE = Path(__file__).resolve().parent
INDUSTRIES = ("retail", "d2c", "restaurant", "fuel_station", "services", "manufacturing", "other")
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Unit = Literal["INR", "count", "percent", "litres", "days", "ratio"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Kpi(_Model):
    key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")]
    label: Short
    meaning: Text
    unit: Unit
    good_direction: Literal["up", "down"] | None = None


class Elasticities(_Model):
    """Units per 1% of list price (``price``), per 1% of the net price paid (``discount``) and revenue per 1% of
    marketing spend (``spend``). ``basis`` says where the numbers come from; it is shown with the assumption."""

    price: Annotated[float, Field(ge=-6.0, le=-0.05)]
    discount: Annotated[float, Field(ge=-6.0, le=-0.05)]
    spend: Annotated[float, Field(gt=0.0, le=1.5)]
    basis: Text


class Template(_Model):
    key: Literal[
        "retail", "d2c", "restaurant", "fuel_station", "services", "manufacturing", "other"
    ]
    name: Short
    # What the prompts call the business: "a {business} business in India".
    business: Short
    summary: Text
    words: Text
    kpis: Annotated[list[Kpi], Field(min_length=3, max_length=8)]
    starter_questions: Annotated[list[Short], Field(min_length=3, max_length=5)]
    # A playbook's file name (``agent/playbooks/<name>.md``) -> what to add for this kind of business.
    playbook_tweaks: dict[str, Text] = Field(default_factory=dict)
    elasticities: Elasticities

    @property
    def generic(self) -> bool:
        return self.key == "other"


@cache
def load(key: str) -> Template:
    path = HERE / f"{key}.yaml"
    return Template.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def get(key: str | None) -> Template:
    """The template for a workspace's industry; an unset or unknown one gets the generic template."""
    return load(key if key in INDUSTRIES else "other")


def all_templates() -> list[Template]:
    return [load(key) for key in INDUSTRIES]


def prompt_notes(key: str | None, playbooks: list[str]) -> str:
    """The industry section of the system prompt: what to watch and how the chosen playbooks change. Empty for an
    unset or generic industry (a short prompt is cheaper; the generic template has nothing to add)."""
    template = get(key)
    if template.generic or key is None:
        return ""
    lines = [
        f"## What matters in a {template.name.lower()} business",
        "KPIs to look at first: "
        + "; ".join(
            f"{k.label} ({k.meaning[0].lower()}{k.meaning[1:].rstrip('.')})" for k in template.kpis
        )
        + ".",
        f"Words to use: {template.words}",
    ]
    for name in playbooks:
        tweak = template.playbook_tweaks.get(name)
        if tweak:
            lines.append(f"For the steps above ({name.replace('_', ' ')}): {tweak}")
    return "\n".join(lines)


def metric_hints(key: str | None) -> list[str]:
    """Names of the metrics this kind of business usually watches, for the dictionary draft (BUILD_SPEC 9 step 2)."""
    template = get(key)
    return [] if template.generic else [k.key for k in template.kpis]
