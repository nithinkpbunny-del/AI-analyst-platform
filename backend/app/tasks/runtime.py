"""What background jobs need, built once per worker process from settings."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

import redis

from app.config import get_settings
from app.connectors.google_sheets import default_setup
from app.connectors.razorpay import default_setup as razorpay_setup
from app.connectors.registry import ConnectorRuntime
from app.connectors.shopify import default_setup as shopify_setup
from app.connectors.sql_database import default_setup as database_setup
from app.connectors.zoho_books import default_setup as zoho_setup
from app.data.objects import S3ObjectStore
from app.db.session import make_engine, make_session_factory
from app.email.sender import SmtpEmailSender
from app.security.secrets import SecretStore, key_wrapper


def _utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def worker_runtime() -> ConnectorRuntime:  # pragma: no cover - exercised by the running worker
    settings = get_settings()
    return ConnectorRuntime(
        settings=settings,
        session_factory=make_session_factory(make_engine(settings.database_url)),
        objects=S3ObjectStore(settings),
        redis=redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=10),
        secrets=SecretStore(key_wrapper(settings)),
        google=default_setup(settings),
        razorpay=razorpay_setup(settings),
        shopify=shopify_setup(settings),
        zoho=zoho_setup(settings),
        database=database_setup(settings),
        clock=_utc_now,
        email=SmtpEmailSender(settings),
    )
