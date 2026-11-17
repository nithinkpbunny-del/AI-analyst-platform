"""The read-only GraphQL guard (BUILD_SPEC 6.3 layer 2, 8.5; DECISIONS D93).

Queries pass. Every mutation is refused, except Shopify's ``bulkOperationRunQuery`` in its exact shape with a pure
query inside. The HTTP client checks the request body again, so even code that skipped the first check cannot send a
mutation.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.connectors.base import ReadOnlyViolation
from app.connectors.graphql_guard import validate_body, validate_query, validate_request
from app.connectors.shopify import ShopifySetup

BULK = (
    "mutation($q: String!) { bulkOperationRunQuery(query: $q) { "
    "bulkOperation { id status } userErrors { field message } } }"
)
INNER = "{ orders { edges { node { id createdAt } } } }"


class TestQueries:
    @pytest.mark.parametrize(
        "document",
        [
            "{ shop { name } }",
            "query { orders(first: 5) { edges { node { id } } } }",
            "query Named($q: String) { orders(first: 5, query: $q) { pageInfo { hasNextPage } } }",
            "query { ...F } fragment F on QueryRoot { shop { name } }",
            "{ __schema { queryType { name } } }",
        ],
    )
    def test_reads_pass(self, document: str) -> None:
        validate_request(document)
        validate_query(document)


MUTATIONS = [
    'mutation { orderUpdate(input: {id: "gid://shopify/Order/1", note: "x"}) { userErrors { message } } }',
    "mutation M { orderUpdate(input: {id: 1}) { order { id } } }",
    'mutation { customerDelete(input: {id: "gid://shopify/Customer/1"}) { deletedCustomerId } }',
    'mutation { productCreate(input: {title: "x"}) { product { id } } }',
    'mutation { orderCancel(orderId: "1", reason: OTHER, refund: true, restock: true) { job { id } } }',
    'mutation { bulkOperationCancel(id: "gid://shopify/BulkOperation/1") { bulkOperation { id } } }',
    'mutation { bulkOperationRunMutation(mutation: "mutation { x }", stagedUploadPath: "p") '
    "{ bulkOperation { id } } }",
    # a read-looking name cannot smuggle a write next to it
    f'mutation {{ bulkOperationRunQuery(query: "{INNER}") {{ bulkOperation {{ id }} }} '
    "orderUpdate(input: {id: 1}) { userErrors { message } } }",
]


class TestMutations:
    @pytest.mark.parametrize("document", MUTATIONS)
    def test_every_mutation_is_refused(self, document: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_request(document)

    @pytest.mark.parametrize("document", MUTATIONS)
    def test_a_mutation_is_never_a_query(self, document: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_query(document)

    def test_a_subscription_is_refused(self) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_request("subscription { orderCreated { id } }")

    @pytest.mark.parametrize(
        "document",
        [
            "query A { shop { name } } mutation B { orderUpdate(input: {id: 1}) { userErrors { message } } }",
            "query A { shop { name } } query B { shop { name } }",
            "",
            "{ shop { name }",
            "not graphql at all",
            "x" * 60_000,
        ],
    )
    def test_several_operations_or_unreadable_documents_are_refused(self, document: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_request(document)


class TestBulkRead:
    def test_the_bulk_read_passes_with_a_variable(self) -> None:
        validate_request(BULK, {"q": INNER})

    def test_the_bulk_read_passes_with_a_literal(self) -> None:
        validate_request(
            f"mutation {{ bulkOperationRunQuery(query: {json.dumps(INNER)}) "
            "{ bulkOperation { id status } userErrors { field message } } }"
        )

    @pytest.mark.parametrize(
        "inner",
        [
            "mutation { orderUpdate(input: {id: 1}) { userErrors { message } } }",
            "subscription { orderCreated { id } }",
            "{ orders { edges",
            "",
        ],
    )
    def test_the_inner_document_must_be_a_pure_query(self, inner: str) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_request(BULK, {"q": inner})

    @pytest.mark.parametrize(
        ("document", "variables"),
        [
            # missing or non-string query
            (BULK, {}),
            (BULK, {"q": 5}),
            ("mutation { bulkOperationRunQuery(query: 5) { bulkOperation { id } } }", None),
            # alias, directives, extra arguments
            (
                "mutation($q: String!) { run: bulkOperationRunQuery(query: $q) { bulkOperation { id } } }",
                {"q": INNER},
            ),
            (
                "mutation($q: String!) @x { bulkOperationRunQuery(query: $q) { bulkOperation { id } } }",
                {"q": INNER},
            ),
            (
                "mutation($q: String!) { bulkOperationRunQuery(query: $q, groupObjects: true) "
                "{ bulkOperation { id } } }",
                {"q": INNER},
            ),
            # selections other than the result's plain fields
            (
                "mutation($q: String!) { bulkOperationRunQuery(query: $q) { bulkOperation { url } } }",
                {"q": INNER},
            ),
            (
                "mutation($q: String!) { bulkOperationRunQuery(query: $q) { job { id } } }",
                {"q": INNER},
            ),
            (
                "mutation($q: String!) { bulkOperationRunQuery(query: $q) { ...R } } "
                "fragment R on BulkOperationRunQueryPayload { bulkOperation { id } }",
                {"q": INNER},
            ),
            (
                "mutation($q: String!) { bulkOperationRunQuery(query: $q) }",
                {"q": INNER},
            ),
        ],
    )
    def test_any_other_shape_is_refused(
        self, document: str, variables: dict[str, Any] | None
    ) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_request(document, variables)


class TestBody:
    def test_a_query_body_passes(self) -> None:
        validate_body(json.dumps({"query": "{ shop { name } }"}).encode())
        validate_body(json.dumps({"query": BULK, "variables": {"q": INNER}}).encode())

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[1, 2]",
            json.dumps({"query": MUTATIONS[0]}).encode(),
            json.dumps({"query": BULK, "variables": ["x"]}).encode(),
            json.dumps({"variables": {}}).encode(),
        ],
    )
    def test_anything_else_is_refused(self, body: bytes) -> None:
        with pytest.raises(ReadOnlyViolation):
            validate_body(body)


class TestHttpClient:
    """The second check: in the HTTP client, after any code that might have skipped the first."""

    def _client(self) -> tuple[Any, list[httpx.Request]]:
        sent: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"data": {}})

        setup = ShopifySetup(mode="shopify", transport=httpx.MockTransport(answer))
        return setup.http("shop.myshopify.com"), sent

    def test_a_query_is_sent(self) -> None:
        http, sent = self._client()
        url = ShopifySetup().graphql_url("shop.myshopify.com")
        assert http.request("POST", url, json={"query": "{ shop { name } }"}).status_code == 200
        assert len(sent) == 1

    @pytest.mark.parametrize("document", MUTATIONS[:3])
    def test_a_mutation_never_leaves_the_process(self, document: str) -> None:
        http, sent = self._client()
        url = ShopifySetup().graphql_url("shop.myshopify.com")
        with pytest.raises(ReadOnlyViolation):
            http.request("POST", url, json={"query": document})
        assert sent == []

    def test_other_writes_and_other_endpoints_are_refused(self) -> None:
        http, sent = self._client()
        for method in ("PUT", "PATCH", "DELETE"):
            with pytest.raises(ReadOnlyViolation):
                http.request(method, "https://shop.myshopify.com/admin/api/2026-07/orders/1.json")
        with pytest.raises(ReadOnlyViolation):  # a REST POST is not on the allow-list
            http.request(
                "POST", "https://shop.myshopify.com/admin/api/2026-07/orders.json", json={}
            )
        with pytest.raises(ReadOnlyViolation):  # the token endpoint is only for the sign-in client
            http.post_form("https://shop.myshopify.com/admin/oauth/access_token", {"code": "x"})
        assert sent == []

    def test_the_raw_client_is_guarded_too(self) -> None:
        http, sent = self._client()
        raw: httpx.Client = http._ReadOnlyHttpClient__client
        url = ShopifySetup().graphql_url("shop.myshopify.com")
        with pytest.raises(ReadOnlyViolation):
            raw.post(url, json={"query": MUTATIONS[0]})
        assert sent == []
