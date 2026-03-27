"""
HTTP request logging middleware (method, path, status, latency, correlation id).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.middleware.correlation import get_correlation_id

logger = logging.getLogger("app.http.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit one structured log line per request after the response is produced."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Time the request and log outcome with correlation id.

        Args:
            request: Incoming HTTP request.
            call_next: Downstream ASGI handler.

        Returns:
            The upstream response.
        """
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000.0
        status_code = response.status_code
        cid = get_correlation_id() or getattr(request.state, "correlation_id", None) or ""
        extra = {
            "http_method": request.method,
            "http_path": request.url.path,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 3),
            "correlation_id": cid,
        }
        if status_code >= 500:
            logger.error("HTTP request completed", extra=extra)
        elif status_code >= 400:
            logger.warning("HTTP request completed", extra=extra)
        else:
            logger.info("HTTP request completed", extra=extra)
        return response
