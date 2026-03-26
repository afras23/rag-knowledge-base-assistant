"""
Ingestion pipeline orchestrator (Phase 3 — Component 4).

This service coordinates parse -> chunk -> embed steps and prepares data for
future vector storage integration.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.exceptions import IngestionError
from app.models.ingestion import IngestionEventStatus, IngestionJobStatus
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import Chunk, DocumentChunker
from app.services.ingestion.embedder import DocumentEmbedder, EmbeddingResult
from app.services.ingestion.parsers import ParsedDocument, get_parser
from app.services.vectorstore.chroma_client import ChromaClient

logger = logging.getLogger(__name__)


class IngestionResult(BaseModel):
    """Summary of a multi-document ingestion run."""

    total_documents: int = Field(..., ge=0, description="Total input documents discovered")
    processed: int = Field(..., ge=0, description="Documents processed (including skipped)")
    failed: int = Field(..., ge=0, description="Documents that failed during orchestration")
    skipped: int = Field(..., ge=0, description="Documents skipped due to idempotency")


@dataclass(frozen=True)
class PreparedStoragePayload:
    """Prepared document/chunk data for a future vector storage adapter."""

    document_id: str
    collection_id: str
    chunks: list[Chunk]
    embeddings: list[list[float] | None]


class IngestionPipeline:
    """Orchestrates parse -> chunk -> embed flow for a document batch."""

    def __init__(
        self,
        *,
        ingestion_repo: IngestionRepository,
        document_repo: DocumentRepository,
        chunker: DocumentChunker,
        embedder: DocumentEmbedder,
        chroma_client: ChromaClient,
    ) -> None:
        """
        Initialize ingestion orchestrator with explicit dependencies.

        Args:
            ingestion_repo: Repository for ingestion job/event tracking.
            document_repo: Repository for document metadata and idempotency checks.
            chunker: Chunking service.
            embedder: Embedding service.
            chroma_client: ChromaDB vector storage client.
        """
        self._ingestion_repo = ingestion_repo
        self._document_repo = document_repo
        self._chunker = chunker
        self._embedder = embedder
        self._chroma_client = chroma_client

    async def ingest_documents(
        self,
        *,
        collection_id: str,
        file_paths: list[Path] | None = None,
        directory_path: Path | None = None,
        restriction_level: Literal["public", "restricted", "confidential"] = "restricted",
        created_by: str | None = None,
    ) -> IngestionResult:
        """
        Ingest a document set from explicit paths or a directory.

        Args:
            collection_id: Collection target for ingested documents.
            file_paths: Optional explicit file paths.
            directory_path: Optional directory to scan recursively.
            restriction_level: Restriction level applied to created documents.
            created_by: Optional user/service identifier.

        Returns:
            IngestionResult summary for the batch.

        Raises:
            IngestionError: If no inputs are provided or path discovery fails.
        """
        source_paths = self._resolve_source_paths(file_paths=file_paths, directory_path=directory_path)
        total_documents = len(source_paths)
        ingestion_job = await self._ingestion_repo.create_job(total_documents=total_documents, created_by=created_by)

        processed = 0
        failed = 0
        skipped = 0
        succeeded = 0

        await self._ingestion_repo.update_job_progress(
            job_id=ingestion_job.id,
            status=IngestionJobStatus.processing,
            processed=0,
            succeeded=0,
            failed=0,
        )
        logger.info(
            "Ingestion batch started",
            extra={"job_id": str(ingestion_job.id), "total_documents": total_documents, "collection_id": collection_id},
        )

        for source_path in source_paths:
            document_start = time.perf_counter()
            try:
                result_type, created_document_id = await self._ingest_single_document(
                    source_path=source_path,
                    collection_id=collection_id,
                    restriction_level=restriction_level,
                    job_id=ingestion_job.id,
                )
                if result_type == "skipped":
                    skipped += 1
                else:
                    succeeded += 1

                if created_document_id:
                    duration_ms = (time.perf_counter() - document_start) * 1000.0
                    await self._ingestion_repo.log_ingestion_event(
                        job_id=ingestion_job.id,
                        document_id=created_document_id,
                        stage="orchestration",
                        status=IngestionEventStatus.success
                        if result_type == "ingested"
                        else IngestionEventStatus.skipped,
                        error_message=None,
                        duration_ms=duration_ms,
                    )
            except Exception as ingestion_exc:  # noqa: BLE001
                failed += 1
                logger.error(
                    "Document ingestion failed",
                    extra={
                        "job_id": str(ingestion_job.id),
                        "source_path": str(source_path),
                        "collection_id": collection_id,
                        "error": str(ingestion_exc),
                    },
                )
            finally:
                processed += 1
                await self._ingestion_repo.update_job_progress(
                    job_id=ingestion_job.id,
                    status=IngestionJobStatus.processing,
                    processed=processed,
                    succeeded=succeeded,
                    failed=failed,
                )

        final_status = (
            IngestionJobStatus.failed
            if failed == total_documents and total_documents > 0
            else IngestionJobStatus.completed
        )
        await self._ingestion_repo.update_job_progress(
            job_id=ingestion_job.id,
            status=final_status,
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            completed_at=datetime.now(timezone.utc),
        )
        logger.info(
            "Ingestion batch completed",
            extra={
                "job_id": str(ingestion_job.id),
                "total_documents": total_documents,
                "processed": processed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
            },
        )
        return IngestionResult(total_documents=total_documents, processed=processed, failed=failed, skipped=skipped)

    async def _ingest_single_document(
        self,
        *,
        source_path: Path,
        collection_id: str,
        restriction_level: Literal["public", "restricted", "confidential"],
        job_id: UUID,
    ) -> tuple[Literal["ingested", "skipped"], UUID | None]:
        """Ingest one document and return result type plus document id."""
        detected_format = self._detect_file_format(source_path)
        parsed_document = await get_parser(detected_format).parse_path(source_path)
        content_hash = self._calculate_content_hash(parsed_document)

        existing_document = await self._document_repo.find_by_content_hash(content_hash)
        if existing_document is not None:
            logger.info(
                "Skipped duplicate document",
                extra={
                    "job_id": str(job_id),
                    "source_path": str(source_path),
                    "document_id": str(existing_document.id),
                    "content_hash": content_hash,
                },
            )
            return "skipped", existing_document.id

        chunk_list = self._chunker.chunk_document(parsed_document)
        embeddings, embedding_result = await self._embedder.embed_chunks(chunk_list)

        metadata_payload = self._build_metadata_payload(
            parsed_document=parsed_document, embedding_result=embedding_result
        )
        created_document = await self._document_repo.create_document(
            title=source_path.stem,
            file_format=detected_format,
            collection_id=collection_id,
            restriction_level=restriction_level,
            content_hash=content_hash,
            version_label=None,
            supersedes_id=None,
            metadata_json=metadata_payload,
            chunk_count=len(chunk_list),
        )

        try:
            inserted_count = await self._chroma_client.add_documents(
                collection_id=collection_id,
                document_id=str(created_document.id),
                chunks=chunk_list,
                embeddings=embeddings,
            )
        except Exception as chroma_exc:  # noqa: BLE001
            # Ensure no partial success: if Chroma insertion fails, remove the DB record.
            await self._document_repo.delete_document(created_document.id)
            raise IngestionError(
                "Failed to store document vectors in Chroma; document creation rolled back",
                context={
                    "document_id": str(created_document.id),
                    "collection_id": collection_id,
                    "source_path": str(source_path),
                    "error": str(chroma_exc),
                },
            ) from chroma_exc

        _prepared_payload = PreparedStoragePayload(
            document_id=str(created_document.id),
            collection_id=collection_id,
            chunks=chunk_list,
            embeddings=embeddings,
        )
        logger.info(
            "Prepared and stored document vectors",
            extra={
                "job_id": str(job_id),
                "document_id": str(created_document.id),
                "collection_id": collection_id,
                "chunk_count": len(chunk_list),
                "embedded_chunks": embedding_result.embedded_chunks,
                "inserted_vector_count": inserted_count,
            },
        )
        return "ingested", created_document.id

    @staticmethod
    def _calculate_content_hash(parsed_document: ParsedDocument) -> str:
        """Create SHA-256 hash from normalized parsed full text for idempotency."""
        digest = hashlib.sha256(parsed_document.full_text.encode("utf-8")).hexdigest()
        return digest

    @staticmethod
    def _detect_file_format(source_path: Path) -> Literal["pdf", "docx", "markdown"]:
        """Infer parser format from file extension."""
        suffix = source_path.suffix.lower().strip()
        if suffix == ".pdf":
            return "pdf"
        if suffix == ".docx":
            return "docx"
        if suffix in {".md", ".markdown"}:
            return "markdown"
        raise IngestionError(
            "Unsupported file extension for ingestion",
            context={"source_path": str(source_path), "suffix": suffix},
        )

    @staticmethod
    def _resolve_source_paths(
        *,
        file_paths: list[Path] | None,
        directory_path: Path | None,
    ) -> list[Path]:
        """Resolve and validate source paths from explicit paths or a directory."""
        if file_paths and directory_path:
            raise IngestionError("Provide either file_paths or directory_path, not both")
        if not file_paths and not directory_path:
            raise IngestionError("At least one ingestion source is required")

        if file_paths:
            resolved_files = [path.resolve() for path in file_paths]
        else:
            assert directory_path is not None
            if not directory_path.exists() or not directory_path.is_dir():
                raise IngestionError(
                    "Directory path does not exist or is not a directory",
                    context={"directory_path": str(directory_path)},
                )
            resolved_files = sorted(path.resolve() for path in directory_path.rglob("*") if path.is_file())

        filtered_files = [
            path for path in resolved_files if path.suffix.lower() in {".pdf", ".docx", ".md", ".markdown"}
        ]
        if not filtered_files:
            raise IngestionError("No supported files found for ingestion")
        return filtered_files

    @staticmethod
    def _build_metadata_payload(
        parsed_document: ParsedDocument, embedding_result: EmbeddingResult
    ) -> dict[str, object]:
        """Build metadata JSON for persisted document records."""
        return {
            "filename": parsed_document.metadata.filename,
            "format": parsed_document.metadata.format,
            "page_count": parsed_document.metadata.page_count,
            "section_count": len(parsed_document.sections),
            "embedding": {
                "total_chunks": embedding_result.total_chunks,
                "embedded_chunks": embedding_result.embedded_chunks,
                "failed_chunks": embedding_result.failed_chunks,
                "total_cost": embedding_result.total_cost,
            },
        }
