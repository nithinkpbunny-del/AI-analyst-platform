"""Logging setup: one handler, redaction on every line, JSON in production, readable text otherwise."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from app.config import Settings
from app.security.redaction import Redactor

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
_QUIET_LOGGERS = (
    "uvicorn.access",
)  # request lines are logged by our own middleware (no query strings)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line. Redaction is applied by the wrapping formatter afterwards."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _RedactingWrapper(logging.Formatter):
    """Applies the redactor to whatever the inner formatter produced."""

    def __init__(self, inner: logging.Formatter, redactor: Redactor) -> None:
        super().__init__()
        self._inner = inner
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact(self._inner.format(record))


def configure_logging(settings: Settings) -> Redactor:
    """Install the redacting handler on the root logger and on uvicorn/celery loggers.

    Safe to call more than once (it replaces existing handlers). Returns the redactor so callers can reuse it.
    """
    redactor = Redactor(settings.secret_values())
    inner: logging.Formatter = (
        _JsonFormatter() if settings.app_env == "production" else logging.Formatter(_TEXT_FORMAT)
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_RedactingWrapper(inner, redactor))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())

    # Frameworks install their own handlers; route them through ours so nothing bypasses redaction.
    for name in ("uvicorn", "uvicorn.error", "celery", "celery.app.trace", "celery.worker"):
        framework_logger = logging.getLogger(name)
        framework_logger.handlers[:] = []
        framework_logger.propagate = True
    for name in _QUIET_LOGGERS:
        quiet = logging.getLogger(name)
        quiet.handlers[:] = []
        quiet.propagate = False
    return redactor
