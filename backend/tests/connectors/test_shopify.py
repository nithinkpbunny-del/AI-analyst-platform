"""The Shopify connector (M12) against the stand-in Shopify (no live calls)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.connectors.base import ConnectorError, DatasetInfo, ReadOnlyViolation
from app.connectors.shopify import (
    DEFAULT_SCOPES,
    ShopifyConnector,
    ShopifySetup,
    authorization_url,
    callback_hmac,
    check_scopes,
    exchange_code,
    normalize_shop,
    verify_callback,
)
from app.connectors.shopify_stand_in import (
    REVOKED_TOKEN,
    StandInData,
    StandInShopifyTransport,
    _response,
    order_node,
    sample_data,
    signed_callback,
    throttled,
)
from tests.recorded_shopify import SHOP, TOKEN, code, shopify_test_setup

NOW = datetime(2026, 11, 1, tzinfo=UTC)
TABLES = ("orders", "order_items", "customers", "products", "variants")
# What the connector must never ask Shopify for (data minimisation).
PERSONAL_FIELDS = (
    "email",
    "phone",
    "firstName",
    "lastName",
    "displayName",
    "address1",
    "address2",
    "zip",
    "note",
    "billingAddress",
)


def connector(
    fake: StandInShopifyTransport,
    *,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    token: str = TOKEN,
    max_rows: int = 100_000,
    **setup: Any,
) -> ShopifyConnector:
    return ShopifyConnector(
        setup=shopify_test_setup(fake, **setup),
        shop=SHOP,
        access_token=token,
        scopes=frozenset(scopes),
        timezone="Asia/Kolkata",
        max_rows=max_rows,
        clock=lambda: NOW,
    )


def fetch(c: ShopifyConnector, ref: str, cursor: str | None = None) -> Any:
    return c.fetch(DatasetInfo(ref, ref), since_cursor=cursor)


def queries(fake: StandInShopifyTransport) -> list[str]:
    return [str(payload.get("query", "")) for _, _, payload in fake.requests]


@pytest.fixture
def fake() -> StandInShopifyTransport:
    return StandInShopifyTransport(sample_data(orders=40))


class TestShopAddress:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("urbancart", "urbancart.myshopify.com"),
            ("UrbanCart.myshopify.com", "urbancart.myshopify.com"),
            ("https://urbancart.myshopify.com/admin/orders", "urbancart.myshopify.com"),
            ("https://admin.shopify.com/store/urbancart/orders", "urbancart.myshopify.com"),
            ("  my-shop-2 ", "my-shop-2.myshopify.com"),
        ],
    )
    def test_accepted_forms(self, raw: str, expected: str) -> None:
        assert normalize_shop(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "evil.com",
            "urbancart.myshopify.com.evil.com",
            "https://evil.com/?x=.myshopify.com",
            "-bad.myshopify.com",
            "a b",
            "shop@evil.com",
        ],
    )
    def test_anything_else_is_refused(self, raw: str) -> None:
        assert normalize_shop(raw) is None


class TestSignIn:
    def test_the_approval_page_asks_only_for_read_scopes(self) -> None:
        setup = ShopifySetup(mode="shopify", client_id="app-key")
        url = authorization_url(setup, shop=SHOP, state="s" * 20)
        assert url.startswith(f"https://{SHOP}/admin/oauth/authorize?")
        assert "read_orders%2Cread_products%2Cread_customers%2Cread_inventory" in url
        assert "write_" not in url
        assert (
            "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fconnections%2Fshopify%2Fcallback" in url
        )

    def test_the_stand_in_uses_the_local_page(self) -> None:
        setup = ShopifySetup(mode="stand_in", client_id="x")
        assert authorization_url(setup, shop=SHOP, state="s").startswith(
            "http://localhost:8000/connections/shopify/stand-in?"
        )

    def test_the_callback_hmac_is_checked(self) -> None:
        setup = ShopifySetup(mode="shopify", client_secret="app-secret")
        params = signed_callback(SHOP, "state-1", "code-1", "app-secret", 1_700_000_000)
        assert verify_callback(setup, params)
        assert not verify_callback(setup, {**params, "shop": "other.myshopify.com"})
        assert not verify_callback(setup, {**params, "code": "code-2"})
        assert not verify_callback(setup, {k: v for k, v in params.items() if k != "hmac"})
        forged = signed_callback(SHOP, "state-1", "code-1", "wrong-secret", 1_700_000_000)
        assert not verify_callback(setup, forged)
        assert callback_hmac("app-secret", params) == params["hmac"]

    def test_scopes_that_can_write_are_refused(self) -> None:
        check_scopes(frozenset({"read_orders", "read_products", "read_customers"}))
        check_scopes(
            frozenset({"read_orders", "read_products", "read_customers", "read_all_orders"})
        )
        with pytest.raises(ConnectorError) as raised:
            check_scopes(
                frozenset({"read_orders", "read_products", "read_customers", "write_orders"})
            )
        assert raised.value.kind == "invalid" and "more access" in raised.value.message
        with pytest.raises(ConnectorError) as missing:
            check_scopes(frozenset({"read_orders"}))
        assert "orders, products and customers" in missing.value.message

    def test_the_code_becomes_a_token_once(self, fake: StandInShopifyTransport) -> None:
        setup = shopify_test_setup(fake)
        http = setup.http(SHOP, purpose="oauth")
        issued = code()
        token = exchange_code(setup, http, shop=SHOP, code=issued)
        assert token.access_token.startswith("shpat_")
        assert token.scopes == frozenset(DEFAULT_SCOPES)
        with pytest.raises(ConnectorError) as again:
            exchange_code(setup, http, shop=SHOP, code=issued)
        assert again.value.kind == "invalid"

    def test_a_token_with_write_scopes_is_refused(self, fake: StandInShopifyTransport) -> None:
        setup = shopify_test_setup(fake)
        with pytest.raises(ConnectorError):
            exchange_code(
                setup,
                setup.http(SHOP, purpose="oauth"),
                shop=SHOP,
                code=code(scopes="read_orders,read_products,read_customers,write_orders"),
            )

    def test_a_wrong_app_secret_is_refused(self, fake: StandInShopifyTransport) -> None:
        setup = shopify_test_setup(fake, client_secret="not-the-secret")
        with pytest.raises(ConnectorError):
            exchange_code(setup, setup.http(SHOP, purpose="oauth"), shop=SHOP, code=code())

    def test_the_sign_in_client_cannot_query(self, fake: StandInShopifyTransport) -> None:
        http = shopify_test_setup(fake).http(SHOP, purpose="oauth")
        with pytest.raises(ReadOnlyViolation):
            http.request(
                "POST", ShopifySetup().graphql_url(SHOP), json={"query": "{ shop { name } }"}
            )


class TestTables:
    def test_test_says_what_it_can_read(self, fake: StandInShopifyTransport) -> None:
        result = connector(fake).test()
        assert result.ok
        checks = {c.label: c.ok for c in result.checks}
        assert checks == {
            "signed_in": True,
            "read_only": True,
            "orders_readable": True,
            "all_orders": False,
        }
        assert "60 days" in result.message
        assert result.details["currency"] == "INR"
        with_all = connector(fake, scopes=(*DEFAULT_SCOPES, "read_all_orders")).test()
        assert "60 days" not in with_all.message

    def test_five_tables(self, fake: StandInShopifyTransport) -> None:
        assert [d.ref for d in connector(fake).list_datasets()] == list(TABLES)

    def test_orders_in_rupees_and_the_workspace_time_zone(
        self, fake: StandInShopifyTransport
    ) -> None:
        result = fetch(connector(fake), "orders")
        frame = result.frame
        assert len(frame) == 40 and frame["id"].is_unique
        first = frame.loc[frame["id"] == "5000000001"].iloc[0]
        # 1 x ₹1,699 less a ₹50 discount: rupees, not paise
        assert first["total_price"] == pytest.approx(1649.0)
        assert first["total_discounts"] == pytest.approx(50.0)
        assert first["total_line_items_price"] == pytest.approx(1699.0)
        assert first["total_refunds"] == pytest.approx(100.0)
        assert str(first["created_at"]) == "2026-06-01 10:00:00"  # 04:30 UTC in IST
        assert first["financial_status"] == "paid" and first["currency"] == "INR"
        assert first["shipping_state"] == "Karnataka" and first["customer_id"] == "7000000001"
        money = {c.name: (c.unit, c.stored_in) for c in result.columns}
        for column in (
            "total_price",
            "total_refunds",
            "total_discounts",
            "total_line_items_price",
            "total_shipping",
            "total_tax",
        ):
            assert money[column] == ("INR", "rupees"), column
        assert money["items_count"] == (None, None) and money["id"] == (None, None)
        assert result.cursor == "2026-06-02T19:30:00Z" and result.key_column == "id"
        assert not result.incremental and not result.truncated

    def test_order_items_customers_products_variants(self, fake: StandInShopifyTransport) -> None:
        c = connector(fake)
        items = fetch(c, "order_items")
        assert len(items.frame) == 40
        line = items.frame.iloc[0]
        assert {
            "order_id",
            "product_id",
            "variant_id",
            "product_type",
            "quantity",
            "unit_price",
        } <= set(items.frame.columns)
        assert line["unit_price"] in (1699.0, 1299.0, 689.0, 549.0)
        assert {c.name: c.stored_in for c in items.columns}["unit_price"] == "rupees"
        customers = fetch(c, "customers")
        assert len(customers.frame) == 10
        assert {c.name: c.stored_in for c in customers.columns}["total_spent"] == "rupees"
        assert sorted(customers.frame["total_spent"])[:2] == [1000.0, 1250.0]
        assert set(customers.frame.columns) == {
            "id",
            "created_at",
            "updated_at",
            "city",
            "state",
            "country",
            "accepts_marketing",
            "orders_count",
            "total_spent",
        }
        products = fetch(c, "products")
        assert len(products.frame) == 4 and products.frame["status"].iloc[0] == "active"
        variants = fetch(c, "variants")
        assert variants.frame["cost_per_item"].iloc[0] == pytest.approx(1257.26)

    def test_no_personal_data_is_ever_requested(self, fake: StandInShopifyTransport) -> None:
        c = connector(fake)
        for ref in TABLES:
            fetch(c, ref)
        connector(fake, bulk_threshold=1).fetch(DatasetInfo("orders", "orders"))
        text = " ".join(queries(fake))
        for word in PERSONAL_FIELDS:
            assert not re.search(rf"\b{word}\b", text), word

    def test_cost_without_the_inventory_scope_is_not_asked_for(
        self, fake: StandInShopifyTransport
    ) -> None:
        c = connector(fake, scopes=("read_orders", "read_products", "read_customers"))
        variants = fetch(c, "variants")
        assert variants.frame["cost_per_item"].isna().all()
        assert "inventoryItem" not in " ".join(queries(fake))

    def test_another_currency_leaves_the_unit_open(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=5, currency="USD"))
        result = fetch(connector(fake), "orders")
        price = next(c for c in result.columns if c.name == "total_price")
        assert price.unit is None and price.stored_in is None and price.currencies == ("USD",)

    def test_test_orders_are_left_out(self) -> None:
        data = sample_data(orders=5)
        data.orders.append(order_node(5999999999, created_at="2026-06-05T00:00:00Z", test=True))
        result = fetch(connector(StandInShopifyTransport(data)), "orders")
        assert "5999999999" not in set(result.frame["id"])

    def test_orders_older_than_the_history_window_are_not_read(self) -> None:
        data = sample_data(orders=3)
        data.orders.append(order_node(5000000999, created_at="2020-01-01T00:00:00Z"))
        result = fetch(connector(StandInShopifyTransport(data), history_days=365), "orders")
        assert "5000000999" not in set(result.frame["id"])


class TestPaging:
    def test_pages_are_followed(self, fake: StandInShopifyTransport) -> None:
        result = fetch(connector(fake), "orders")  # 40 orders, 25 per page
        assert len(result.frame) == 40
        pages = [q for q in queries(fake) if "orders(first: $first" in q]
        assert len(pages) == 2

    def test_an_order_with_many_line_items_is_read_completely(self) -> None:
        lines = [
            {
                "product": 8000000001 + i,
                "title": f"Item {i}",
                "type": "Beauty",
                "quantity": 1,
                "price": 100,
            }
            for i in range(23)
        ]
        data = StandInData(
            orders=[order_node(5000000001, created_at="2026-06-01T00:00:00Z", lines=lines)]
        )
        fake = StandInShopifyTransport(data)
        items = fetch(connector(fake), "order_items")
        assert len(items.frame) == 23 and items.frame["id"].is_unique
        assert set(items.frame["order_id"]) == {"5000000001"}
        assert any("order(id: $id)" in q for q in queries(fake))

    def test_the_row_cap_stops_paging(self, fake: StandInShopifyTransport) -> None:
        result = fetch(connector(fake, max_rows=30), "orders")
        assert len(result.frame) == 30 and result.truncated

    def test_throttling_waits_and_retries(self, fake: StandInShopifyTransport) -> None:
        waits: list[float] = []
        fake.queued = [throttled(), throttled()]
        c = connector(fake, sleep=waits.append)
        result = fetch(c, "products")
        assert len(result.frame) == 4
        assert len(waits) >= 2 and all(w > 0 for w in waits)

    def test_endless_throttling_becomes_rate_limited(self, fake: StandInShopifyTransport) -> None:
        fake.queued = [throttled() for _ in range(10)]
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(fake), "products")
        assert raised.value.kind == "rate_limited"

    def test_a_low_bucket_slows_the_next_request(self, fake: StandInShopifyTransport) -> None:
        waits: list[float] = []
        c = connector(fake, sleep=waits.append)
        c._note_cost(
            {
                "cost": {
                    "actualQueryCost": 400,
                    "throttleStatus": {"currentlyAvailable": 100, "restoreRate": 100},
                }
            }
        )
        c._pace()
        assert waits == [3.0]


class TestBulk:
    def test_a_big_first_read_uses_one_bulk_export(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60), polls_until_done=3)
        waits: list[float] = []
        bulk = fetch(connector(fake, bulk_threshold=50, sleep=waits.append), "order_items")
        paged = fetch(connector(StandInShopifyTransport(sample_data(orders=60))), "order_items")
        assert fake.bulk_started == 1
        assert len(bulk.frame) == 60
        assert sorted(bulk.frame["id"]) == sorted(paged.frame["id"])
        merged = bulk.frame.set_index("id").sort_index()
        expected = paged.frame.set_index("id").sort_index()
        assert merged.equals(expected)
        assert bulk.cursor == paged.cursor
        assert waits  # it polled while Shopify prepared the file
        assert any(path.startswith("/stand-in-bulk/") for _, path, _ in fake.requests)
        assert fake.writes_received == 0

    def test_the_bulk_request_is_the_only_mutation_and_its_query_is_a_read(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60))
        fetch(connector(fake, bulk_threshold=10), "orders")
        mutations = [q for q in queries(fake) if q.lstrip().startswith("mutation")]
        assert len(mutations) == 1 and "bulkOperationRunQuery" in mutations[0]
        payload = next(
            p for _, _, p in fake.requests if "bulkOperationRunQuery" in str(p.get("query"))
        )
        assert payload["variables"]["q"].lstrip().startswith("{ orders(")

    def test_incremental_reads_never_use_bulk(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60))
        fetch(connector(fake, bulk_threshold=1), "orders", cursor="2026-06-01T00:00:00Z")
        assert fake.bulk_started == 0

    def test_the_row_cap_stops_reading_the_file(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60))
        result = fetch(connector(fake, bulk_threshold=10, max_rows=20), "orders")
        assert len(result.frame) == 20 and result.truncated

    def test_another_running_export_is_waited_for(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60), polls_until_done=2)
        c = connector(fake, bulk_threshold=10)
        c._start_bulk(
            "{ products { edges { node { id } } } }"
        )  # someone else's export, still running
        result = fetch(c, "orders")
        assert len(result.frame) == 60 and fake.bulk_started == 2

    def test_a_failed_export_is_reported(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60))
        c = connector(fake, bulk_threshold=10)
        original = fake._poll

        def failing() -> dict[str, Any] | None:
            current = original()
            return None if current is None else {**current, "status": "FAILED", "url": None}

        fake._poll = failing  # type: ignore[method-assign]
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "orders")
        assert raised.value.kind == "unavailable" and "export" in raised.value.message

    def test_an_export_that_never_finishes_times_out(self) -> None:
        fake = StandInShopifyTransport(sample_data(orders=60), polls_until_done=10_000)
        c = connector(fake, bulk_threshold=10, bulk_timeout_seconds=10, bulk_poll_seconds=3.0)
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "orders")
        assert "too long" in raised.value.message

    def test_an_empty_export(self) -> None:
        fake = StandInShopifyTransport(StandInData())
        c = connector(fake, bulk_threshold=0)
        assert len(fetch(c, "customers").frame) == 0


class TestIncremental:
    def test_only_rows_updated_since_the_cursor_are_read(
        self, fake: StandInShopifyTransport
    ) -> None:
        result = fetch(connector(fake), "orders", cursor="2026-06-02T10:00:00Z")
        assert result.incremental
        # an hour of overlap before the cursor: 09:00 UTC onwards
        assert sorted(result.frame["updated_at"])[0] >= datetime(2026, 6, 2, 14, 30)
        assert len(result.frame) == 11
        last = [q for _, _, q in fake.requests if q.get("variables", {}).get("q")][-1]
        assert last["variables"]["q"] == "test:false updated_at:>='2026-06-02T09:00:00Z'"

    def test_an_unreadable_cursor_reads_everything(self, fake: StandInShopifyTransport) -> None:
        result = fetch(connector(fake), "orders", cursor="garbage")
        assert not result.incremental and len(result.frame) == 40

    def test_no_change_keeps_the_cursor(self, fake: StandInShopifyTransport) -> None:
        result = fetch(connector(fake), "orders", cursor="2027-01-01T00:00:00Z")
        assert len(result.frame) == 0 and result.cursor == "2027-01-01T00:00:00Z"


class TestErrors:
    def test_a_revoked_token_needs_a_new_sign_in(self, fake: StandInShopifyTransport) -> None:
        c = connector(fake, token=REVOKED_TOKEN)
        with pytest.raises(ConnectorError) as raised:
            fetch(c, "orders")
        assert raised.value.kind == "needs_reauth"
        assert raised.value.message.startswith("Shopify stopped sharing data")
        result = connector(fake, token=REVOKED_TOKEN).test()
        assert not result.ok
        assert {c.label: c.ok for c in result.checks}["signed_in"] is False

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(403, "needs_reauth"), (404, "not_found"), (402, "unavailable"), (500, "unavailable")],
    )
    def test_http_errors(self, fake: StandInShopifyTransport, status: int, kind: str) -> None:
        fake.queued = [_response(status, {"errors": "x"}) for _ in range(5)]
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(fake), "orders")
        assert raised.value.kind == kind

    def test_access_denied_is_explained(self, fake: StandInShopifyTransport) -> None:
        fake.queued = [
            _response(
                200,
                {"errors": [{"message": "Access denied", "extensions": {"code": "ACCESS_DENIED"}}]},
            )
        ]
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(fake), "customers")
        assert raised.value.kind == "invalid" and "did not allow" in raised.value.message

    def test_an_unreadable_answer(self, fake: StandInShopifyTransport) -> None:
        fake.queued = [httpx.Response(200, text="<html>")]
        with pytest.raises(ConnectorError) as raised:
            fetch(connector(fake), "orders")
        assert raised.value.kind == "unavailable"

    def test_an_unknown_table(self, fake: StandInShopifyTransport) -> None:
        with pytest.raises(ConnectorError):
            connector(fake).fetch(DatasetInfo("draft_orders", "draft_orders"))


class TestReadOnly:
    @pytest.mark.parametrize(
        "document",
        [
            'mutation { orderUpdate(input: {id: "gid://shopify/Order/1", note: "x"}) '
            "{ userErrors { message } } }",
            'mutation { customerDelete(input: {id: "gid://shopify/Customer/1"}) '
            "{ userErrors { message } } }",
            'mutation { bulkOperationCancel(id: "gid://shopify/BulkOperation/1") '
            "{ userErrors { message } } }",
        ],
    )
    def test_a_mutation_is_refused_before_it_is_sent(
        self, fake: StandInShopifyTransport, document: str
    ) -> None:
        c = connector(fake)
        with pytest.raises(ReadOnlyViolation):
            c._graphql(document)
        with pytest.raises(ReadOnlyViolation):  # past the first check, the HTTP client refuses it
            c._http.request("POST", ShopifySetup().graphql_url(SHOP), json={"query": document})
        assert fake.requests == [] and fake.writes_received == 0

    def test_a_full_read_sends_no_write(self, fake: StandInShopifyTransport) -> None:
        c = connector(fake, bulk_threshold=10)
        for ref in TABLES:
            fetch(c, ref)
        assert fake.writes_received == 0
        methods = {method for method, _, _ in fake.requests}
        assert methods <= {"POST", "GET"}
        for _, path, payload in fake.requests:
            if path.endswith("/graphql.json"):
                assert json.dumps(payload).count("mutation") <= 1
