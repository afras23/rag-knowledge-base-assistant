"""
Correlation ID middleware for request tracing.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a correlation id to each request/response for observability."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Propagate or generate a correlation id.

        Args:
            request: Incoming request.
            call_next: Next ASGI handler.

        Returns:
            Response with ``X-Correlation-ID`` header set.
        """
        incoming = request.headers.get(CORRELATION_HEADER)
        correlation_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
