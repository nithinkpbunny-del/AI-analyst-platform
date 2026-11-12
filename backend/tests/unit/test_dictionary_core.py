"""Drafting in code, joins, metric checks, units, the AI step and replacing a source (R2, R13)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest
import yaml

from app.dictionary import ai, links, replace, validate
from app.dictionary.draft import DatasetInput, build, dataset_keys
from app.dictionary.model import Dictionary, Metric, Relationship, parse, slug, to_yaml
from app.dictionary.profiler import profile_frame
from app.dictionary.units import to_units
from tests.unit.test_dictionary_profiler import infos

ORDERS = pd.DataFrame(
    {
        "id": [1, 2, 3, 4],
        "created_at": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
        "customer_id": [10, 11, 10, 12],
        "total_price": [100.0, 250.0, 80.0, 70.0],
        "total_refunds": [0.0, 50.0, 0.0, 0.0],
        "currency": ["INR"] * 4,
    }
)
CUSTOMERS = pd.DataFrame({"id": [10, 11, 12], "city": ["Pune", "Delhi", "Pune"]})
PAYMENTS = pd.DataFrame(
    {
        "id": ["p1", "p2"],
        "entity": ["payment"] * 2,
        "amount": [10000, 25000],
        "currency": ["INR"] * 2,
        "status": ["captured", "captured"],
        "method": ["upi", "card"],
        "fee": [200, 500],
        "tax": [36, 90],
    }
)


def item(dataset_id: str, name: str, frame: pd.DataFrame, connection: str = "c1") -> DatasetInput:
    return DatasetInput(
        dataset_id, connection, f"{name} file", name, profile_frame(frame, infos(frame), name=name)
    )


@pytest.fixture
def inputs() -> list[DatasetInput]:
    return [item("d1", "orders", ORDERS), item("d2", "customers", CUSTOMERS)]


def keysets(*pairs: tuple[str, str, pd.DataFrame]) -> links.KeySets:
    out: links.KeySets = {}
    for key, column, frame in pairs:
        found = links.collect(frame, column)
        assert found is not None
        out[(key, column)] = found
    return out


class TestDraftInCode:
    def test_roles_units_time_column_and_standard_metrics(self, inputs: list[DatasetInput]) -> None:
        built = build(inputs, None, company_name="UrbanCart")
        orders = built.dictionary.datasets["orders"]
        assert orders.time_column == "created_at"
        assert orders.columns["id"].role == "key"
        assert orders.columns["created_at"].role == "time"
        price = orders.columns["total_price"]
        assert (price.role, price.unit, price.divide_by) == ("measure", "INR", None)
        assert price.needs_review is False
        assert price.source == "code"
        assert built.dictionary.company.name == "UrbanCart"
        metrics = built.dictionary.metrics
        assert metrics["orders_count"].expr == "COUNT(*)"
        assert metrics["sum_total_price"].expr == "SUM(total_price)"
        assert built.new_datasets == ["orders", "customers"]

    def test_paise_columns_are_flagged_for_the_owner(self) -> None:
        built = build([item("d3", "payments", PAYMENTS)], None, company_name="X")
        amount = built.dictionary.datasets["payments"].columns["amount"]
        assert (amount.unit, amount.divide_by) == ("INR", 100)
        assert (amount.needs_review, amount.review_reason) == (True, "paise")
        assert built.dictionary.review_items() == 3  # amount, fee and tax

    def test_keys_stay_unique_across_connections(self) -> None:
        same = [item("a", "orders", ORDERS, "c1"), item("b", "orders", ORDERS, "c2")]
        keys = dataset_keys(same)
        assert len(set(keys.values())) == 2
        assert keys["a"].endswith("orders")

    def test_a_new_structure_keeps_what_was_settled_and_drafts_what_is_new(
        self, inputs: list[DatasetInput]
    ) -> None:
        first = build(inputs, None, company_name="X").dictionary
        first.datasets["orders"].columns["total_price"].meaning = "Order value incl. tax"
        first.datasets["orders"].columns["total_price"].source = "owner"
        first.datasets["orders"].columns["id"].meaning = "Order number"
        changed = ORDERS.drop(columns=["total_refunds"]).assign(coupon=["a", "b", "a", "b"])
        second = build(
            [item("d1", "orders", changed), item("d2", "customers", CUSTOMERS)],
            first,
            company_name="X",
        )
        orders = second.dictionary.datasets["orders"]
        assert orders.columns["total_price"].meaning == "Order value incl. tax"
        assert orders.columns["id"].meaning == "Order number"
        assert "coupon" in orders.columns and "total_refunds" not in orders.columns
        assert second.new_columns == [("orders", "coupon")]
        assert second.new_datasets == []
        kinds = {(d["kind"], d.get("column")) for d in second.diff}
        assert kinds == {("column_added", "coupon"), ("column_removed", "total_refunds")}

    def test_a_changed_type_is_flagged_and_listed(self, inputs: list[DatasetInput]) -> None:
        first = build(inputs, None, company_name="X").dictionary
        as_text = ORDERS.assign(total_price=["a", "b", "c", "d"])
        second = build(
            [item("d1", "orders", as_text), item("d2", "customers", CUSTOMERS)],
            first,
            company_name="X",
        )
        price = second.dictionary.datasets["orders"].columns["total_price"]
        assert (price.needs_review, price.review_reason) == (True, "type_changed")
        assert any(d["kind"] == "type_changed" for d in second.diff)

    def test_a_table_that_disappeared_is_dropped_and_listed(
        self, inputs: list[DatasetInput]
    ) -> None:
        first = build(inputs, None, company_name="X").dictionary
        second = build([inputs[0]], first, company_name="X")
        assert "customers" not in second.dictionary.datasets
        assert {"kind": "dataset_removed", "dataset": "customers", "columns": 2} in second.diff

    def test_owner_edits_survive_but_unedited_code_units_follow_the_evidence(
        self, inputs: list[DatasetInput]
    ) -> None:
        first = build(inputs, None, company_name="X").dictionary
        first.datasets["orders"].columns["total_refunds"].unit = None  # code's own, not an edit
        second = build(inputs, first, company_name="X")
        assert second.dictionary.datasets["orders"].columns["total_refunds"].unit == "INR"
        first.datasets["orders"].columns["total_refunds"].unit = None
        first.datasets["orders"].columns["total_refunds"].source = "owner"
        third = build(inputs, first, company_name="X")
        assert third.dictionary.datasets["orders"].columns["total_refunds"].unit is None


class TestSameNamedTables:
    """Two sources with a table of the same name; the first one is removed (D64, the M7 bug)."""

    def two_sources(self) -> Dictionary:
        first = build(
            [item("d1", "orders", ORDERS, "c1"), item("d2", "customers", CUSTOMERS, "c1")],
            None,
            company_name="X",
        ).dictionary
        first.metrics["revenue"] = Metric(
            expr="SUM(total_price) - SUM(total_refunds)", dataset="orders", source="owner"
        )
        first.datasets["orders"].columns["total_price"].meaning = "Order value incl. tax"
        first.datasets["orders"].columns["total_price"].source = "owner"
        first.relationships.append(
            Relationship(left="orders.customer_id", right="customers.id", match_rate=1.0)
        )
        sheet = item("d3", "orders", ORDERS, "c2")
        both = build(
            [item("d1", "orders", ORDERS, "c1"), item("d2", "customers", CUSTOMERS, "c1"), sheet],
            first,
            company_name="X",
        ).dictionary
        assert both.datasets["orders_file_orders"].dataset_id == "d3"  # the second got a prefix
        return both

    def test_the_remaining_table_takes_over_the_metrics_under_the_plain_name(self) -> None:
        both = self.two_sources()
        over = replace.take_over_orphans(both, live={"d2", "d3"})
        rest = [item("d2", "customers", CUSTOMERS, "c1"), item("d3", "orders", ORDERS, "c2")]
        built = build(rest, over.dictionary, company_name="X")
        dictionary = built.dictionary
        assert set(dictionary.datasets) == {"orders", "customers"}
        assert dictionary.datasets["orders"].dataset_id == "d3"
        assert dictionary.metrics["revenue"].dataset == "orders"
        counts = [k for k, m in dictionary.metrics.items() if m.expr == "COUNT(*)"]
        assert sorted(counts) == ["customers_count", "orders_count"]  # no second COUNT(*) of orders
        assert all(m.dataset in dictionary.datasets for m in dictionary.metrics.values())
        assert [(r.left, r.right) for r in dictionary.relationships] == [
            ("orders.customer_id", "customers.id")
        ]
        # What the Owner wrote about the removed table is kept on the remaining one.
        price = dictionary.datasets["orders"].columns["total_price"]
        assert price.meaning == "Order value incl. tax"
        assert over.moves == [
            {
                "kind": "metrics_moved",
                "dataset": "orders",
                "from": "orders file",
                "to": "orders file",
                "metrics": ["orders_count", "revenue", "sum_total_price", "sum_total_refunds"],
            }
        ]
        samples = {"orders": to_units(ORDERS, dictionary.datasets["orders"])}
        samples["customers"] = CUSTOMERS
        validate.check_metrics(dictionary, samples)
        assert all(m.valid for m in dictionary.metrics.values())
        assert dictionary.review_items() == 0

    def test_a_table_with_other_columns_is_not_taken_for_the_same_table(self) -> None:
        both = self.two_sources()
        both.datasets["orders_file_orders"].columns = {
            k: v for k, v in both.datasets["orders_file_orders"].columns.items() if k == "id"
        }
        over = replace.take_over_orphans(both, live={"d2", "d3"})
        assert over.moves == [] and "orders" in over.dictionary.datasets

    def test_with_no_partner_nothing_points_at_the_gone_table(self) -> None:
        both = self.two_sources()
        rest = [item("d2", "customers", CUSTOMERS, "c1")]
        built = build(rest, both, company_name="X")
        dictionary = built.dictionary
        assert set(dictionary.datasets) == {"customers"}
        # Automatic metrics of the gone tables go; the Owner's metric stays and waits for the Owner.
        assert set(dictionary.metrics) == {"customers_count", "revenue"}
        assert dictionary.metrics["revenue"].needs_review is True
        assert dictionary.relationships == []
        assert {"kind": "metric_orphaned", "metric": "revenue", "dataset": "orders"} in built.diff
        assert {
            "kind": "join_removed",
            "dataset": "orders",
            "from": "orders.customer_id",
            "to": "customers.id",
        } in built.diff
        assert all(
            j.partition(".")[0] in dictionary.datasets
            for e in dictionary.datasets.values()
            for c in e.columns.values()
            for j in c.joins
        )

    def test_the_warning_before_removal(self) -> None:
        both = self.two_sources()
        impact = replace.removal_impact(both, "c1")
        assert impact == {
            "moves": [{"table": "orders", "to": "orders file", "metrics": 4}],
            "orphaned_metrics": [],
            "joins": 1,  # orders -> customers goes with the customers table
        }
        only = replace.removal_impact(both, "c2")
        assert only == {"moves": [], "orphaned_metrics": [], "joins": 0}


class TestJoins:
    def test_customer_id_joins_the_customers_table_and_is_measured(
        self, inputs: list[DatasetInput]
    ) -> None:
        dictionary = build(inputs, None, company_name="X").dictionary
        sets = keysets(
            ("orders", "id", ORDERS),
            ("orders", "customer_id", ORDERS),
            ("customers", "id", CUSTOMERS),
        )
        found = links.discover(dictionary, sets)
        assert [(r.left, r.right, r.type, r.match_rate) for r in found] == [
            ("orders.customer_id", "customers.id", "many_to_one", 1.0)
        ]
        dictionary.relationships = found
        links.link_columns(dictionary)
        assert dictionary.datasets["orders"].columns["customer_id"].joins == ["customers.id"]

    def test_partial_matches_wait_for_the_owner_and_poor_ones_are_dropped(self) -> None:
        left = pd.DataFrame({"k": [str(i) for i in range(10)]})
        some = pd.DataFrame({"k": [str(i) for i in range(5)] + ["x1", "x2", "x3", "x4", "x5"]})
        none = pd.DataFrame({"k": [f"z{i}" for i in range(10)]})
        a, b = links.collect(left, "k"), links.collect(some, "k")
        c = links.collect(none, "k")
        assert a and b and c
        from app.dictionary.model import Relationship

        weak = links.evaluate(Relationship(left="a.k", right="b.k"), {("a", "k"): a, ("b", "k"): b})
        assert weak is not None
        assert weak.match_rate == 0.5 and weak.needs_review is True
        assert (
            links.evaluate(Relationship(left="a.k", right="c.k"), {("a", "k"): a, ("c", "k"): c})
            is None
        )

    def test_a_join_that_cannot_be_tested_is_not_invented(self) -> None:
        from app.dictionary.model import Relationship

        assert links.evaluate(Relationship(left="a.k", right="b.k"), {}) is None


class TestMetrics:
    def dictionary(self) -> Dictionary:
        d = build([item("d1", "orders", ORDERS)], None, company_name="X").dictionary
        d.metrics.clear()
        return d

    def samples(self) -> dict[str, pd.DataFrame]:
        return {"orders": ORDERS}

    def check(self, d: Dictionary) -> dict[str, tuple[bool | None, str | None]]:
        validate.check_metrics(d, self.samples())
        return {k: (m.valid, m.error) for k, m in d.metrics.items()}

    def test_expressions_run_on_the_sample(self) -> None:
        d = self.dictionary()
        d.metrics["revenue"] = Metric(
            expr="SUM(total_price) - SUM(total_refunds)", dataset="orders"
        )
        d.metrics["orders"] = Metric(expr="COUNT(*)", dataset="orders")
        d.metrics["aov"] = Metric(expr="revenue / orders")
        assert self.check(d) == {
            "revenue": (True, None),
            "orders": (True, None),
            "aov": (True, None),
        }

    def test_a_metric_can_use_another_metric_and_gets_its_table(self) -> None:
        d = self.dictionary()
        d.metrics["revenue"] = Metric(expr="SUM(total_price)", dataset="orders")
        d.metrics["double"] = Metric(expr="revenue * 2")
        sql, table = validate.expand("double", d)
        assert sql == "(SUM(total_price)) * 2" and table == "orders"

    @pytest.mark.parametrize(
        ("expr", "code"),
        [
            ("SUM(total_prce)", "unknown_column"),
            ("SUM(total_price", "syntax"),
            ("total_price", "not_a_total"),
            ("SUM(total_price); DROP TABLE data", "unsafe"),
            ("(SELECT 1)", "unsafe"),
            ("read_csv('/etc/passwd')", "unsafe"),
        ],
    )
    def test_bad_expressions_say_why(self, expr: str, code: str) -> None:
        d = self.dictionary()
        d.metrics["m"] = Metric(expr=expr, dataset="orders")
        valid, error = self.check(d)["m"]
        assert valid is False and error is not None and error.split(":")[0] == code

    def test_cycles_and_missing_tables(self) -> None:
        d = self.dictionary()
        d.metrics["a"] = Metric(expr="b + 1", dataset="orders")
        d.metrics["b"] = Metric(expr="a + 1", dataset="orders")
        d.metrics["c"] = Metric(expr="COUNT(*)", dataset="nowhere")
        results = self.check(d)
        assert results["a"][1] == "cycle: a" or (results["a"][1] or "").startswith("cycle")
        assert (results["c"][1] or "").startswith("no_table")

    def test_a_metric_named_like_a_column_is_that_column(self) -> None:
        d = self.dictionary()
        d.metrics["total_price"] = Metric(expr="SUM(total_price)", dataset="orders")
        assert self.check(d)["total_price"] == (True, None)

    def test_paise_are_rupees_in_metrics(self) -> None:
        d = build([item("p", "payments", PAYMENTS)], None, company_name="X").dictionary
        d.metrics = {"gmv": Metric(expr="SUM(amount)", dataset="payments")}
        converted = to_units(PAYMENTS, d.datasets["payments"])
        assert converted["amount"].tolist() == [100.0, 250.0]
        assert PAYMENTS["amount"].tolist() == [10000, 25000]  # the stored copy is untouched
        validate.run_metric("SUM(amount)", converted)  # runs fine
        import duckdb

        total = (
            duckdb.connect()
            .register("data", converted)
            .execute("SELECT SUM(amount) FROM data")
            .fetchone()
        )
        assert total == (350.0,)

    def test_unchecked_tables_keep_their_result_when_asked(self) -> None:
        d = self.dictionary()
        d.metrics["m"] = Metric(expr="COUNT(*)", dataset="orders", valid=True)
        validate.check_metrics(d, {}, keep_unchecked=True)
        assert d.metrics["m"].valid is True


class TestAiStep:
    def built(self) -> tuple[Any, dict[str, Any]]:
        inputs = [item("d1", "orders", ORDERS), item("d2", "customers", CUSTOMERS)]
        built = build(inputs, None, company_name="X")
        profiles = {"orders": inputs[0].profile, "customers": inputs[1].profile}
        return built, profiles

    def test_only_the_profile_is_sent_never_rows_and_money_is_in_rupees(self) -> None:
        built, profiles = self.built()
        payloads = ai.build_payloads(built, profiles, tables_per_call=8)
        assert len(payloads) == 1
        text = json.dumps(payloads)
        assert "rows" in text  # the row COUNT
        orders = next(t for t in payloads[0]["tables"] if t["key"] == "orders")
        price = next(c for c in orders["columns"] if c["name"] == "total_price")
        assert price["unit"] == "rupees" and price["range"] == [70.0, 250.0]
        assert len(price.get("samples", [])) <= 5

    def test_tables_are_sent_in_batches(self) -> None:
        built, profiles = self.built()
        assert len(ai.build_payloads(built, profiles, tables_per_call=1)) == 2

    def test_only_new_columns_of_known_tables_are_sent(self) -> None:
        built, profiles = self.built()
        built.new_datasets.clear()
        built.new_columns[:] = [("orders", "customer_id")]
        payloads = ai.build_payloads(built, profiles, tables_per_call=8)
        tables = payloads[0]["tables"]
        assert [t["key"] for t in tables] == ["orders"]
        assert [c["name"] for c in tables[0]["columns"]] == ["customer_id"]

    def test_nothing_new_means_nothing_to_send(self) -> None:
        built, profiles = self.built()
        built.new_datasets.clear()
        built.new_columns.clear()
        assert ai.build_payloads(built, profiles, tables_per_call=8) == []

    def test_the_same_input_has_the_same_hash(self) -> None:
        built, profiles = self.built()
        a = ai.build_payloads(built, profiles, tables_per_call=8)
        b = ai.build_payloads(built, profiles, tables_per_call=8)
        assert ai.input_hash(a, "v1") == ai.input_hash(b, "v1")
        assert ai.input_hash(a, "v1") != ai.input_hash(a, "v2")

    @pytest.mark.parametrize(
        "text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} Hope it helps']
    )
    def test_the_answer_is_found_in_the_reply(self, text: str) -> None:
        assert ai.parse_answer(text) == {"a": 1}

    @pytest.mark.parametrize("text", ["OK", "", "{broken", "[1, 2]"])
    def test_anything_else_is_not_an_answer(self, text: str) -> None:
        assert ai.parse_answer(text) is None

    def test_good_parts_are_used_and_bad_parts_dropped(self) -> None:
        built, _ = self.built()
        answer = {
            "industry": "d2c",
            "tables": {
                "orders": {
                    "meaning": "One row per customer order",
                    "grain": "order",
                    "time_column": "created_at",
                    "columns": {
                        "total_price": {
                            "meaning": "Order value incl. tax",
                            "synonyms": ["revenue", "sales"],
                            "role": "measure",
                            "unit": "percent",
                        },
                        "customer_id": {"meaning": "Customer", "role": "bogus"},
                        "no_such_column": {"meaning": "?"},
                    },
                },
                "no_such_table": {"meaning": "?"},
            },
            "metrics": {
                "Revenue": {"expr": "SUM(total_price)", "table": "orders", "unit": "INR"},
                "orders_count": {"expr": "COUNT(*)", "table": "orders"},
                "bad": "not an object",
            },
            "relationships": [{"left": "orders.customer_id", "right": "customers.id"}, "junk"],
        }
        used = ai.apply_answers(built, [answer])
        d = built.dictionary
        orders = d.datasets["orders"]
        assert d.company.industry == "d2c"
        assert orders.meaning == "One row per customer order" and orders.grain == "order"
        price = orders.columns["total_price"]
        assert (price.meaning, price.synonyms, price.source) == (
            "Order value incl. tax",
            ["revenue", "sales"],
            "ai",
        )
        assert price.unit == "INR"  # the data's evidence beats the model's "percent"
        assert orders.columns["customer_id"].role == "key"  # a bogus role is ignored
        assert "revenue" in d.metrics and d.metrics["revenue"].source == "ai"
        assert d.metrics["orders_count"].source == "code"  # the code's metric is not replaced
        assert [r.source for r in d.relationships] == ["ai"]
        assert used["dropped"] >= 2

    def test_a_unit_without_evidence_waits_for_the_owner(self) -> None:
        built, _ = self.built()
        answer = {
            "tables": {"orders": {"columns": {"id": {"role": "measure", "unit": "INR"}}}},
        }
        built.dictionary.datasets["orders"].columns["id"].role = "measure"
        ai.apply_answers(built, [answer])
        column = built.dictionary.datasets["orders"].columns["id"]
        assert (column.unit, column.needs_review, column.review_reason) == ("INR", True, "ai_unit")


class TestReplacing:
    def old_dictionary(self) -> tuple[Dictionary, DatasetInput]:
        sheets = item("old1", "Orders", ORDERS, "sheets")
        d = build([sheets], None, company_name="X").dictionary
        key = next(iter(d.datasets))
        d.datasets[key].columns["total_price"].meaning = "Order value incl. tax"
        d.datasets[key].columns["total_price"].synonyms = ["revenue"]
        d.datasets[key].columns["total_price"].source = "owner"
        d.metrics["revenue"] = Metric(expr="SUM(total_price)", dataset=key)
        return d, sheets

    def test_tables_are_matched_by_columns_and_names(self) -> None:
        d, _ = self.old_dictionary()
        zoho = [
            item("n1", "invoices", CUSTOMERS, "zoho"),
            item("n2", "orders", ORDERS, "zoho"),
        ]
        matches = replace.match_datasets(d.datasets, zoho)
        assert {k: v.dataset_id for k, v in matches.items()} == {"orders": "n2"}

    def test_columns_match_by_name_label_and_synonym(self) -> None:
        d, _ = self.old_dictionary()
        renamed = ORDERS.rename(columns={"total_price": "Revenue", "created_at": "Created At"})
        new = item("n2", "orders", renamed, "zoho")
        mapping = replace.match_columns(d.datasets["orders"], new)
        assert mapping["total_price"] == "Revenue"  # via the synonym "revenue"
        assert mapping["created_at"] == "Created At"
        assert mapping["id"] == "id"

    def test_mappings_move_and_the_rest_is_flagged(self) -> None:
        d, _ = self.old_dictionary()
        renamed = ORDERS.rename(columns={"total_price": "Revenue"}).assign(channel=list("abab"))
        renamed = renamed.drop(columns=["total_refunds"])
        new = item("n2", "orders", renamed, "zoho")
        rebound = replace.rebind(d, from_connection_id="sheets", to_inputs=[new])
        assert rebound.matched == [("Orders", "orders")]
        built = build(
            [new],
            rebound.dictionary,
            company_name="X",
            flag_datasets=frozenset(rebound.flag_datasets),
            replaced_connection_id="sheets",
        )
        entry = built.dictionary.datasets["orders"]
        revenue = entry.columns["Revenue"]
        assert (revenue.meaning, revenue.synonyms, revenue.source) == (
            "Order value incl. tax",
            ["revenue"],
            "owner",
        )
        assert entry.dataset_id == "n2"
        assert built.dictionary.metrics["revenue"].expr == "SUM(Revenue)"  # renamed with the column
        channel = entry.columns["channel"]
        assert (channel.needs_review, channel.review_reason) == (True, "new_column")
        assert {(x["kind"], x["column"]) for x in built.diff} == {
            ("column_added", "channel"),
            ("column_unmatched", "total_refunds"),
        }

    def test_a_contradicting_unit_is_flagged(self) -> None:
        d, _ = self.old_dictionary()
        d.datasets["orders"].columns["total_price"].divide_by = 100  # the old source was in paise
        d.datasets["orders"].columns["total_price"].source = "owner"
        new = item("n2", "orders", ORDERS, "zoho")
        rebound = replace.rebind(d, from_connection_id="sheets", to_inputs=[new])
        built = build(
            [new],
            rebound.dictionary,
            company_name="X",
            flag_datasets=frozenset(rebound.flag_datasets),
            replaced_connection_id="sheets",
        )
        price = built.dictionary.datasets["orders"].columns["total_price"]
        assert (price.needs_review, price.review_reason) == (True, "unit_differs")

    def test_unmatched_tables_are_listed(self) -> None:
        d, _ = self.old_dictionary()
        other = item("n1", "invoices", CUSTOMERS, "zoho")
        rebound = replace.rebind(d, from_connection_id="sheets", to_inputs=[other])
        assert rebound.unmatched == ["Orders"]
        built = build(
            [other],
            rebound.dictionary,
            company_name="X",
            replaced_connection_id="sheets",
        )
        assert any(x["kind"] == "dataset_unmatched" for x in built.diff)


class TestDocument:
    def test_yaml_has_the_spec_shape(self, inputs: list[DatasetInput]) -> None:
        d = build(inputs, None, company_name="UrbanCart").dictionary
        d.datasets["orders"].columns["total_price"].meaning = "Order value"
        text = to_yaml(d)
        data = yaml.safe_load(text)
        assert data["company"]["currency"] == "INR"
        assert data["datasets"]["orders"]["time_column"] == "created_at"
        assert data["datasets"]["orders"]["columns"]["total_price"] == {
            "meaning": "Order value",
            "role": "measure",
            "unit": "INR",
        }
        assert data["metrics"]["orders_count"]["expr"] == "COUNT(*)"

    def test_the_document_round_trips_and_rejects_unknown_fields(
        self, inputs: list[DatasetInput]
    ) -> None:
        d = build(inputs, None, company_name="X").dictionary
        assert parse(d.model_dump(mode="json")) == d
        with pytest.raises(ValueError, match="Extra inputs"):
            parse({"company": {"nme": "x"}})

    def test_slugs(self) -> None:
        assert slug("Shopify Orders (2026).csv") == "shopify_orders_2026_csv"
        assert slug("###") == "data"
