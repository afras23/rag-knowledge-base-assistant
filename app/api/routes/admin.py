"""
Admin ingestion routes (Phase 3 — Component 4).

Endpoints are intentionally thin: they validate input, delegate to
`IngestionPipeline` and repositories, and return existing Phase 2 schemas.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.schemas.admin import (
    IngestionEventResponse,
    IngestionJobResponse,
)
from app.api.schemas.common import PaginatedResponse
from app.config import settings
from app.core.exceptions import IngestionError
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import DocumentChunker
from app.services.ingestion.embedder import DocumentEmbedder
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.vectorstore.chroma_client import ChromaClient

logger = logging.getLogger(__name__)

router = APIRouter()


@asynccontextmanager
async def _db_session() -> AsyncIterator[AsyncSession]:
    """Create an async DB session for a single request."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _get_ingestion_repo(session: AsyncSession = Depends(_db_session)) -> IngestionRepository:
    return IngestionRepository(session)


async def _get_document_repo(session: AsyncSession = Depends(_db_session)) -> DocumentRepository:
    return DocumentRepository(session)


def _get_chroma_client() -> ChromaClient:
    return ChromaClient()


def _get_pipeline(
    *,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    document_repo: DocumentRepository = Depends(_get_document_repo),
    chroma_client: ChromaClient = Depends(_get_chroma_client),
) -> IngestionPipeline:
    return IngestionPipeline(
        ingestion_repo=ingestion_repo,
        document_repo=document_repo,
        chunker=DocumentChunker(),
        embedder=DocumentEmbedder(),
        chroma_client=chroma_client,
    )


@router.post("/admin/ingest", status_code=201)
async def ingest_admin(
    collection_id: str = Form(..., description="Target collection ID"),
    restriction_level: Literal["public", "restricted", "confidential"] = Form(
        "restricted",
        description="Restriction level assigned to created documents",
    ),
    directory_path: str | None = Form(None, description="Optional server directory path to ingest"),
    files: list[UploadFile] | None = File(default=None),
    pipeline: IngestionPipeline = Depends(_get_pipeline),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> IngestionJobResponse:
    """
    Trigger an ingestion job from uploaded files or a directory path.
    """
    if not directory_path and not files:
        raise HTTPException(status_code=422, detail="Either files or directory_path must be provided")
    if directory_path and files:
        raise HTTPException(status_code=422, detail="Provide either files or directory_path, not both")

    file_paths: list[Path] = []
    if files:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="rag_ingest_") as tmp:
            temp_dir = Path(tmp)
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(status_code=422, detail="Uploaded file must have a filename")
                destination = temp_dir / upload.filename
                content = await upload.read()
                await anyio.to_thread.run_sync(destination.write_bytes, content)
                file_paths.append(destination)

            ingestion_result = await pipeline.ingest_documents(
                collection_id=collection_id,
                file_paths=file_paths,
                directory_path=None,
                restriction_level=restriction_level,
                created_by=None,
            )
    else:
        ingestion_result = await pipeline.ingest_documents(
            collection_id=collection_id,
            file_paths=None,
            directory_path=Path(directory_path) if directory_path else None,
            restriction_level=restriction_level,
            created_by=None,
        )

    job = await ingestion_repo.get_job_status(job_id=ingestion_result.job_id)
    return IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/admin/ingest/{job_id}", status_code=200)
async def get_ingestion_job(
    job_id: UUID,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> IngestionJobResponse:
    """Return ingestion job progress."""
    try:
        job = await ingestion_repo.get_job_status(job_id=job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Ingestion job not found") from None

    return IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.delete("/admin/documents/{document_id}", status_code=200)
async def delete_admin_document(
    document_id: UUID,
    document_repo: DocumentRepository = Depends(_get_document_repo),
    chroma_client: ChromaClient = Depends(_get_chroma_client),
) -> JSONResponse:
    """Delete a document record and corresponding Chroma vectors."""
    db_document = await document_repo.get_document(document_id)

    # Best-effort: try vector deletion first; only delete DB if vectors succeed.
    try:
        deleted_vectors = await chroma_client.delete_document(
            collection_id=db_document.collection_id,
            document_id=str(db_document.id),
        )
    except IngestionError as exc:
        logger.error(
            "Vector deletion failed during admin document delete",
            extra={"error_code": exc.error_code, "document_id": str(document_id), "error": exc.message},
        )
        raise HTTPException(status_code=500, detail="Vector deletion failed") from exc

    try:
        await document_repo.delete_document(db_document.id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DB deletion failed during admin document delete",
            extra={"document_id": str(document_id), "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Document deletion failed") from exc

    return JSONResponse(
        status_code=200,
        content={"status": "deleted", "document_id": str(document_id), "deleted_vectors": deleted_vectors},
    )


@router.post("/admin/reindex", status_code=202)
async def reindex_admin(
    collection_id: str = Form(..., description="Collection to reindex"),
) -> JSONResponse:
    """Trigger full re-ingestion for a collection (stub)."""
    logger.info(
        "Reindex requested (stub)",
        extra={"collection_id": collection_id, "timestamp": datetime.now(timezone.utc).isoformat()},
    )
    return JSONResponse(status_code=202, content={"status": "accepted", "collection_id": collection_id})


@router.get("/admin/ingest/{job_id}/events", status_code=200)
async def get_ingestion_events(
    job_id: UUID,
    page: int = 1,
    page_size: int = 20,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> PaginatedResponse[IngestionEventResponse]:
    """Return paginated ingestion events for a job."""
    try:
        events, total_count = await ingestion_repo.list_job_events(job_id=job_id, page=page, page_size=page_size)
    except LookupError:
        raise HTTPException(status_code=404, detail="Ingestion job not found") from None

    items = [
        IngestionEventResponse(
            document_id=event.document_id,
            stage=event.stage,
            status=cast(Literal["success", "failed", "skipped"], event.status),
            error_message=event.error_message,
            duration_ms=event.duration_ms,
            timestamp=event.created_at,
        )
        for event in events
    ]

    return PaginatedResponse[IngestionEventResponse](
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
    )
