"""
FastAPI application entry point.

This module wires middleware, registers API routes, and defines app startup/shutdown
lifespan handling.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import settings
from app.core.database import engine
from app.core.exceptions import BaseAppError
from app.core.logging_config import configure_root_logger
from app.core.middleware import CorrelationIdMiddleware, RequestLoggingMiddleware

logger = logging.getLogger(__name__)


class BasicErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Convert unexpected exceptions into JSON error responses."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """
        Dispatch request through the next handler.

        Args:
            request: Incoming HTTP request.
            call_next: Next handler callable.

        Returns:
            Starlette Response.
        """
        try:
            return await call_next(request)
        except BaseAppError as app_error:
            cid = getattr(request.state, "correlation_id", None)
            logger.warning(
                "Application error",
                extra={
                    "error_code": app_error.error_code,
                    "status_code": app_error.status_code,
                    "path": request.url.path,
                    "method": request.method,
                    "correlation_id": cid,
                    **app_error.context,
                },
            )
            return JSONResponse(
                status_code=app_error.status_code,
                content={
                    "status": "error",
                    "error": {
                        "code": app_error.error_code,
                        "message": app_error.message,
                    },
                    "metadata": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "correlation_id": cid or "",
                    },
                },
            )
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown logic for the FastAPI application."""
    logger.info(
        "Application starting",
        extra={"app_env": settings.app_env, "version": settings.app_version},
    )
    yield
    await engine.dispose()
    logger.info(
        "Application shutting down",
        extra={"app_env": settings.app_env, "version": settings.app_version},
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    configure_root_logger(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS
    if settings.debug and not settings.cors_allow_origins_list:
        allow_origins: list[str] = ["*"]
    else:
        allow_origins = settings.cors_allow_origins_list

    allow_credentials = allow_origins != ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BasicErrorHandlerMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Map HTTPException to the standard error envelope."""
        cid = getattr(request.state, "correlation_id", None)
        detail: Any = exc.detail
        if isinstance(detail, list):
            message = "Validation error"
        elif isinstance(detail, dict):
            message = str(detail.get("msg", detail))
        else:
            message = str(detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "error",
                "error": {
                    "code": "HTTP_ERROR",
                    "message": message,
                },
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "correlation_id": cid or "",
                },
            },
        )

    # Routes
    from app.api.routes.health import router as health_router

    app.include_router(health_router, prefix=settings.api_prefix)

    from app.api.routes.admin import router as admin_router

    app.include_router(admin_router, prefix=settings.api_prefix)

    from app.api.routes.chat import router as chat_router

    app.include_router(chat_router, prefix=settings.api_prefix)

    return app


app = create_app()
