"""Which connector types exist, and building a connector for one connection.

``ConnectorRuntime`` bundles what connectors and the cache need (database sessions, object storage, Redis, the
secret store, the Google setup, the clock). The API builds one in ``create_app``; the Celery worker builds one
from settings (``app.tasks.runtime``); tests inject fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.connectors.base import Connector, ConnectorError
from app.connectors.files import FileConnector, StoredFile
from app.connectors.google_sheets import GoogleSheetsConnector, GoogleSheetsSetup, Spreadsheet
from app.connectors.razorpay import RazorpayConnector, RazorpaySetup
from app.connectors.shopify import ShopifyConnector, ShopifySetup
from app.data.objects import ObjectStore
from app.email.sender import EmailSender
from app.models import Connection, FileVersion
from app.security.secrets import SecretStore, SecretUnavailable

# Purpose bound into each connection secret's encryption context (app.security.secrets).
SECRET_PURPOSES = {
    "google_sheets": "google_sheets_refresh_token",
    "razorpay": "razorpay_key_secret",
    "shopify": "shopify_access_token",
}
TYPE_LABELS = {
    "file": "Excel or CSV file",
    "google_sheets": "Google Sheets",
    "razorpay": "Razorpay",
    "shopify": "Shopify",
}


@dataclass
class ConnectorRuntime:
    settings: Settings
    session_factory: sessionmaker[DbSession]
    objects: ObjectStore
    redis: redis.Redis[bytes]
    secrets: SecretStore
    google: GoogleSheetsSetup
    clock: Callable[[], datetime]
    # Emails from background jobs (briefings, finished long tasks, shares): SMTP in the worker, memory in tests.
    email: EmailSender | None = None
    razorpay: RazorpaySetup = field(default_factory=RazorpaySetup)
    shopify: ShopifySetup = field(default_factory=ShopifySetup)


def version_in_use(connection: Connection, versions: list[FileVersion]) -> FileVersion | None:
    """The file version a connection reads: the one recorded in its config (DECISIONS D58). Connections from
    before that record fall back to their newest readable version."""
    ready = [v for v in versions if v.status == "ready"]
    chosen = str(connection.config.get("file_version_id") or "")
    for version in ready:
        if str(version.id) == chosen:
            return version
    return max(ready, key=lambda v: v.version, default=None)


def file_in_use(db: DbSession, connection: Connection) -> FileVersion | None:
    versions = list(
        db.scalars(
            select(FileVersion).where(
                FileVersion.connection_id == connection.id, FileVersion.status == "ready"
            )
        )
    )
    return version_in_use(connection, versions)


def stored(version: FileVersion) -> StoredFile:
    return StoredFile(
        version_id=version.id,
        version=version.version,
        storage_key=version.storage_key,
        original_name=version.original_name,
    )


def spreadsheets(connection: Connection) -> list[Spreadsheet]:
    items: list[dict[str, Any]] = list(connection.config.get("spreadsheets", []))
    return [Spreadsheet(id=str(s["id"]), name=str(s.get("name", ""))) for s in items]


def build_connector(
    db: DbSession,
    connection: Connection,
    runtime: ConnectorRuntime,
    *,
    timezone: str,
    file_version: FileVersion | None = None,
) -> Connector:
    """A connector for ``connection``. ``db`` must be scoped to the connection's workspace (secrets, RLS)."""
    max_rows = runtime.settings.max_rows_per_dataset
    if connection.type == "file":
        version = file_version or file_in_use(db, connection)
        return FileConnector(
            stored(version) if version else None,
            objects=runtime.objects,
            timezone=timezone,
            max_rows=max_rows,
        )
    if connection.type == "google_sheets":
        if not runtime.google.available:
            raise ConnectorError(
                "unavailable", "Google Sheets is not set up on this server. Ask your administrator."
            )
        if connection.secret_ref is None:
            raise ConnectorError("needs_reauth", "Sign in to Google again to reconnect.")
        try:
            token = runtime.secrets.reveal(
                db,
                org_id=connection.org_id,
                secret_id=connection.secret_ref,
                purpose=SECRET_PURPOSES["google_sheets"],
            )
        except SecretUnavailable as error:
            raise ConnectorError(
                "needs_reauth", "Sign in to Google again to reconnect.", detail=str(error)
            ) from error
        return GoogleSheetsConnector(
            setup=runtime.google,
            refresh_token=token,
            spreadsheets=spreadsheets(connection),
            timezone=timezone,
            max_rows=max_rows,
        )
    if connection.type == "razorpay":
        key_id = str(connection.config.get("key_id") or "")
        if connection.secret_ref is None or not key_id:
            raise ConnectorError("needs_reauth", "Enter the Razorpay API key again to reconnect.")
        try:
            key_secret = runtime.secrets.reveal(
                db,
                org_id=connection.org_id,
                secret_id=connection.secret_ref,
                purpose=SECRET_PURPOSES["razorpay"],
            )
        except SecretUnavailable as error:
            raise ConnectorError(
                "needs_reauth",
                "Enter the Razorpay API key again to reconnect.",
                detail=str(error),
            ) from error
        return RazorpayConnector(
            setup=runtime.razorpay,
            key_id=key_id,
            key_secret=key_secret,
            timezone=timezone,
            max_rows=max_rows,
            clock=runtime.clock,
        )
    if connection.type == "shopify":
        return _shopify(db, connection, runtime, timezone=timezone, max_rows=max_rows)
    raise ConnectorError("invalid", "This kind of connection is not supported.")  # pragma: no cover


def shopify_scopes(connection: Connection) -> frozenset[str]:
    return frozenset(str(s) for s in connection.config.get("scopes", []))


def _shopify(
    db: DbSession,
    connection: Connection,
    runtime: ConnectorRuntime,
    *,
    timezone: str,
    max_rows: int,
) -> ShopifyConnector:
    if not runtime.shopify.available:
        raise ConnectorError(
            "unavailable", "Shopify is not set up on this server. Ask your administrator."
        )
    shop = str(connection.config.get("shop") or "")
    reconnect = "Sign in to Shopify again to reconnect."
    if connection.secret_ref is None or not shop:
        raise ConnectorError("needs_reauth", reconnect)
    try:
        token = runtime.secrets.reveal(
            db,
            org_id=connection.org_id,
            secret_id=connection.secret_ref,
            purpose=SECRET_PURPOSES["shopify"],
        )
    except SecretUnavailable as error:
        raise ConnectorError("needs_reauth", reconnect, detail=str(error)) from error
    return ShopifyConnector(
        setup=runtime.shopify,
        shop=shop,
        access_token=token,
        scopes=shopify_scopes(connection),
        timezone=timezone,
        max_rows=max_rows,
        clock=runtime.clock,
    )


def close_connector(connector: Connector) -> None:
    close = getattr(connector, "close", None)
    if callable(close):
        close()
