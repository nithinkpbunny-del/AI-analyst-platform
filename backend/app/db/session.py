"""Engine and session factory. Tenant scoping (``SET LOCAL app.org_id`` + RLS) lives in ``app.db.tenant``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.db.tenant  # noqa: F401  (registers the tenant-scoping hook on every Session)
from app.config import Settings


def make_engine(
    database_url: str,
    *,
    connect_timeout_seconds: int = 5,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout_seconds: int = 30,
) -> Engine:
    """``pool_size`` connections stay open, up to ``max_overflow`` more under load; a caller waits at most
    ``pool_timeout_seconds`` for a free one (``DB_POOL_*`` settings)."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        connect_args={"connect_timeout": connect_timeout_seconds},
    )


def engine_from_settings(settings: Settings) -> Engine:
    return make_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout_seconds=settings.db_pool_timeout_seconds,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaction boundary: commit on success, roll back on any error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
