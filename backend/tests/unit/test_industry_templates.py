"""Industry templates (BUILD_SPEC X9; M17; D114): six templates plus a generic one, as validated data.

Nothing here calls a model. The tests check that every file is complete and consistent with the rest of the app
(the industries the workspace and the dictionary accept, the playbook files, the what-if bounds) and that each
piece reaches the place that uses it: the system prompt, the what-if defaults, the dictionary draft's hints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app import templates_industry as templates
from app.agent import prompts
from app.analytics import whatif
from app.dictionary import ai
from app.dictionary.draft import build
from app.dictionary.model import INDUSTRIES as DICTIONARY_INDUSTRIES
from app.schemas.team import OrganizationSettingsRequest
from tests.unit.test_dictionary_core import CUSTOMERS, ORDERS, item

SIX = ("retail", "d2c", "restaurant", "fuel_station", "services", "manufacturing")
PLAYBOOKS = {p.stem for p in (Path(prompts.HERE) / "playbooks").glob("*.md")}


class TestFiles:
    def test_the_industries_match_the_ones_the_rest_of_the_app_accepts(self) -> None:
        assert templates.INDUSTRIES == DICTIONARY_INDUSTRIES
        assert set(SIX) < set(templates.INDUSTRIES)
        accepted = OrganizationSettingsRequest.model_fields["industry"].annotation
        assert set(accepted.__args__[0].__args__) == set(templates.INDUSTRIES)  # type: ignore[union-attr]

    def test_every_industry_has_exactly_one_file_and_no_other_file_exists(self) -> None:
        files = {p.stem for p in Path(templates.HERE).glob("*.yaml")}
        assert files == set(templates.INDUSTRIES)

    @pytest.mark.parametrize("key", SIX)
    def test_the_six_templates_are_complete(self, key: str) -> None:
        t = templates.load(key)
        assert t.key == key and not t.generic
        assert len(t.kpis) >= 5 and len(t.starter_questions) >= 4
        assert len({k.key for k in t.kpis}) == len(t.kpis)  # no repeated KPI
        assert t.playbook_tweaks and set(t.playbook_tweaks) <= PLAYBOOKS
        assert len(t.playbook_tweaks) >= 3
        assert all(q.endswith("?") for q in t.starter_questions)

    def test_the_generic_template_is_small_and_has_no_tweaks(self) -> None:
        t = templates.load("other")
        assert t.generic and t.playbook_tweaks == {}
        assert templates.prompt_notes("other", ["why_changed"]) == ""
        assert templates.metric_hints("other") == []

    def test_an_unset_or_unknown_industry_gets_the_generic_template(self) -> None:
        assert templates.get(None).key == "other"
        assert templates.get("pet_shop").key == "other"

    def test_industries_differ_where_it_matters(self) -> None:
        questions = [tuple(templates.load(k).starter_questions) for k in SIX]
        assert len(set(questions)) == len(SIX)
        assert (
            templates.load("fuel_station").elasticities.price
            > templates.load("d2c").elasticities.price
        )

    def test_a_template_with_an_unknown_field_or_a_wrong_value_is_refused(self) -> None:
        data: dict[str, Any] = yaml.safe_load((Path(templates.HERE) / "retail.yaml").read_text())
        with pytest.raises(ValidationError):
            templates.Template.model_validate({**data, "surprise": 1})
        with pytest.raises(ValidationError):
            templates.Template.model_validate(
                {
                    **data,
                    "elasticities": {**data["elasticities"], "price": 0.5},
                }  # demand cannot rise with price
            )
        with pytest.raises(ValidationError):
            templates.Template.model_validate({**data, "starter_questions": ["only one?"]})


class TestWhatIfDefaults:
    def test_the_defaults_come_from_the_templates(self) -> None:
        for key in templates.INDUSTRIES:
            t = templates.load(key)
            assert whatif.DEFAULTS[key] == {
                "price": t.elasticities.price,
                "discount": t.elasticities.discount,
                "spend": t.elasticities.spend,
            }

    @pytest.mark.parametrize("key", SIX)
    def test_a_default_is_labelled_with_where_it_comes_from(self, key: str) -> None:
        for lever in ("price", "discount", "marketing_spend"):
            e = whatif.default_elasticity(key, lever)
            assert e.source == "default" and e.basis == templates.load(key).elasticities.basis
            assert e.low <= e.value <= e.high or e.low >= e.value >= e.high

    def test_the_basis_survives_a_reason_and_an_unknown_industry_uses_the_generic_default(
        self,
    ) -> None:
        e = whatif._with_reason(whatif.default_elasticity("retail", "price"), "too_little_history")
        assert e.basis and e.reason == "too_little_history"
        assert (
            whatif.default_elasticity("nonsense", "price").value
            == whatif.DEFAULTS["other"]["price"]
        )
        assert (
            whatif.default_elasticity(None, "discount").basis
            == templates.load("other").elasticities.basis
        )


class TestPrompt:
    def notes(self, key: str | None, playbooks: list[str]) -> str:
        return templates.prompt_notes(key, playbooks)

    def test_the_notes_have_kpis_words_and_only_the_chosen_playbooks_tweaks(self) -> None:
        text = self.notes("restaurant", ["why_changed"])
        t = templates.load("restaurant")
        assert "## What matters in a restaurant business" in text
        assert all(k.label in text for k in t.kpis)
        assert t.playbook_tweaks["why_changed"] in text
        assert (
            t.playbook_tweaks["profitability"] not in text
        )  # a playbook that was not chosen adds nothing

    def test_no_playbook_means_no_tweak_lines(self) -> None:
        assert "For the steps above" not in self.notes("retail", [])

    def test_the_notes_stay_short(self) -> None:
        for key in SIX:
            assert len(self.notes(key, ["why_changed", "profitability"])) < 1800


class TestDictionaryHints:
    def payloads(self, industry: str | None) -> list[dict[str, Any]]:
        inputs = [item("d1", "orders", ORDERS), item("d2", "customers", CUSTOMERS)]
        built = build(inputs, None, company_name="X")
        profiles = {"orders": inputs[0].profile, "customers": inputs[1].profile}
        return ai.build_payloads(built, profiles, tables_per_call=8, industry=industry)

    def test_the_draft_is_told_the_names_a_business_of_this_kind_watches(self) -> None:
        payload = self.payloads("restaurant")[0]
        assert payload["typical_metrics"] == [k.key for k in templates.load("restaurant").kpis]
        assert "typical_metrics" in ai.SYSTEM

    def test_nothing_is_added_without_an_industry_or_for_the_generic_one(self) -> None:
        assert "typical_metrics" not in self.payloads(None)[0]
        assert "typical_metrics" not in self.payloads("other")[0]

    def test_the_hints_hold_names_only_never_data(self) -> None:
        text = json.dumps(self.payloads("retail")[0]["typical_metrics"])
        assert len(text) < 400
