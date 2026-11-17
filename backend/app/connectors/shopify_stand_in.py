"""A local stand-in for Shopify, used in development without app credentials (``APP_ENV=development``) and by tests.

It imitates what the connector uses, in Shopify's own formats:

* the OAuth token endpoint (``POST /admin/oauth/access_token``), for codes issued by the stand-in consent page;
* the Admin GraphQL endpoint: every document is parsed and validated against a small copy of Shopify's schema
  (only the fields the connector asks for), then answered from in-memory data. Cursor paging, ``query`` search
  filters (``test:``, ``created_at:``, ``updated_at:``), ``sortKey``/``reverse``, the ``*Count`` fields, cost
  extensions, and bulk operations (``bulkOperationRunQuery``, ``currentBulkOperation``, the JSONL file with
  ``__parentId`` lines) behave like Shopify's;
* any other mutation is refused and counted in ``writes_received`` (the connector must never send one).

The data comes from the demo ``eval/fixtures/shopify_*.csv`` files (``make fixtures``) or, when they are missing,
from a small built-in sample. Nothing here is a secret.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
from graphql import (
    DocumentNode,
    FieldNode,
    GraphQLError,
    GraphQLSchema,
    OperationDefinitionNode,
    OperationType,
    SelectionSetNode,
    build_schema,
    parse,
    validate,
)
from graphql.utilities import value_from_ast_untyped

STAND_IN_CLIENT_ID = "stand-in-shopify"
STAND_IN_CLIENT_SECRET = "stand-in-shopify-secret"  # noqa: S105 (not a real secret)
STAND_IN_TOKEN_PREFIX = "shpat_standin"  # noqa: S105
REVOKED_TOKEN = "shpat_standin_revoked"  # noqa: S105 (tests: a token Shopify no longer accepts)
CODE_PREFIX = "standin-code."
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "eval" / "fixtures"

SCHEMA_SDL = """
scalar DateTime
scalar Decimal
scalar Money
scalar UnsignedInt64

type Query {
  shop: Shop!
  orders(first: Int, after: String, query: String, sortKey: SortKey, reverse: Boolean): OrderConnection!
  order(id: ID!): Order
  customers(first: Int, after: String, query: String, sortKey: SortKey, reverse: Boolean): CustomerConnection!
  products(first: Int, after: String, query: String, sortKey: SortKey, reverse: Boolean): ProductConnection!
  productVariants(first: Int, after: String, query: String, sortKey: VariantSortKey, reverse: Boolean):
    ProductVariantConnection!
  ordersCount(query: String, limit: Int): Count
  customersCount(query: String, limit: Int): Count
  productsCount(query: String, limit: Int): Count
  productVariantsCount(query: String, limit: Int): Count
  currentBulkOperation(type: BulkOperationType): BulkOperation
}

type Mutation {
  bulkOperationRunQuery(query: String!): BulkOperationRunQueryPayload
  bulkOperationCancel(id: ID!): BulkOperationRunQueryPayload
  orderUpdate(input: OrderInput!): OrderUpdatePayload
  customerDelete(input: CustomerDeleteInput!): OrderUpdatePayload
}

input OrderInput { id: ID! note: String }
input CustomerDeleteInput { id: ID! }
type OrderUpdatePayload { userErrors: [UserError!]! }

enum SortKey { UPDATED_AT CREATED_AT ID }
enum VariantSortKey { ID SKU POSITION }
enum BulkOperationType { QUERY MUTATION }

type Shop { name: String! currencyCode: String! myshopifyDomain: String! }
type Count { count: Int! precision: String! }
type PageInfo { hasNextPage: Boolean! endCursor: String }
type MoneyV2 { amount: Decimal! currencyCode: String! }
type MoneyBag { shopMoney: MoneyV2! }
type MailingAddress { city: String province: String country: String }
type EmailConsent { marketingState: String }

type Order {
  id: ID! name: String! createdAt: DateTime! updatedAt: DateTime! cancelledAt: DateTime
  sourceName: String displayFinancialStatus: String displayFulfillmentStatus: String
  paymentGatewayNames: [String!]! subtotalLineItemsQuantity: Int! test: Boolean!
  customer: Customer shippingAddress: MailingAddress
  totalPriceSet: MoneyBag! subtotalPriceSet: MoneyBag totalDiscountsSet: MoneyBag! totalRefundedSet: MoneyBag!
  totalShippingPriceSet: MoneyBag! totalTaxSet: MoneyBag!
  lineItems(first: Int, after: String): LineItemConnection!
}
type LineItem {
  id: ID! title: String! sku: String quantity: Int!
  originalUnitPriceSet: MoneyBag! totalDiscountSet: MoneyBag!
  product: Product variant: ProductVariant
}
type Customer {
  id: ID! createdAt: DateTime! updatedAt: DateTime! numberOfOrders: UnsignedInt64! amountSpent: MoneyV2!
  defaultAddress: MailingAddress emailMarketingConsent: EmailConsent
}
type ProductPriceRangeV2 { minVariantPrice: MoneyV2! maxVariantPrice: MoneyV2! }
type Product {
  id: ID! title: String! productType: String! vendor: String! status: String!
  createdAt: DateTime! updatedAt: DateTime! totalInventory: Int priceRangeV2: ProductPriceRangeV2!
}
type InventoryItem { unitCost: MoneyV2 }
type ProductVariant {
  id: ID! title: String! sku: String price: Money! inventoryQuantity: Int
  createdAt: DateTime! updatedAt: DateTime! product: Product! inventoryItem: InventoryItem!
}

type BulkOperation {
  id: ID! status: String! errorCode: String objectCount: UnsignedInt64! url: String partialDataUrl: String
  type: BulkOperationType! createdAt: DateTime!
}
type UserError { field: [String!] message: String! code: String }
type BulkOperationRunQueryPayload { bulkOperation: BulkOperation userErrors: [UserError!]! }

type OrderEdge { cursor: String! node: Order! }
type OrderConnection { edges: [OrderEdge!]! nodes: [Order!]! pageInfo: PageInfo! }
type LineItemEdge { cursor: String! node: LineItem! }
type LineItemConnection { edges: [LineItemEdge!]! nodes: [LineItem!]! pageInfo: PageInfo! }
type CustomerEdge { cursor: String! node: Customer! }
type CustomerConnection { edges: [CustomerEdge!]! nodes: [Customer!]! pageInfo: PageInfo! }
type ProductEdge { cursor: String! node: Product! }
type ProductConnection { edges: [ProductEdge!]! nodes: [Product!]! pageInfo: PageInfo! }
type ProductVariantEdge { cursor: String! node: ProductVariant! }
type ProductVariantConnection { edges: [ProductVariantEdge!]! nodes: [ProductVariant!]! pageInfo: PageInfo! }
"""


@lru_cache(maxsize=1)
def schema() -> GraphQLSchema:
    return build_schema(SCHEMA_SDL)


# ------------------------------------------------------------------------------------------- the data
def _gid(kind: str, number: Any) -> str:
    return f"gid://shopify/{kind}/{number}"


def _utc(text: str) -> str:
    return datetime.fromisoformat(text).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(amount: Any, currency: str = "INR") -> dict[str, Any]:
    return {"amount": str(round(float(amount or 0), 2)), "currencyCode": currency}


def _bag(amount: Any, currency: str = "INR") -> dict[str, Any]:
    return {"shopMoney": _money(amount, currency)}


@dataclass
class StandInData:
    shop_name: str = "UrbanCart (stand-in)"
    currency: str = "INR"
    orders: list[dict[str, Any]] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)


def order_node(
    number: int,
    *,
    created_at: str,
    updated_at: str | None = None,
    customer: int | None = 7000000001,
    channel: str = "web",
    state: str = "Karnataka",
    city: str = "Bengaluru",
    lines: list[dict[str, Any]] | None = None,
    discounts: float = 0.0,
    refunds: float = 0.0,
    test: bool = False,
    currency: str = "INR",
    financial_status: str = "PAID",
) -> dict[str, Any]:
    """One order in Shopify's shape. ``lines``: ``{"product": int, "title", "type", "sku", "quantity", "price"}``."""
    lines = lines or [
        {
            "product": 8000000001,
            "title": "Wireless Earbuds",
            "type": "Electronics",
            "quantity": 1,
            "price": 1699,
        }
    ]
    items = []
    gross = 0.0
    for index, line in enumerate(lines, start=1):
        gross += float(line["price"]) * int(line["quantity"])
        share = discounts / len(lines)
        items.append(
            {
                "id": _gid("LineItem", number * 100 + index),
                "title": line["title"],
                "sku": line.get("sku"),
                "quantity": int(line["quantity"]),
                "originalUnitPriceSet": _bag(line["price"], currency),
                "totalDiscountSet": _bag(share, currency),
                "product": {
                    "id": _gid("Product", line["product"]),
                    "productType": line.get("type", ""),
                },
                "variant": {"id": _gid("ProductVariant", int(line["product"]) + 1_000_000_000)},
            }
        )
    subtotal = gross - discounts
    return {
        "id": _gid("Order", number),
        "name": f"#{number % 100000}",
        "createdAt": created_at,
        "updatedAt": updated_at or created_at,
        "cancelledAt": None,
        "sourceName": channel,
        "displayFinancialStatus": financial_status,
        "displayFulfillmentStatus": "FULFILLED",
        "paymentGatewayNames": ["razorpay"],
        "subtotalLineItemsQuantity": sum(i["quantity"] for i in items),
        "test": test,
        "customer": {"id": _gid("Customer", customer)} if customer else None,
        "shippingAddress": {"city": city, "province": state, "country": "India"},
        "totalPriceSet": _bag(subtotal, currency),
        "subtotalPriceSet": _bag(subtotal, currency),
        "totalDiscountsSet": _bag(discounts, currency),
        "totalRefundedSet": _bag(refunds, currency),
        "totalShippingPriceSet": _bag(0, currency),
        "totalTaxSet": _bag(0, currency),
        "lineItems": items,
    }


def sample_data(
    *, orders: int = 40, start: str = "2026-06-01T04:30:00Z", currency: str = "INR"
) -> StandInData:
    """A small, deterministic store: ``orders`` orders one hour apart, 10 customers, 4 products."""
    products = [
        (8000000001, "Wireless Earbuds", "Electronics", 1699, 1257.26),
        (8000000002, "Cotton Kurta", "Fashion", 1299, 545.58),
        (8000000003, "Bedsheet Set", "Home & Kitchen", 689, 378.95),
        (8000000004, "Face Serum", "Beauty", 549, 208.62),
    ]
    first = datetime.fromisoformat(start.replace("Z", "+00:00"))
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    data = StandInData(currency=currency)
    for i in range(orders):
        product = products[i % len(products)]
        created = (first + timedelta(hours=i)).strftime(stamp)
        data.orders.append(
            order_node(
                5000000001 + i,
                created_at=created,
                customer=7000000001 + i % 10,
                channel=("web", "pos", "android")[i % 3],
                state=("Karnataka", "Maharashtra", "Delhi")[i % 3],
                lines=[
                    {
                        "product": product[0],
                        "title": product[1],
                        "type": product[2],
                        "sku": f"SKU-{product[0] % 100}",
                        "quantity": 1 + i % 2,
                        "price": product[3],
                    }
                ],
                discounts=50.0 if i % 4 == 0 else 0.0,
                refunds=100.0 if i % 9 == 0 else 0.0,
                currency=currency,
            )
        )
    for i in range(10):
        created = (first - timedelta(days=30 - i)).strftime(stamp)
        data.customers.append(
            {
                "id": _gid("Customer", 7000000001 + i),
                "createdAt": created,
                "updatedAt": created,
                "numberOfOrders": str(orders // 10),
                "amountSpent": _money(1000 + 250 * i, currency),
                "defaultAddress": {"city": "Pune", "province": "Maharashtra", "country": "India"},
                "emailMarketingConsent": {
                    "marketingState": "SUBSCRIBED" if i % 2 else "NOT_SUBSCRIBED"
                },
            }
        )
    _add_products(
        data,
        [
            (
                p[0],
                p[1],
                p[2],
                f"SKU-{p[0] % 100}",
                "UrbanCart",
                p[3],
                p[4],
                100,
                "2026-01-01T00:00:00Z",
            )
            for p in products
        ],
    )
    return data


def _add_products(data: StandInData, rows: list[tuple[Any, ...]]) -> None:
    for number, title, kind, sku, vendor, price, cost, stock, created in rows:
        product = {
            "id": _gid("Product", number),
            "title": title,
            "productType": kind,
            "vendor": vendor,
            "status": "ACTIVE",
            "createdAt": created,
            "updatedAt": created,
            "totalInventory": int(stock),
            "priceRangeV2": {
                "minVariantPrice": _money(price, data.currency),
                "maxVariantPrice": _money(price, data.currency),
            },
        }
        data.products.append(product)
        data.variants.append(
            {
                "id": _gid("ProductVariant", int(number) + 1_000_000_000),
                "title": "Default Title",
                "sku": sku,
                "price": str(round(float(price), 2)),
                "inventoryQuantity": int(stock),
                "createdAt": created,
                "updatedAt": created,
                "product": product,
                "inventoryItem": {"unitCost": _money(cost, data.currency)},
            }
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=2)
def demo_data(fixtures_dir: Path = FIXTURES_DIR) -> StandInData:
    """The demo company's Shopify data (one line item per order, as in ``shopify_orders.csv``)."""
    orders_csv = fixtures_dir / "shopify_orders.csv"
    if not orders_csv.exists():
        return sample_data()
    data = StandInData()
    for row in _read_csv(orders_csv):
        quantity = int(row["quantity"])
        node = order_node(
            int(row["id"]),
            created_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
            customer=int(row["customer_id"]) if row["customer_id"] else None,
            channel=row["sales_channel"],
            state=row["shipping_state"],
            city=row["shipping_city"],
            lines=[
                {
                    "product": int(row["product_id"]),
                    "title": row["product_title"],
                    "type": row["product_type"],
                    "sku": row["sku"],
                    "quantity": quantity,
                    "price": float(row["unit_price"]),
                }
            ],
            discounts=float(row["total_discounts"] or 0),
            refunds=float(row["total_refunds"] or 0),
            financial_status=row["financial_status"].upper(),
        )
        node["name"] = row["name"]
        node["paymentGatewayNames"] = [row["payment_gateway"]]
        node["displayFulfillmentStatus"] = (row["fulfillment_status"] or "unfulfilled").upper()
        node["totalPriceSet"] = _bag(row["total_price"])
        data.orders.append(node)
    customers_csv = fixtures_dir / "shopify_customers.csv"
    if customers_csv.exists():
        for row in _read_csv(customers_csv):
            created = _utc(row["created_at"])
            data.customers.append(
                {
                    "id": _gid("Customer", row["id"]),
                    "createdAt": created,
                    "updatedAt": created,
                    "numberOfOrders": row["orders_count"],
                    "amountSpent": _money(row["total_spent"]),
                    "defaultAddress": {
                        "city": row["city"],
                        "province": row["state"],
                        "country": "India",
                    },
                    "emailMarketingConsent": {
                        "marketingState": "SUBSCRIBED"
                        if row["accepts_marketing"].lower() == "true"
                        else "NOT_SUBSCRIBED"
                    },
                }
            )
    products_csv = fixtures_dir / "shopify_products.csv"
    if products_csv.exists():
        _add_products(
            data,
            [
                (
                    int(r["id"]),
                    r["title"],
                    r["product_type"],
                    r["sku"],
                    r["vendor"],
                    float(r["price"]),
                    float(r["cost_per_item"]),
                    int(r["inventory_quantity"]),
                    _utc(r["created_at"]),
                )
                for r in _read_csv(products_csv)
            ],
        )
    return data


# --------------------------------------------------------------------------------------- answering
_TERM = re.compile(r"(-?)(\w+):(>=|<=|>|<)?'?([^'\s]+)'?")
_CONNECTION_FIELDS = frozenset({"lineItems"})


def _matches(node: dict[str, Any], search: str | None) -> bool:
    for negate, key, op, value in _TERM.findall(search or ""):
        if key == "test":
            ok = bool(node.get("test", False)) == (value.lower() == "true")
        elif key in ("created_at", "updated_at"):
            stamp = str(node.get("createdAt" if key == "created_at" else "updatedAt", ""))
            ok = {
                ">=": stamp >= value,
                ">": stamp > value,
                "<=": stamp <= value,
                "<": stamp < value,
                "": stamp[: len(value)] == value,
            }[op]
        else:
            ok = True
        if bool(negate) == ok:
            return False
    return True


def _connection(items: list[Any], first: int | None, after: str | None) -> dict[str, Any]:
    start = int(after) + 1 if after else 0
    page = items[start : start + first] if first else items[start:]
    end = start + len(page)
    edges = [{"cursor": str(start + i), "node": node} for i, node in enumerate(page)]
    return {
        "edges": edges,
        "nodes": page,
        "pageInfo": {"hasNextPage": end < len(items), "endCursor": str(end - 1) if page else None},
    }


def _project(selection: SelectionSetNode | None, value: Any, variables: dict[str, Any]) -> Any:
    if value is None or selection is None:
        return value
    if isinstance(value, list):
        return [_project(selection, item, variables) for item in value]
    out: dict[str, Any] = {}
    for node in selection.selections:
        if not isinstance(node, FieldNode):
            raise GraphQLError("The stand-in does not support fragments.")
        name = node.name.value
        args = {
            a.name.value: value_from_ast_untyped(a.value, variables) for a in node.arguments or ()
        }
        raw = value.get(name) if isinstance(value, dict) else None
        if callable(raw):
            raw = raw(**args)
        elif name in _CONNECTION_FIELDS and isinstance(raw, list):
            raw = _connection(raw, args.get("first"), args.get("after"))
        out[node.alias.value if node.alias else name] = _project(node.selection_set, raw, variables)
    return out


def _flatten(result: dict[str, Any]) -> list[dict[str, Any]]:
    """A bulk file: each node on its own line; nodes of a nested connection carry ``__parentId``."""
    lines: list[dict[str, Any]] = []

    def emit(node: dict[str, Any], parent: str | None) -> None:
        own = {k: v for k, v in node.items() if not (isinstance(v, dict) and "edges" in v)}
        if parent is not None:
            own["__parentId"] = parent
        lines.append(own)
        for value in node.values():
            if isinstance(value, dict) and "edges" in value:
                for edge in value["edges"]:
                    emit(edge["node"], str(node.get("id")))

    for connection in result.values():
        for edge in (connection or {}).get("edges", []):
            emit(edge["node"], None)
    return lines


def _response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )


def issue_code(shop: str, scopes: str) -> str:
    payload = json.dumps({"shop": shop, "scope": scopes}).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _read_code(code: str) -> dict[str, str] | None:
    if not code.startswith(CODE_PREFIX):
        return None
    raw = code[len(CODE_PREFIX) :]
    try:
        value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class StandInShopifyTransport(httpx.BaseTransport):
    """Answers like Shopify. ``data`` defaults to the demo data; tests pass their own and use the hooks."""

    def __init__(
        self,
        data: StandInData | Callable[[], StandInData] | None = None,
        *,
        client_secret: str = STAND_IN_CLIENT_SECRET,
        polls_until_done: int = 1,
    ) -> None:
        self._data = data
        self._client_secret = client_secret
        self.polls_until_done = polls_until_done
        self.requests: list[tuple[str, str, dict[str, Any]]] = []  # (method, path, graphql payload)
        self.writes_received = 0
        self.queued: list[
            httpx.Response
        ] = []  # answered first, in order (tests: errors, throttling)
        self.bulk_started = 0
        self._bulk: dict[str, Any] | None = None
        self._files: dict[str, str] = {}
        self._used_codes: set[str] = set()
        self._lock = threading.Lock()

    @property
    def data(self) -> StandInData:
        if self._data is None:
            return demo_data()
        if callable(self._data):
            return self._data()
        return self._data

    # ------------------------------------------------------------------------------------- routing
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        with self._lock:
            if request.method == "GET" and path.startswith("/stand-in-bulk/"):
                self.requests.append(("GET", path, {}))
                text = self._files.get(path)
                return httpx.Response(200, text=text) if text is not None else _response(404, {})
            if request.method == "POST" and path == "/admin/oauth/access_token":
                return self._token(request)
            if request.method == "POST" and path.endswith("/graphql.json"):
                return self._graphql(request)
            self.writes_received += request.method not in ("GET", "HEAD")
            return _response(404, {"errors": "Not Found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        code = form.get("code", "")
        details = _read_code(code)
        if (
            form.get("client_secret") != self._client_secret
            or details is None
            or details.get("shop") != request.url.host
            or code in self._used_codes
        ):
            return _response(
                400, {"error": "invalid_request", "error_description": "The code is not valid."}
            )
        self._used_codes.add(code)
        token = f"{STAND_IN_TOKEN_PREFIX}{base64.b16encode(code[-12:].encode()).decode().lower()}"
        return _response(200, {"access_token": token, "scope": details.get("scope", "")})

    def _graphql(self, request: httpx.Request) -> httpx.Response:
        token = request.headers.get("x-shopify-access-token", "")
        try:
            payload = json.loads(request.content)
        except ValueError:
            return _response(400, {"errors": "Bad Request"})
        self.requests.append(("POST", request.url.path, payload))
        if self.queued:
            return self.queued.pop(0)
        if not token.startswith(STAND_IN_TOKEN_PREFIX) or token == REVOKED_TOKEN:
            return _response(
                401, {"errors": "[API] Invalid API key or access token (unrecognized login)"}
            )
        variables = payload.get("variables") or {}
        try:
            document = parse(str(payload.get("query", "")))
        except GraphQLError as error:
            return _response(200, {"errors": [{"message": error.message}]})
        problems = validate(schema(), document)
        if problems:
            return _response(200, {"errors": [{"message": p.message} for p in problems]})
        operation = next(d for d in document.definitions if isinstance(d, OperationDefinitionNode))
        if operation.operation is OperationType.MUTATION:
            return self._mutation(operation, variables)
        data = _project(operation.selection_set, self._root(), variables)
        return _response(200, {"data": data, "extensions": _cost()})

    # -------------------------------------------------------------------------------------- answers
    def _root(self) -> dict[str, Any]:
        data = self.data

        def listing(items: list[dict[str, Any]]) -> Callable[..., dict[str, Any]]:
            def resolve(
                first: int | None = None,
                after: str | None = None,
                query: str | None = None,
                sortKey: str | None = None,
                reverse: bool = False,
            ) -> dict[str, Any]:
                chosen = [n for n in items if _matches(n, query)]
                if sortKey == "UPDATED_AT":
                    chosen.sort(key=lambda n: (n.get("updatedAt", ""), n["id"]), reverse=reverse)
                elif reverse:
                    chosen.reverse()
                return _connection(chosen, first, after)

            return resolve

        def count(items: list[dict[str, Any]]) -> Callable[..., dict[str, Any]]:
            def resolve(query: str | None = None, limit: int | None = None) -> dict[str, Any]:
                return {"count": sum(1 for n in items if _matches(n, query)), "precision": "EXACT"}

            return resolve

        by_id = {o["id"]: o for o in data.orders}
        return {
            "shop": {
                "name": data.shop_name,
                "currencyCode": data.currency,
                "myshopifyDomain": "urbancart-demo.myshopify.com",
            },
            "orders": listing(data.orders),
            "order": lambda id: by_id.get(id),
            "customers": listing(data.customers),
            "products": listing(data.products),
            "productVariants": listing(data.variants),
            "ordersCount": count(data.orders),
            "customersCount": count(data.customers),
            "productsCount": count(data.products),
            "productVariantsCount": count(data.variants),
            "currentBulkOperation": lambda type="QUERY": self._poll(),
        }

    def _poll(self) -> dict[str, Any] | None:
        if self._bulk is None:
            return None
        if self._bulk["status"] in ("CREATED", "RUNNING"):
            self._bulk["polls"] += 1
            if self._bulk["polls"] >= self.polls_until_done:
                self._bulk["status"] = "COMPLETED"
            else:
                self._bulk["status"] = "RUNNING"
        return {k: v for k, v in self._bulk.items() if k != "polls"}

    def _mutation(
        self, operation: OperationDefinitionNode, variables: dict[str, Any]
    ) -> httpx.Response:
        field_ = operation.selection_set.selections[0]
        assert isinstance(field_, FieldNode)
        if field_.name.value != "bulkOperationRunQuery":
            self.writes_received += 1
            return _response(
                200,
                {
                    "errors": [
                        {
                            "message": "Refused by the stand-in.",
                            "extensions": {"code": "ACCESS_DENIED"},
                        }
                    ]
                },
            )
        args = {
            a.name.value: value_from_ast_untyped(a.value, variables) for a in field_.arguments or ()
        }
        if self._bulk is not None and self._bulk["status"] in ("CREATED", "RUNNING"):
            payload = {
                "bulkOperation": None,
                "userErrors": [
                    {
                        "field": None,
                        "message": "A bulk query operation for this app and shop is already in progress: "
                        f"{self._bulk['id']}.",
                    }
                ],
            }
            return _response(
                200, {"data": {"bulkOperationRunQuery": payload}, "extensions": _cost()}
            )
        inner: DocumentNode = parse(str(args.get("query", "")))
        inner_op = next(d for d in inner.definitions if isinstance(d, OperationDefinitionNode))
        if inner_op.operation is not OperationType.QUERY or validate(schema(), inner):
            refused: dict[str, Any] = {
                "bulkOperation": None,
                "userErrors": [{"field": ["query"], "message": "Invalid bulk query."}],
            }
            return _response(
                200, {"data": {"bulkOperationRunQuery": refused}, "extensions": _cost()}
            )
        self.bulk_started += 1
        lines = _flatten(_project(inner_op.selection_set, self._root(), {}))
        number = self.bulk_started
        path = f"/stand-in-bulk/{number}.jsonl"
        self._files[path] = "".join(json.dumps(line) + "\n" for line in lines)
        self._bulk = {
            "id": _gid("BulkOperation", number),
            "status": "CREATED",
            "errorCode": None,
            "objectCount": str(len(lines)),
            "url": f"https://storage.stand-in.invalid{path}" if lines else None,
            "partialDataUrl": None,
            "type": "QUERY",
            "createdAt": "2026-11-16T00:00:00Z",
            "polls": 0,
        }
        view = {k: self._bulk[k] for k in ("id", "status")}
        return _response(
            200,
            {
                "data": {"bulkOperationRunQuery": {"bulkOperation": view, "userErrors": []}},
                "extensions": _cost(),
            },
        )


def _cost() -> dict[str, Any]:
    return {
        "cost": {
            "requestedQueryCost": 12,
            "actualQueryCost": 10,
            "throttleStatus": {
                "maximumAvailable": 2000.0,
                "currentlyAvailable": 1990,
                "restoreRate": 100.0,
            },
        }
    }


def throttled() -> httpx.Response:
    """Shopify's answer when the cost bucket is empty (tests)."""
    return _response(
        200,
        {
            "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
            "extensions": {
                "cost": {
                    "requestedQueryCost": 300,
                    "actualQueryCost": None,
                    "throttleStatus": {
                        "maximumAvailable": 2000.0,
                        "currentlyAvailable": 20,
                        "restoreRate": 100.0,
                    },
                }
            },
        },
    )


def signed_callback(
    shop: str, state: str, code: str, secret: str, timestamp: int
) -> dict[str, str]:
    """The query string Shopify adds to the redirect after the merchant approves, with its HMAC."""
    from app.connectors.shopify import callback_hmac

    params = {
        "code": code,
        "host": base64.urlsafe_b64encode(f"admin.shopify.com/store/{shop.split('.')[0]}".encode())
        .decode()
        .rstrip("="),
        "shop": shop,
        "state": state,
        "timestamp": str(timestamp),
    }
    params["hmac"] = callback_hmac(secret, params)
    return params
