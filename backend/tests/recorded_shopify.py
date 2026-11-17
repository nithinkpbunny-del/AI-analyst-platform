"""Shopify for tests: the stand-in (``app.connectors.shopify_stand_in``) behind a "real" setup, so no live calls.

The setup is in ``shopify`` mode (as with real credentials), so the development-only stand-in pages answer 404 in
API tests; the stand-in transport answers OAuth and GraphQL in Shopify's formats.
"""

from __future__ import annotations

from typing import Any

from app.connectors.shopify import DEFAULT_SCOPES, ShopifySetup
from app.connectors.shopify_stand_in import (
    STAND_IN_CLIENT_ID,
    STAND_IN_CLIENT_SECRET,
    STAND_IN_TOKEN_PREFIX,
    StandInShopifyTransport,
    issue_code,
)

SHOP = "urbancart-demo.myshopify.com"
TOKEN = f"{STAND_IN_TOKEN_PREFIX}0123456789abcdef0123"
SCOPES = ",".join(DEFAULT_SCOPES)


def shopify_test_setup(fake: StandInShopifyTransport, **kwargs: Any) -> ShopifySetup:
    values: dict[str, Any] = {
        "mode": "shopify",
        "client_id": STAND_IN_CLIENT_ID,
        "client_secret": STAND_IN_CLIENT_SECRET,
        "transport": fake,
        "sleep": lambda _s: None,
        "bulk_poll_seconds": 0.0,
        "api_base_url": "http://localhost:8000",
    } | kwargs
    return ShopifySetup(**values)


def code(shop: str = SHOP, scopes: str = SCOPES) -> str:
    return issue_code(shop, scopes)
