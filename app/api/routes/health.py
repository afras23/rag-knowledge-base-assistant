"""
Health check endpoints.

These endpoints provide liveness/readiness and basic service health signals.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Create engine at module scope to avoid repeatedly constructing it per request.
_async_engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)


@router.get("/health")
async def health() -> JSONResponse:
    """Basic liveness check."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": settings.app_version,
        },
    )


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness check: verifies database connectivity."""
    try:
        async with _async_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "checks": {"database": "ok"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except SQLAlchemyError:
        logger.exception(
            "Readiness check failed",
            extra={"error_code": "DATABASE_CONNECTIVITY_FAILED"},
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "checks": {"database": "error"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
