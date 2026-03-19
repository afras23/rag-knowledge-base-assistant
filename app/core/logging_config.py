"""
Structured JSON logging configuration.

Logs include correlation_id from contextvars so each request can be traced end-to-end.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class JsonCorrelationFormatter(logging.Formatter):
    """Format log records as JSON including correlation_id."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Base fields.
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        correlation_id = correlation_id_ctx.get()
        if correlation_id:
            log_payload["correlation_id"] = correlation_id

        # Include any `extra` fields. In stdlib logging, values from extra become
        # attributes on the LogRecord directly.
        reserved = set(logging.LogRecord("reserved", logging.INFO, "", 0, "", (), None).__dict__.keys())
        for key, value in record.__dict__.items():
            if key in reserved:
                continue
            log_payload[key] = value

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload, ensure_ascii=False)


def configure_root_logger(log_level: str) -> None:
    """Configure root logger with structured JSON output."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonCorrelationFormatter()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def get_module_logger(logger_name: str) -> logging.Logger:
    """Get a module-scoped logger."""
    return logging.getLogger(logger_name)
