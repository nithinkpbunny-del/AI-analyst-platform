"""The Shopify connector (BUILD_SPEC 8.5): orders, order items, customers, products and variants, read-only.

* **Sign-in**: the OAuth authorization-code flow of a Shopify app (custom or public distribution) with the scopes
  ``read_orders``, ``read_products``, ``read_customers``, ``read_inventory`` (and ``read_all_orders`` when Shopify
  approved it: without it Shopify returns only the last 60 days of orders). The callback is checked with Shopify's
  HMAC; the granted scopes are checked after the token exchange and anything that is not a ``read_`` scope is
  refused (BUILD_SPEC 6.3, layer 1). The offline access token is stored encrypted; the shop address is not a
  secret and stays in the connection's config.
* **Reading**: the Admin **GraphQL** API, queries only. Every document is parsed before it is sent
  (``app.connectors.graphql_guard``) and the HTTP client parses the request body again, so a mutation cannot leave
  this process. The single exception is Shopify's ``bulkOperationRunQuery`` (a read job on Shopify's side), in its
  exact shape only (DECISIONS D93).
* **Large histories**: a first read of a big table starts a bulk operation (one export file, no paging) and
  streams its lines; small tables and incremental reads use cursor-paged queries. Paged reads follow Shopify's
  cost-based limits: they watch the bucket the answer reports and wait when it runs low (DECISIONS D94).
* **Money is in rupees** (a decimal in the shop's currency). The source says so (``ColumnInfo.stored_in``) and the
  dictionary does not divide anything, the opposite of Razorpay's paise (DECISIONS D90, D91, D95). A shop that
  sells in another currency leaves the unit for the Owner.
* **Incremental**: the cursor is the newest ``updated_at`` seen; the next read asks for rows updated since then
  (minus a small overlap) and they replace their earlier copies by ``id``.
* **Personal data**: names, e-mail addresses, phone numbers and street addresses are never requested (data
  minimisation). Customers are read with their city and state only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
import pandas as pd

from app.config import Settings
from app.connectors import graphql_guard
from app.connectors.base import (
    ConnectionTestResult,
    ConnectorError,
    DatasetInfo,
    FetchResult,
    TestCheck,
)
from app.connectors.http import ReadOnlyHttpClient
from app.connectors.tabular import ColumnInfo, ColumnType

logger = logging.getLogger(__name__)

API_VERSION = "2026-07"
DEFAULT_SCOPES = ("read_orders", "read_products", "read_customers", "read_inventory")
REQUIRED_SCOPES = frozenset({"read_orders", "read_products", "read_customers"})
ALL_ORDERS_SCOPE = "read_all_orders"
INVENTORY_SCOPE = "read_inventory"
HTTP_TIMEOUT_SECONDS = 60.0
# A read starts this long before the newest ``updated_at`` seen, so a row saved at the same moment is not missed.
OVERLAP = timedelta(hours=1)
LINE_ITEMS_PAGE = 10
MAX_PAGES_PER_FETCH = 20_000
MAX_THROTTLE_WAITS = 6
SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
_ADMIN_URL = re.compile(r"admin\.shopify\.com/store/([a-z0-9][a-z0-9-]{0,60})", re.IGNORECASE)
REAUTH_MESSAGE = "Shopify stopped sharing data. Sign in to Shopify again to reconnect."

Mode = Literal["shopify", "stand_in", "off"]


# ------------------------------------------------------------------------------------------- the shop
def normalize_shop(raw: str) -> str | None:
    """``my-store``, ``my-store.myshopify.com``, a pasted admin address: the ``*.myshopify.com`` host, or None."""
    text = raw.strip().lower()
    if not text or len(text) > 200:
        return None
    admin = _ADMIN_URL.search(text)
    if admin:
        text = f"{admin.group(1)}.myshopify.com"
    elif "//" in text:
        text = urlsplit(text).netloc
    else:
        text = text.split("/")[0]
    if "." not in text:
        text = f"{text}.myshopify.com"
    return text if SHOP_DOMAIN.match(text) else None


def _scope_list(value: str) -> frozenset[str]:
    return frozenset(s.strip() for s in re.split(r"[,\s]+", value) if s.strip())


def _ordered_scopes(value: str) -> tuple[str, ...]:
    """The configured scopes in their written order, without repeats; the defaults when none are set. Only read
    scopes are ever requested: anything else in the setting is dropped (BUILD_SPEC 6.3, layer 1)."""
    words = (s.strip() for s in re.split(r"[,\s]+", value) if s.strip())
    scopes = tuple(dict.fromkeys(s for s in words if s.startswith("read_")))
    return scopes or DEFAULT_SCOPES


@dataclass(frozen=True)
class ShopifySetup:
    """Where Shopify is and how the app is registered. Tests and the development stand-in replace the transport."""

    mode: Mode = "off"
    client_id: str = ""
    client_secret: str = ""
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    api_version: str = API_VERSION
    api_base_url: str = "http://localhost:8000"
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] | None = None
    history_days: int = 730
    # A first read of a table with more rows than this uses a bulk operation.
    bulk_threshold: int = 1000
    bulk_timeout_seconds: int = 900
    bulk_poll_seconds: float = 3.0

    @property
    def available(self) -> bool:
        return self.mode != "off"

    @property
    def stand_in(self) -> bool:
        return self.mode == "stand_in"

    def redirect_uri(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/connections/shopify/callback"

    def graphql_url(self, shop: str) -> str:
        return f"https://{shop}/admin/api/{self.api_version}/graphql.json"

    def token_url(self, shop: str) -> str:
        return f"https://{shop}/admin/oauth/access_token"

    def http(self, shop: str, *, purpose: Literal["api", "oauth"] = "api") -> ReadOnlyHttpClient:
        client = httpx.Client(transport=self.transport, timeout=HTTP_TIMEOUT_SECONDS)
        kwargs: dict[str, Any] = {}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        if purpose == "oauth":
            return ReadOnlyHttpClient(client, allowed_posts=(self.token_url(shop),), **kwargs)
        return ReadOnlyHttpClient(
            client,
            allowed_posts=(self.graphql_url(shop),),
            post_guard=_graphql_body_guard,
            **kwargs,
        )


def _graphql_body_guard(request: httpx.Request) -> None:
    """Second check, in the HTTP client: a POST to a GraphQL endpoint must hold a query (or the bulk read)."""
    if request.url.path.endswith("/graphql.json"):
        graphql_guard.validate_body(request.content)


def default_setup(settings: Settings) -> ShopifySetup:
    """Real Shopify when the app's key and secret are set; the local stand-in in development; otherwise off."""
    common: dict[str, Any] = {
        "scopes": _ordered_scopes(settings.shopify_scopes),
        "api_version": settings.shopify_api_version,
        "api_base_url": settings.api_base_url,
        "history_days": settings.shopify_history_days,
        "bulk_threshold": settings.shopify_bulk_threshold,
        "bulk_timeout_seconds": settings.shopify_bulk_timeout_seconds,
    }
    secret = settings.shopify_api_secret.get_secret_value()
    if settings.shopify_api_key and secret:
        return ShopifySetup(
            mode="shopify", client_id=settings.shopify_api_key, client_secret=secret, **common
        )
    if settings.app_env == "development":
        from app.connectors.shopify_stand_in import (
            STAND_IN_CLIENT_ID,
            STAND_IN_CLIENT_SECRET,
            StandInShopifyTransport,
        )

        return ShopifySetup(
            mode="stand_in",
            client_id=STAND_IN_CLIENT_ID,
            client_secret=STAND_IN_CLIENT_SECRET,
            transport=StandInShopifyTransport(),
            bulk_poll_seconds=0.2,
            **common,
        )
    return ShopifySetup(mode="off", **common)


# ------------------------------------------------------------------------------------------------ OAuth
def authorization_url(setup: ShopifySetup, *, shop: str, state: str) -> str:
    params = {
        "client_id": setup.client_id,
        "scope": ",".join(setup.scopes),
        "redirect_uri": setup.redirect_uri(),
        "state": state,
    }
    if setup.stand_in:
        return f"{setup.api_base_url.rstrip('/')}/connections/shopify/stand-in?" + urlencode(
            {**params, "shop": shop}
        )
    return f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"


def callback_hmac(secret: str, params: dict[str, str]) -> str:
    message = "&".join(f"{key}={params[key]}" for key in sorted(params) if key != "hmac")
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_callback(setup: ShopifySetup, params: dict[str, str]) -> bool:
    """Shopify signs the callback's query string with the app secret; only a request it sent passes."""
    given = params.get("hmac", "")
    return bool(given) and hmac.compare_digest(given, callback_hmac(setup.client_secret, params))


@dataclass(frozen=True)
class ShopifyToken:
    access_token: str
    scopes: frozenset[str]


def check_scopes(scopes: frozenset[str]) -> None:
    extra = sorted(s for s in scopes if not s.startswith("read_"))
    if extra:
        raise ConnectorError(
            "invalid",
            "Shopify gave this app more access than it asks for. Connect again and allow only reading.",
            detail=f"unexpected scopes: {extra}",
        )
    missing = REQUIRED_SCOPES - scopes
    if missing:
        raise ConnectorError(
            "invalid",
            "Shopify did not allow reading orders, products and customers. Connect again and approve "
            "the access the app asks for.",
            detail=f"missing scopes: {sorted(missing)}",
        )


def exchange_code(
    setup: ShopifySetup, http: ReadOnlyHttpClient, *, shop: str, code: str
) -> ShopifyToken:
    response = http.post_form(
        setup.token_url(shop),
        {"client_id": setup.client_id, "client_secret": setup.client_secret, "code": code},
    )
    if response.status_code != 200:
        if response.status_code in (400, 401, 403):
            raise ConnectorError(
                "invalid",
                "Shopify did not accept the sign-in. Start again with 'Connect Shopify'.",
                detail=f"token endpoint: HTTP {response.status_code}",
            )
        if response.status_code == 429:
            raise ConnectorError(
                "rate_limited", "Shopify is limiting requests right now. Try again in a minute."
            )
        raise ConnectorError(
            "unavailable",
            "Shopify did not answer as expected. Try again in a few minutes.",
            detail=f"token endpoint: HTTP {response.status_code}",
        )
    try:
        body = response.json()
        token = str(body["access_token"])
        scopes = _scope_list(str(body.get("scope", "")))
    except (ValueError, KeyError, TypeError) as error:
        raise ConnectorError(
            "unavailable", "Shopify sent an answer that could not be read."
        ) from error
    check_scopes(scopes)
    return ShopifyToken(access_token=token, scopes=scopes)


# ------------------------------------------------------------------------------------------ the tables
@dataclass(frozen=True)
class Field:
    name: str
    type: ColumnType
    money: bool = False  # a money amount, in the shop's currency (rupees for an Indian shop)


@dataclass(frozen=True)
class RowContext:
    currency: str | None


def _tail(gid: Any) -> str | None:
    """``gid://shopify/Order/5000000001`` -> ``5000000001``."""
    if gid is None:
        return None
    return str(gid).rsplit("/", 1)[-1].split("?")[0]


def _amount(node: dict[str, Any], key: str) -> Any:
    money = node.get(key)
    if not isinstance(money, dict):
        return None
    shop_money = money.get("shopMoney")
    return shop_money.get("amount") if isinstance(shop_money, dict) else None


def _plain_amount(value: Any) -> Any:
    return value.get("amount") if isinstance(value, dict) else value


def _lower(value: Any) -> Any:
    return str(value).lower() if value is not None else None


def _orders_rows(node: dict[str, Any], ctx: RowContext) -> list[dict[str, Any]]:
    shipping = node.get("shippingAddress") or {}
    customer = node.get("customer") or {}
    gateways = node.get("paymentGatewayNames") or []
    subtotal = _to_float(_amount(node, "subtotalPriceSet"))
    discounts = _to_float(_amount(node, "totalDiscountsSet"))
    return [
        {
            "id": _tail(node.get("id")),
            "name": node.get("name"),
            "created_at": node.get("createdAt"),
            "updated_at": node.get("updatedAt"),
            "cancelled_at": node.get("cancelledAt"),
            "customer_id": _tail(customer.get("id")),
            "sales_channel": node.get("sourceName"),
            "financial_status": _lower(node.get("displayFinancialStatus")),
            "fulfillment_status": _lower(node.get("displayFulfillmentStatus")),
            "payment_gateway": gateways[0] if gateways else None,
            "currency": _currency(node) or ctx.currency,
            "items_count": node.get("subtotalLineItemsQuantity"),
            "total_line_items_price": None
            if subtotal is None
            else round(subtotal + (discounts or 0.0), 2),
            "total_discounts": _amount(node, "totalDiscountsSet"),
            "total_shipping": _amount(node, "totalShippingPriceSet"),
            "total_tax": _amount(node, "totalTaxSet"),
            "total_price": _amount(node, "totalPriceSet"),
            "total_refunds": _amount(node, "totalRefundedSet"),
            "shipping_city": shipping.get("city"),
            "shipping_state": shipping.get("province"),
            "shipping_country": shipping.get("country"),
        }
    ]


def _currency(node: dict[str, Any]) -> str | None:
    money = (node.get("totalPriceSet") or {}).get("shopMoney") or {}
    code = money.get("currencyCode")
    return str(code) if code else None


def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _order_items_rows(node: dict[str, Any], ctx: RowContext) -> list[dict[str, Any]]:
    rows = []
    for edge in (node.get("lineItems") or {}).get("edges", []):
        item = edge.get("node") or {}
        product = item.get("product") or {}
        variant = item.get("variant") or {}
        rows.append(
            {
                "id": _tail(item.get("id")),
                "order_id": _tail(node.get("id")),
                "created_at": node.get("createdAt"),
                "updated_at": node.get("updatedAt"),
                "product_id": _tail(product.get("id")),
                "variant_id": _tail(variant.get("id")),
                "product_title": item.get("title"),
                "product_type": product.get("productType") or None,
                "sku": item.get("sku") or None,
                "quantity": item.get("quantity"),
                "unit_price": _amount(item, "originalUnitPriceSet"),
                "total_discount": _amount(item, "totalDiscountSet"),
            }
        )
    return rows


def _customers_rows(node: dict[str, Any], ctx: RowContext) -> list[dict[str, Any]]:
    address = node.get("defaultAddress") or {}
    consent = node.get("emailMarketingConsent") or {}
    return [
        {
            "id": _tail(node.get("id")),
            "created_at": node.get("createdAt"),
            "updated_at": node.get("updatedAt"),
            "city": address.get("city"),
            "state": address.get("province"),
            "country": address.get("country"),
            "accepts_marketing": None
            if not consent
            else consent.get("marketingState") == "SUBSCRIBED",
            "orders_count": node.get("numberOfOrders"),
            "total_spent": _plain_amount(node.get("amountSpent")),
        }
    ]


def _products_rows(node: dict[str, Any], ctx: RowContext) -> list[dict[str, Any]]:
    prices = node.get("priceRangeV2") or {}
    return [
        {
            "id": _tail(node.get("id")),
            "title": node.get("title"),
            "product_type": node.get("productType") or None,
            "vendor": node.get("vendor") or None,
            "status": _lower(node.get("status")),
            "created_at": node.get("createdAt"),
            "updated_at": node.get("updatedAt"),
            "price": _plain_amount(prices.get("minVariantPrice")),
            "price_max": _plain_amount(prices.get("maxVariantPrice")),
            "inventory_quantity": node.get("totalInventory"),
        }
    ]


def _variants_rows(node: dict[str, Any], ctx: RowContext) -> list[dict[str, Any]]:
    unit_cost = ((node.get("inventoryItem") or {}).get("unitCost")) or {}
    return [
        {
            "id": _tail(node.get("id")),
            "product_id": _tail((node.get("product") or {}).get("id")),
            "title": node.get("title"),
            "sku": node.get("sku") or None,
            "price": node.get("price"),
            "cost_per_item": unit_cost.get("amount"),
            "inventory_quantity": node.get("inventoryQuantity"),
            "created_at": node.get("createdAt"),
            "updated_at": node.get("updatedAt"),
        }
    ]


_MONEY = "{ shopMoney { amount currencyCode } }"


@dataclass(frozen=True)
class Table:
    ref: str
    root: str  # the root connection field
    count_field: str
    node: str  # the fields of one node; ``{PAGE_INFO}`` and ``{LINE_ARGS}`` differ between paged and bulk reads
    fields: tuple[Field, ...]
    to_rows: Callable[[dict[str, Any], RowContext], list[dict[str, Any]]]
    page_size: int
    sort_updated: bool = True  # the root field can be sorted by ``UPDATED_AT``
    base_search: str = ""  # always part of the search (e.g. no test orders)
    created_filter: bool = False  # a first read is limited to ``history_days`` (orders only)
    needs_scope: str | None = None


ORDERS = Table(
    "orders",
    "orders",
    "ordersCount",
    f"""id name createdAt updatedAt cancelledAt sourceName displayFinancialStatus displayFulfillmentStatus
        paymentGatewayNames subtotalLineItemsQuantity
        customer {{ id }}
        shippingAddress {{ city province country }}
        totalPriceSet {_MONEY} subtotalPriceSet {_MONEY} totalDiscountsSet {_MONEY}
        totalRefundedSet {_MONEY} totalShippingPriceSet {_MONEY} totalTaxSet {_MONEY}""",
    (
        Field("id", "text"),
        Field("name", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
        Field("cancelled_at", "datetime"),
        Field("customer_id", "text"),
        Field("sales_channel", "text"),
        Field("financial_status", "text"),
        Field("fulfillment_status", "text"),
        Field("payment_gateway", "text"),
        Field("currency", "text"),
        Field("items_count", "integer"),
        Field("total_line_items_price", "number", money=True),
        Field("total_discounts", "number", money=True),
        Field("total_shipping", "number", money=True),
        Field("total_tax", "number", money=True),
        Field("total_price", "number", money=True),
        Field("total_refunds", "number", money=True),
        Field("shipping_city", "text"),
        Field("shipping_state", "text"),
        Field("shipping_country", "text"),
    ),
    _orders_rows,
    page_size=25,
    base_search="test:false",
    created_filter=True,
)
ORDER_ITEMS = Table(
    "order_items",
    "orders",
    "ordersCount",
    f"""id createdAt updatedAt
        lineItems{{LINE_ARGS}} {{ {{PAGE_INFO}}
          edges {{ node {{ id title sku quantity
            originalUnitPriceSet {_MONEY} totalDiscountSet {_MONEY}
            product {{ id productType }} variant {{ id }} }} }} }}""",
    (
        Field("id", "text"),
        Field("order_id", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
        Field("product_id", "text"),
        Field("variant_id", "text"),
        Field("product_title", "text"),
        Field("product_type", "text"),
        Field("sku", "text"),
        Field("quantity", "integer"),
        Field("unit_price", "number", money=True),
        Field("total_discount", "number", money=True),
    ),
    _order_items_rows,
    page_size=8,
    base_search="test:false",
    created_filter=True,
)
CUSTOMERS = Table(
    "customers",
    "customers",
    "customersCount",
    """id createdAt updatedAt numberOfOrders amountSpent { amount currencyCode }
        defaultAddress { city province country } emailMarketingConsent { marketingState }""",
    (
        Field("id", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
        Field("city", "text"),
        Field("state", "text"),
        Field("country", "text"),
        Field("accepts_marketing", "boolean"),
        Field("orders_count", "integer"),
        Field("total_spent", "number", money=True),
    ),
    _customers_rows,
    page_size=50,
)
PRODUCTS = Table(
    "products",
    "products",
    "productsCount",
    """id title productType vendor status createdAt updatedAt totalInventory
        priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount currencyCode } }""",
    (
        Field("id", "text"),
        Field("title", "text"),
        Field("product_type", "text"),
        Field("vendor", "text"),
        Field("status", "text"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
        Field("price", "number", money=True),
        Field("price_max", "number", money=True),
        Field("inventory_quantity", "integer"),
    ),
    _products_rows,
    page_size=50,
)
VARIANTS = Table(
    "variants",
    "productVariants",
    "productVariantsCount",
    """id title sku price inventoryQuantity createdAt updatedAt product { id }
        {INVENTORY}""",
    (
        Field("id", "text"),
        Field("product_id", "text"),
        Field("title", "text"),
        Field("sku", "text"),
        Field("price", "number", money=True),
        Field("cost_per_item", "number", money=True),
        Field("inventory_quantity", "integer"),
        Field("created_at", "datetime"),
        Field("updated_at", "datetime"),
    ),
    _variants_rows,
    page_size=50,
    sort_updated=False,
)
TABLES: dict[str, Table] = {t.ref: t for t in (ORDERS, ORDER_ITEMS, CUSTOMERS, PRODUCTS, VARIANTS)}
# A bulk file lists a child node after its parent; this names the connection a child belongs to.
CHILD_CONNECTIONS = {"LineItem": "lineItems"}


def _column(values: list[Any], field_: Field, timezone: str) -> pd.Series:
    if field_.type == "datetime":
        parsed = pd.to_datetime(pd.Series(values, dtype="object"), utc=True, errors="coerce")
        return parsed.dt.tz_convert(timezone).dt.tz_localize(None)
    if field_.type == "integer":
        numbers = pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce")
        return numbers.round().astype("Int64")
    if field_.type == "number":
        return pd.to_numeric(pd.Series(values, dtype="object"), errors="coerce").astype("float64")
    if field_.type == "boolean":
        return (
            pd.Series(values, dtype="object")
            .map(lambda v: None if v is None else bool(v))
            .astype("boolean")
        )
    return pd.Series([None if v is None else str(v) for v in values], dtype="string")


def to_frame(table: Table, rows: list[dict[str, Any]], timezone: str) -> pd.DataFrame:
    return pd.DataFrame(
        {f.name: _column([r.get(f.name) for r in rows], f, timezone) for f in table.fields}
    )


def column_infos(table: Table, frame: pd.DataFrame, currency: str | None) -> list[ColumnInfo]:
    rupees = currency in (None, "INR")
    infos = []
    for f in table.fields:
        infos.append(
            ColumnInfo(
                name=f.name,
                label=f.name,
                type=f.type,
                # Shopify sends rupees (not paise): the unit is known, nothing is divided (D95).
                unit="INR" if f.money and rupees else None,
                stored_in="rupees" if f.money and rupees else None,
                currencies=(currency,) if f.money and not rupees and currency else (),
                date_format="ISO 8601" if f.type == "datetime" else None,
                null_count=int(frame[f.name].isna().sum()),
            )
        )
    return infos


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------ connector
class ShopifyConnector:
    type = "shopify"

    def __init__(
        self,
        *,
        setup: ShopifySetup,
        shop: str,
        access_token: str,
        scopes: frozenset[str],
        timezone: str,
        max_rows: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._setup = setup
        self._shop = shop
        self._token = access_token
        self._scopes = scopes
        self._timezone = timezone
        self._max_rows = max_rows
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = setup.sleep or time.sleep
        self._http = setup.http(shop)
        # Shopify's cost bucket as the last answer reported it.
        self._available: float | None = None
        self._restore_rate = 50.0
        self._last_cost = 0.0
        self._currency: str | None = None
        self._currency_known = False

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------------------------ requests
    def _pace(self) -> None:
        """Wait while the cost bucket cannot pay for another request like the last one."""
        if self._available is not None and self._available < self._last_cost:
            self._sleep(min(30.0, (self._last_cost - self._available) / self._restore_rate))
            self._available = self._last_cost

    def _note_cost(self, extensions: Any) -> None:
        cost = (extensions or {}).get("cost") if isinstance(extensions, dict) else None
        if not isinstance(cost, dict):
            return
        status = cost.get("throttleStatus") or {}
        try:
            self._available = float(status["currentlyAvailable"])
            self._restore_rate = max(1.0, float(status.get("restoreRate", 50)))
            self._last_cost = float(
                cost.get("actualQueryCost") or cost.get("requestedQueryCost") or 0
            )
        except (KeyError, TypeError, ValueError):
            return

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        graphql_guard.validate_request(
            query, variables
        )  # the first check; the HTTP client makes the second
        url = self._setup.graphql_url(self._shop)
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        for _ in range(MAX_THROTTLE_WAITS):
            self._pace()
            try:
                response = self._http.request(
                    "POST",
                    url,
                    json=payload,
                    headers={"X-Shopify-Access-Token": self._token},
                )
            except httpx.HTTPError as error:
                raise ConnectorError(
                    "unavailable",
                    "Shopify could not be reached. Try again in a few minutes.",
                    detail=type(error).__name__,
                ) from error
            body = self._body(response)
            self._note_cost(body.get("extensions"))
            errors = body.get("errors")
            if not errors:
                data = body.get("data")
                if not isinstance(data, dict):
                    raise ConnectorError(
                        "unavailable", "Shopify sent an answer that could not be read."
                    )
                return data
            codes = _error_codes(errors)
            if "THROTTLED" in codes:
                wait = max(1.0, self._last_cost / self._restore_rate)
                self._sleep(min(30.0, wait))
                continue
            if "ACCESS_DENIED" in codes:
                raise ConnectorError(
                    "invalid",
                    "Shopify did not allow reading this data. Check that the app has access to orders, "
                    "products and customers (and to customer data for public apps).",
                    detail=f"graphql errors: {sorted(codes)}",
                )
            raise ConnectorError(
                "invalid",
                "Shopify refused the request for this data.",
                detail=f"graphql errors: {sorted(codes)}",
            )
        raise ConnectorError(
            "rate_limited", "Shopify is limiting requests. Try again in a few minutes."
        )

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        detail = f"HTTP {status}"
        if status == 200:
            try:
                body = response.json()
            except ValueError as error:
                raise ConnectorError(
                    "unavailable", "Shopify sent an answer that could not be read."
                ) from error
            if isinstance(body, dict):
                return body
            raise ConnectorError("unavailable", "Shopify sent an answer that could not be read.")
        if status in (401, 403):
            raise ConnectorError("needs_reauth", REAUTH_MESSAGE, detail=detail)
        if status == 404:
            raise ConnectorError(
                "not_found",
                "Shopify cannot find this store. Check the store address, or whether the store was closed.",
                detail=detail,
            )
        if status == 402:
            raise ConnectorError(
                "unavailable",
                "Shopify says this store is not active (payment is due). Reopen the store, then try again.",
                detail=detail,
            )
        if status == 429:
            raise ConnectorError(
                "rate_limited",
                "Shopify is limiting requests. Try again in a few minutes.",
                detail=detail,
            )
        raise ConnectorError(
            "unavailable",
            "Shopify is not answering properly. Try again in a few minutes.",
            detail=detail,
        )

    # --------------------------------------------------------------------------------- the interface
    def test(self) -> ConnectionTestResult:
        checks: list[TestCheck] = []
        try:
            data = self._graphql("{ shop { name currencyCode myshopifyDomain } }")
            self._graphql("{ orders(first: 1) { edges { node { id } } } }")
        except ConnectorError as error:
            checks.append(TestCheck("signed_in", error.kind != "needs_reauth"))
            checks.append(TestCheck("orders_readable", False))
            return ConnectionTestResult(ok=False, message=error.message, checks=checks)
        shop = data.get("shop") or {}
        all_orders = ALL_ORDERS_SCOPE in self._scopes
        checks += [
            TestCheck("signed_in", True),
            TestCheck("read_only", True),
            TestCheck("orders_readable", True),
            TestCheck("all_orders", all_orders),
        ]
        name = shop.get("name") or self._shop
        message = f"Shopify accepted the access to {name}. This platform only reads."
        if not all_orders:
            message += " Orders older than 60 days are not available without Shopify's 'all orders' access."
        return ConnectionTestResult(
            ok=True,
            message=message,
            checks=checks,
            details={
                "shop_name": shop.get("name"),
                "currency": shop.get("currencyCode"),
                "all_orders": all_orders,
            },
        )

    def list_datasets(self) -> list[DatasetInfo]:
        return [DatasetInfo(ref=t.ref, name=t.ref) for t in TABLES.values()]

    def source_version(self, dataset: DatasetInfo) -> str | None:
        return None  # Shopify cannot say "nothing changed"; the cursor makes the next read small

    def supports_incremental(self) -> bool:
        return True

    def supports_pushdown(self) -> bool:
        return False

    def shop_currency(self) -> str | None:
        if not self._currency_known:
            data = self._graphql("{ shop { currencyCode } }")
            code = (data.get("shop") or {}).get("currencyCode")
            self._currency = str(code) if code else None
            self._currency_known = True
        return self._currency

    def fetch(
        self,
        dataset: DatasetInfo,
        since_cursor: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> FetchResult:
        table = TABLES.get(dataset.ref)
        if table is None:
            raise ConnectorError("not_found", "This Shopify table does not exist.")
        currency = self.shop_currency()
        since = _parse_cursor(since_cursor)
        incremental = since is not None
        search = self._search(table, since)
        nodes, truncated = self._read_nodes(table, search, incremental=incremental)
        ctx = RowContext(currency=currency)
        rows = [row for node in nodes for row in table.to_rows(node, ctx)]
        frame = to_frame(table, rows, self._timezone)
        newest = max((str(n["updatedAt"]) for n in nodes if n.get("updatedAt")), default=None)
        cursor = newest or since_cursor
        return FetchResult(
            frame=frame,
            columns=column_infos(table, frame, currency),
            source_version=None,
            cursor=cursor,
            incremental=incremental,
            key_column="id",
            truncated=truncated,
        )

    # ------------------------------------------------------------------------------------- reading
    def _search(self, table: Table, since: datetime | None) -> str:
        parts = [table.base_search] if table.base_search else []
        if since is not None:
            parts.append(f"updated_at:>='{_iso(since - OVERLAP)}'")
        elif table.created_filter:
            start = self._clock() - timedelta(days=self._setup.history_days)
            parts.append(f"created_at:>='{_iso(start)}'")
        return " ".join(parts)

    def _node_text(self, table: Table, *, bulk: bool) -> str:
        text = table.node
        text = text.replace("{LINE_ARGS}", "" if bulk else f"(first: {LINE_ITEMS_PAGE})")
        text = text.replace("{PAGE_INFO}", "" if bulk else "pageInfo { hasNextPage endCursor }")
        inventory = (
            "inventoryItem { unitCost { amount currencyCode } }"
            if INVENTORY_SCOPE in self._scopes
            else ""
        )
        return text.replace("{INVENTORY}", inventory)

    def _count(self, table: Table, search: str) -> int | None:
        try:
            data = self._graphql(
                f"query($q: String) {{ {table.count_field}(query: $q, limit: null) {{ count }} }}",
                {"q": search or None},
            )
        except ConnectorError as error:
            if error.kind in ("needs_reauth", "rate_limited", "unavailable"):
                raise
            return None
        count = (data.get(table.count_field) or {}).get("count")
        return int(count) if isinstance(count, int) else None

    def _read_nodes(
        self, table: Table, search: str, *, incremental: bool
    ) -> tuple[list[dict[str, Any]], bool]:
        use_bulk = False
        if not incremental:
            count = self._count(table, search)
            use_bulk = count is not None and count > self._setup.bulk_threshold
        if use_bulk:
            return self._read_bulk(table, search)
        return self._read_paged(table, search)

    def _paged_query(self, table: Table) -> str:
        sort = ", sortKey: UPDATED_AT, reverse: true" if table.sort_updated else ""
        return (
            "query($first: Int!, $after: String, $q: String) { "
            f"{table.root}(first: $first, after: $after, query: $q{sort}) {{ "
            "pageInfo { hasNextPage endCursor } "
            f"edges {{ node {{ {self._node_text(table, bulk=False)} }} }} }} }}"
        )

    def _read_paged(self, table: Table, search: str) -> tuple[list[dict[str, Any]], bool]:
        query = self._paged_query(table)
        nodes: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(MAX_PAGES_PER_FETCH):
            data = self._graphql(
                query, {"first": table.page_size, "after": after, "q": search or None}
            )
            connection = data.get(table.root) or {}
            for edge in connection.get("edges") or []:
                node = edge.get("node")
                if isinstance(node, dict):
                    if table.ref == "order_items":
                        self._complete_line_items(node)
                    nodes.append(node)
            if len(nodes) >= self._max_rows:
                return nodes[: self._max_rows], True
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return nodes, False
            after = page.get("endCursor")
            if not after:
                return nodes, False
        return nodes, True

    def _complete_line_items(self, order: dict[str, Any]) -> None:
        """An order with more line items than one page holds: read the rest (one small query each)."""
        items = order.get("lineItems") or {}
        page = items.get("pageInfo") or {}
        edges = items.setdefault("edges", [])
        while page.get("hasNextPage") and page.get("endCursor"):
            data = self._graphql(
                "query($id: ID!, $after: String) { order(id: $id) { lineItems(first: 50, after: $after) { "
                "pageInfo { hasNextPage endCursor } edges { node { id title sku quantity "
                f"originalUnitPriceSet {_MONEY} totalDiscountSet {_MONEY} "
                "product { id productType } variant { id } } } } } }",
                {"id": order.get("id"), "after": page["endCursor"]},
            )
            more = ((data.get("order") or {}).get("lineItems")) or {}
            edges.extend(more.get("edges") or [])
            page = more.get("pageInfo") or {}
        order["lineItems"] = {"edges": edges}

    # ---------------------------------------------------------------------------------- bulk export
    def _bulk_query(self, table: Table, search: str) -> str:
        arguments = f"query: {json.dumps(search)}" if search else ""
        sort = ", sortKey: UPDATED_AT" if table.sort_updated else ""
        arguments = (arguments + sort).lstrip(", ")
        head = f"{table.root}({arguments})" if arguments else table.root
        return f"{{ {head} {{ edges {{ node {{ {self._node_text(table, bulk=True)} }} }} }} }}"

    def _start_bulk(self, inner: str) -> str:
        mutation = (
            "mutation($q: String!) { bulkOperationRunQuery(query: $q) { "
            "bulkOperation { id status } userErrors { field message } } }"
        )
        for attempt in range(2):
            data = self._graphql(mutation, {"q": inner})
            result = data.get("bulkOperationRunQuery") or {}
            operation = result.get("bulkOperation") or {}
            if operation.get("id") and not result.get("userErrors"):
                return str(operation["id"])
            problems = " ".join(str(e.get("message", "")) for e in result.get("userErrors") or [])
            if attempt == 0 and "already in progress" in problems.lower():
                self._wait_bulk(
                    None
                )  # another export of this app is running: let it finish, then ask again
                continue
            raise ConnectorError(
                "invalid", "Shopify did not start the export of this table.", detail=problems[:200]
            )
        raise ConnectorError(  # pragma: no cover
            "unavailable", "Shopify is busy with another export. Try again in a few minutes."
        )

    def _wait_bulk(self, operation_id: str | None) -> dict[str, Any]:
        waited = 0.0
        while True:
            data = self._graphql(
                "{ currentBulkOperation(type: QUERY) { id status errorCode objectCount url partialDataUrl } }"
            )
            current = data.get("currentBulkOperation") or {}
            if operation_id is not None and current and current.get("id") != operation_id:
                raise ConnectorError(
                    "unavailable", "Shopify replaced the export with another one. Try again."
                )
            status = current.get("status")
            if status in (None, "COMPLETED", "FAILED", "CANCELED", "EXPIRED"):
                return current
            if waited >= self._setup.bulk_timeout_seconds:
                raise ConnectorError(
                    "unavailable", "Shopify took too long to prepare the export. Try again later."
                )
            self._sleep(self._setup.bulk_poll_seconds)
            waited += self._setup.bulk_poll_seconds

    def _read_bulk(self, table: Table, search: str) -> tuple[list[dict[str, Any]], bool]:
        operation_id = self._start_bulk(self._bulk_query(table, search))
        current = self._wait_bulk(operation_id)
        status = current.get("status")
        if status != "COMPLETED":
            raise ConnectorError(
                "unavailable",
                "Shopify could not prepare the export of this table. Try again later.",
                detail=f"bulk operation {status} {current.get('errorCode')}",
            )
        url = current.get("url")
        if not url:
            return [], False  # nothing matched
        try:
            return self._assemble(self._http.stream_lines(str(url)))
        except httpx.HTTPError as error:
            raise ConnectorError(
                "unavailable",
                "Shopify's export file could not be downloaded. Try again in a few minutes.",
                detail=type(error).__name__,
            ) from error

    def _assemble(self, lines: Iterator[str]) -> tuple[list[dict[str, Any]], bool]:
        """Rebuild nodes from a bulk file: a child line (``__parentId``) joins the connection of its parent."""
        nodes: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            parent_id = item.pop("__parentId", None)
            if parent_id is None:
                if len(nodes) >= self._max_rows:
                    return nodes, True
                nodes.append(item)
                if item.get("id"):
                    by_id[str(item["id"])] = item
                continue
            parent = by_id.get(str(parent_id))
            kind = str(item.get("id", "")).split("/")[-2:-1]
            field_name = CHILD_CONNECTIONS.get(kind[0]) if kind else None
            if parent is not None and field_name is not None:
                parent.setdefault(field_name, {"edges": []})["edges"].append({"node": item})
        return nodes, False


def _error_codes(errors: Any) -> set[str]:
    if not isinstance(errors, list):
        return {"UNKNOWN"}
    codes = set()
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        codes.add(str((extensions or {}).get("code") or "UNKNOWN"))
    return codes


def _parse_cursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
