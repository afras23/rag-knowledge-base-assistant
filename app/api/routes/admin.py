"""
Admin ingestion and corpus management routes (Phase 3, Phase 9).
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.admin import (
    CollectionCreateRequest,
    CollectionSchema,
    CollectionUpdateRequest,
    DocumentResponse,
    DocumentSupersedeRequest,
    IngestionEventResponse,
    IngestionJobResponse,
)
from app.api.schemas.common import PaginatedResponse, SuccessResponse
from app.config import settings
from app.core.dependencies import get_db_session
from app.core.exceptions import CollectionNotEmptyError, CollectionNotFoundError, IngestionError
from app.models.document import Document
from app.models.ingestion import IngestionJobStatus
from app.repositories.collection_repo import CollectionRepository
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import DocumentChunker
from app.services.ingestion.embedder import get_embedding_provider
from app.services.ingestion.indexer import IndexingService
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.ingestion.reindex_service import ReindexService
from app.services.vectorstore.chroma_client import ChromaClient, ChromaClientWrapper

logger = logging.getLogger(__name__)

router = APIRouter()


def _correlation_id(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "") or "")


async def _get_ingestion_repo(session: AsyncSession = Depends(get_db_session)) -> IngestionRepository:
    return IngestionRepository(session)


async def _get_document_repo(session: AsyncSession = Depends(get_db_session)) -> DocumentRepository:
    return DocumentRepository(session)


async def _get_collection_repo(session: AsyncSession = Depends(get_db_session)) -> CollectionRepository:
    return CollectionRepository(session)


def _get_chroma_wrapper() -> ChromaClientWrapper:
    return ChromaClientWrapper()


def _get_chroma_client() -> ChromaClient:
    return ChromaClient()


def _get_pipeline(
    *,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    document_repo: DocumentRepository = Depends(_get_document_repo),
    chroma_wrapper: ChromaClientWrapper = Depends(_get_chroma_wrapper),
) -> IngestionPipeline:
    indexing_service = IndexingService(
        embedding_provider=get_embedding_provider(),
        chroma_client=chroma_wrapper,
        ingestion_repo=ingestion_repo,
    )
    return IngestionPipeline(
        ingestion_repo=ingestion_repo,
        document_repo=document_repo,
        chunker=DocumentChunker(),
        indexer=indexing_service,
        settings=settings,
    )


def _get_reindex_service(
    document_repo: DocumentRepository = Depends(_get_document_repo),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    chroma_wrapper: ChromaClientWrapper = Depends(_get_chroma_wrapper),
) -> ReindexService:
    indexer = IndexingService(
        embedding_provider=get_embedding_provider(),
        chroma_client=chroma_wrapper,
        ingestion_repo=ingestion_repo,
    )
    return ReindexService(
        indexer=indexer,
        chroma_client=chroma_wrapper,
        document_repo=document_repo,
    )


def _document_to_response(doc: Document) -> DocumentResponse:
    ingestion_status: Literal["pending", "processing", "completed", "failed"] = (
        "completed" if doc.chunk_count > 0 else "pending"
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        file_format=doc.file_format,
        collection_id=doc.collection_id,
        restriction_level=doc.restriction_level,
        version_label=doc.version_label,
        supersedes_id=doc.supersedes_id,
        superseded_by=doc.superseded_by_id,
        chunk_count=doc.chunk_count,
        ingestion_status=ingestion_status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("/admin/documents", status_code=201)
async def admin_upload_single_document(
    request: Request,
    file: UploadFile = File(..., description="Single document file to ingest"),
    collection_id: str = Form(..., description="Target collection ID"),
    restriction_level: Literal["public", "restricted", "confidential"] = Form(
        "restricted",
        description="Restriction level for the created document",
    ),
    pipeline: IngestionPipeline = Depends(_get_pipeline),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> SuccessResponse[IngestionJobResponse]:
    """Upload one file and run the ingestion pipeline for it."""
    if file.filename is None:
        raise HTTPException(status_code=422, detail="Uploaded file must have a filename")

    with tempfile.TemporaryDirectory(prefix="rag_admin_doc_") as tmp:
        temp_dir = Path(tmp)
        destination = temp_dir / file.filename
        content = await file.read()
        await anyio.to_thread.run_sync(destination.write_bytes, content)

        ingestion_result = await pipeline.ingest_documents(
            collection_id=collection_id,
            file_paths=[destination],
            directory_path=None,
            restriction_level=restriction_level,
            created_by="admin",
        )

    job = await ingestion_repo.get_job_status(job_id=ingestion_result.job_id)
    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.get("/admin/documents", status_code=200)
async def admin_list_documents(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    collection_id: str | None = None,
    include_superseded: bool = False,
    document_repo: DocumentRepository = Depends(_get_document_repo),
) -> SuccessResponse[PaginatedResponse[DocumentResponse]]:
    """Paginated document list; omit superseded rows by default."""
    items_db, total = await document_repo.list_documents(
        collection_id=collection_id,
        page=page,
        page_size=page_size,
        include_superseded=include_superseded,
    )
    page_payload = PaginatedResponse[DocumentResponse](
        items=[_document_to_response(d) for d in items_db],
        total=total,
        page=page,
        page_size=page_size,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=page_payload, metadata={"correlation_id": cid})


@router.get("/admin/documents/{document_id}", status_code=200)
async def admin_get_document(
    document_id: UUID,
    request: Request,
    document_repo: DocumentRepository = Depends(_get_document_repo),
) -> SuccessResponse[DocumentResponse]:
    """Return a single document record."""
    doc = await document_repo.get_document(document_id)

    cid = _correlation_id(request)
    return SuccessResponse(
        status="success",
        data=_document_to_response(doc),
        metadata={"correlation_id": cid},
    )


@router.post("/admin/documents/{document_id}/supersede", status_code=200)
async def admin_supersede_document(
    document_id: UUID,
    body: DocumentSupersedeRequest,
    request: Request,
    document_repo: DocumentRepository = Depends(_get_document_repo),
    chroma_wrapper: ChromaClientWrapper = Depends(_get_chroma_wrapper),
) -> SuccessResponse[DocumentResponse]:
    """
    Mark an older document as superseded by a newer document (DB + Chroma metadata).
    """
    old_doc = await document_repo.get_document(document_id)
    new_doc = await document_repo.get_document(body.new_document_id)

    if old_doc.id == new_doc.id:
        raise HTTPException(status_code=422, detail="new_document_id must differ from document_id")
    if old_doc.collection_id != new_doc.collection_id:
        raise HTTPException(status_code=422, detail="Documents must belong to the same collection")

    await document_repo.mark_superseded(superseded_id=old_doc.id, supersedes_id=new_doc.id)
    updated = int(
        await chroma_wrapper.update_document_superseded_metadata(
            collection_name=old_doc.collection_id,
            doc_id=str(old_doc.id),
            is_superseded=True,
        )
    )
    logger.info(
        "Document supersede completed",
        extra={"old_document_id": str(old_doc.id), "new_document_id": str(new_doc.id), "chroma_rows": updated},
    )
    fresh_old = await document_repo.get_document(document_id)
    cid = _correlation_id(request)
    return SuccessResponse(
        status="success",
        data=_document_to_response(fresh_old),
        metadata={"correlation_id": cid},
    )


@router.post("/admin/collections", status_code=201)
async def admin_create_collection(
    body: CollectionCreateRequest,
    request: Request,
    collection_repo: CollectionRepository = Depends(_get_collection_repo),
) -> SuccessResponse[CollectionSchema]:
    """Create a collection row."""
    from sqlalchemy.exc import IntegrityError

    try:
        row = await collection_repo.create_collection(
            collection_id=body.id,
            name=body.name,
            description=body.description,
            allowed_roles=body.allowed_roles,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Collection id already exists") from exc
    payload = CollectionSchema(
        id=row.id,
        name=row.name,
        description=row.description,
        allowed_roles=list(row.allowed_roles),
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.get("/admin/collections", status_code=200)
async def admin_list_collections(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    collection_repo: CollectionRepository = Depends(_get_collection_repo),
) -> SuccessResponse[PaginatedResponse[CollectionSchema]]:
    """Paginated collection list."""
    rows, total = await collection_repo.list_collections(page=page, page_size=page_size)
    items = [
        CollectionSchema(
            id=r.id,
            name=r.name,
            description=r.description,
            allowed_roles=list(r.allowed_roles),
        )
        for r in rows
    ]
    page_payload = PaginatedResponse[CollectionSchema](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=page_payload, metadata={"correlation_id": cid})


@router.put("/admin/collections/{collection_id}", status_code=200)
async def admin_update_collection(
    collection_id: str,
    body: CollectionUpdateRequest,
    request: Request,
    collection_repo: CollectionRepository = Depends(_get_collection_repo),
) -> SuccessResponse[CollectionSchema]:
    """Update collection metadata."""
    row = await collection_repo.update_collection(
        collection_id=collection_id,
        name=body.name,
        description=body.description,
        allowed_roles=body.allowed_roles,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    payload = CollectionSchema(
        id=row.id,
        name=row.name,
        description=row.description,
        allowed_roles=list(row.allowed_roles),
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.delete("/admin/collections/{collection_id}", status_code=200)
async def admin_delete_collection(
    collection_id: str,
    request: Request,
    collection_repo: CollectionRepository = Depends(_get_collection_repo),
) -> SuccessResponse[dict[str, str]]:
    """Delete an empty collection."""
    existing = await collection_repo.get_by_id(collection_id=collection_id)
    if existing is None:
        raise CollectionNotFoundError(context={"collection_id": collection_id})
    count = await collection_repo.count_documents_in_collection(collection_id=collection_id)
    if count > 0:
        raise CollectionNotEmptyError(
            context={"collection_id": collection_id, "document_count": count},
        )
    await collection_repo.delete_collection_by_id(collection_id=collection_id)
    cid = _correlation_id(request)
    return SuccessResponse(
        status="success",
        data={"status": "deleted", "collection_id": collection_id},
        metadata={"correlation_id": cid},
    )


async def _run_reindex_job(
    *,
    document_ids: list[UUID],
    ingestion_repo: IngestionRepository,
    reindex_service: ReindexService,
) -> UUID:
    """Execute reindex for many documents under one ingestion job."""
    job = await ingestion_repo.create_job(total_documents=len(document_ids), created_by="admin_reindex")
    await ingestion_repo.update_job_progress(
        job_id=job.id,
        status=IngestionJobStatus.processing,
        processed=0,
        succeeded=0,
        failed=0,
    )
    processed = 0
    succeeded = 0
    failed = 0
    for doc_id in document_ids:
        try:
            await reindex_service.reindex_document(document_id=doc_id, job_id=job.id)
            succeeded += 1
        except IngestionError as exc:
            failed += 1
            logger.warning(
                "Reindex failed for document",
                extra={"document_id": str(doc_id), "error": exc.message},
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "Reindex failed for document",
                extra={"document_id": str(doc_id), "error": str(exc)},
            )
        processed += 1
        await ingestion_repo.update_job_progress(
            job_id=job.id,
            status=IngestionJobStatus.processing,
            processed=processed,
            succeeded=succeeded,
            failed=failed,
        )

    final_status = (
        IngestionJobStatus.failed if failed == len(document_ids) and document_ids else IngestionJobStatus.completed
    )
    await ingestion_repo.update_job_progress(
        job_id=job.id,
        status=final_status,
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        completed_at=datetime.now(timezone.utc),
    )
    return job.id


@router.post("/admin/reindex/document/{document_id}", status_code=202)
async def admin_reindex_document(
    document_id: UUID,
    request: Request,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    reindex_service: ReindexService = Depends(_get_reindex_service),
) -> SuccessResponse[IngestionJobResponse]:
    """Re-embed vectors for a single document from stored Chroma text."""
    job_id = await _run_reindex_job(
        document_ids=[document_id],
        ingestion_repo=ingestion_repo,
        reindex_service=reindex_service,
    )
    job = await ingestion_repo.get_job_status(job_id=job_id)
    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.post("/admin/reindex/collection/{collection_id}", status_code=202)
async def admin_reindex_collection(
    collection_id: str,
    request: Request,
    document_repo: DocumentRepository = Depends(_get_document_repo),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    reindex_service: ReindexService = Depends(_get_reindex_service),
) -> SuccessResponse[IngestionJobResponse]:
    """Re-embed all documents in a collection."""
    doc_ids = await document_repo.list_document_ids_for_collection(collection_id=collection_id)
    if not doc_ids:
        raise HTTPException(status_code=422, detail="No documents in collection")
    job_id = await _run_reindex_job(
        document_ids=doc_ids,
        ingestion_repo=ingestion_repo,
        reindex_service=reindex_service,
    )
    job = await ingestion_repo.get_job_status(job_id=job_id)
    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.post("/admin/reindex/all", status_code=202)
async def admin_reindex_all(
    request: Request,
    document_repo: DocumentRepository = Depends(_get_document_repo),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
    reindex_service: ReindexService = Depends(_get_reindex_service),
) -> SuccessResponse[IngestionJobResponse]:
    """Re-embed every document in the corpus (returns ingestion job_id)."""
    doc_ids = await document_repo.list_all_document_ids()
    if not doc_ids:
        raise HTTPException(status_code=422, detail="No documents to reindex")
    job_id = await _run_reindex_job(
        document_ids=doc_ids,
        ingestion_repo=ingestion_repo,
        reindex_service=reindex_service,
    )
    job = await ingestion_repo.get_job_status(job_id=job_id)
    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.post("/admin/ingest", status_code=201)
async def ingest_admin(
    request: Request,
    collection_id: str = Form(..., description="Target collection ID"),
    restriction_level: Literal["public", "restricted", "confidential"] = Form(
        "restricted",
        description="Restriction level assigned to created documents",
    ),
    directory_path: str | None = Form(None, description="Optional server directory path to ingest"),
    files: list[UploadFile] | None = File(default=None),
    pipeline: IngestionPipeline = Depends(_get_pipeline),
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> SuccessResponse[IngestionJobResponse]:
    """
    Trigger an ingestion job from uploaded files or a directory path.
    """
    if not directory_path and not files:
        raise HTTPException(status_code=422, detail="Either files or directory_path must be provided")
    if directory_path and files:
        raise HTTPException(status_code=422, detail="Provide either files or directory_path, not both")

    file_paths: list[Path] = []
    if files:
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
    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.get("/admin/ingest/{job_id}", status_code=200)
async def get_ingestion_job(
    job_id: UUID,
    request: Request,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> SuccessResponse[IngestionJobResponse]:
    """Return ingestion job progress."""
    try:
        job = await ingestion_repo.get_job_status(job_id=job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Ingestion job not found") from None

    payload = IngestionJobResponse(
        job_id=job.id,
        status=cast(Literal["pending", "processing", "completed", "failed"], job.status),
        total_documents=job.total_documents,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.delete("/admin/documents/{document_id}", status_code=200)
async def delete_admin_document(
    document_id: UUID,
    request: Request,
    document_repo: DocumentRepository = Depends(_get_document_repo),
    chroma_client: ChromaClient = Depends(_get_chroma_client),
) -> SuccessResponse[dict[str, str | int]]:
    """Delete a document record and corresponding Chroma vectors."""
    db_document = await document_repo.get_document(document_id)

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

    cid = _correlation_id(request)
    payload: dict[str, str | int] = {
        "status": "deleted",
        "document_id": str(document_id),
        "deleted_vectors": int(deleted_vectors),
    }
    return SuccessResponse(status="success", data=payload, metadata={"correlation_id": cid})


@router.get("/admin/ingest/{job_id}/events", status_code=200)
async def get_ingestion_events(
    job_id: UUID,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    ingestion_repo: IngestionRepository = Depends(_get_ingestion_repo),
) -> SuccessResponse[PaginatedResponse[IngestionEventResponse]]:
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

    page_payload = PaginatedResponse[IngestionEventResponse](
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
    )
    cid = _correlation_id(request)
    return SuccessResponse(status="success", data=page_payload, metadata={"correlation_id": cid})
