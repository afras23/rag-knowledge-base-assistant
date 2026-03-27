"""
Health check endpoints.

These endpoints provide liveness/readiness and basic service health signals.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import MetricsResponse, SuccessResponse
from app.config import settings
from app.core.database import engine
from app.core.dependencies import get_db_session
from app.repositories.document_repo import DocumentRepository
from app.repositories.query_repo import QueryRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _correlation_id(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "") or "")


def _utc_day_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


async def _chroma_reachable() -> bool:
    """Return True when ChromaDB HTTP API responds to heartbeat or list_collections."""
    try:
        chromadb = __import__("chromadb")
        client = chromadb.HttpClient(host=settings.chroma_host, port=int(settings.chroma_port))

        def _ping() -> None:
            if hasattr(client, "heartbeat"):
                client.heartbeat()
            else:
                client.list_collections()

        await anyio.to_thread.run_sync(_ping)
        return True
    except (OSError, RuntimeError, ValueError, ModuleNotFoundError) as exc:
        logger.warning(
            "Chroma readiness check failed",
            extra={"error_type": type(exc).__name__},
        )
        return False


@router.get("/health")
async def health(request: Request) -> SuccessResponse[dict[str, str]]:
    """Basic liveness check."""
    cid = _correlation_id(request)
    payload = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.app_version,
    }
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Readiness check: verifies database and ChromaDB connectivity."""
    cid = _correlation_id(request)
    meta = {"correlation_id": cid, "timestamp": datetime.now(timezone.utc).isoformat()}
    checks: dict[str, str] = {}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        logger.exception(
            "Readiness check failed (database)",
            extra={"error_code": "DATABASE_CONNECTIVITY_FAILED"},
        )
        checks["database"] = "error"
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": {
                    "code": "READINESS_FAILED",
                    "message": "One or more readiness checks failed",
                },
                "metadata": {**meta, "checks": checks},
            },
        )

    chroma_ok = await _chroma_reachable()
    checks["chromadb"] = "ok" if chroma_ok else "error"

    if not chroma_ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": {
                    "code": "READINESS_FAILED",
                    "message": "One or more readiness checks failed",
                },
                "metadata": {**meta, "checks": checks},
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "data": {
                "status": "ready",
                "checks": checks,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "metadata": meta,
        },
    )


@router.get("/metrics")
async def metrics(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> SuccessResponse[MetricsResponse]:
    """Operational metrics backed by Postgres query analytics and document catalog."""
    cid = _correlation_id(request)
    day_start, day_end = _utc_day_bounds()
    query_repo = QueryRepository(session)
    doc_repo = DocumentRepository(session)
    (
        queries_today,
        refusals_today,
        avg_latency_ms,
        cost_today_usd,
    ) = await query_repo.aggregate_query_metrics_for_interval(
        interval_start=day_start,
        interval_end=day_end,
    )
    documents_indexed = await doc_repo.count_documents()
    active_collections = await doc_repo.count_distinct_collections_with_documents()
    payload = MetricsResponse(
        queries_today=queries_today,
        refusals_today=refusals_today,
        avg_latency_ms=avg_latency_ms,
        cost_today_usd=cost_today_usd,
        cost_limit_usd=settings.max_daily_cost_usd,
        documents_indexed=documents_indexed,
        active_collections=active_collections,
    )
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})
