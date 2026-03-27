"""
Correlation ID middleware: propagate ``X-Correlation-ID`` and bind contextvars.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"

correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """
    Return the correlation id for the current async context, if any.

    Returns:
        Correlation id string, or None when unset (e.g. outside HTTP requests).
    """
    return correlation_id_ctx.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Read or generate a correlation id, store on request state and contextvars."""

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
        token: Token[str | None] = correlation_id_ctx.set(correlation_id)
        try:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            return response
        finally:
            correlation_id_ctx.reset(token)
