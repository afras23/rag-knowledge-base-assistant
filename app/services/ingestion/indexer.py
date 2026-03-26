"""
Indexing service for embeddings + ChromaDB upserts (Phase 4).

This service embeds chunks using the configured embedding provider and upserts
them into ChromaDB with deterministic IDs for idempotent re-indexing.
"""

from __future__ import annotations

import logging
import random
import time
from uuid import UUID

import anyio

from app.config import settings
from app.core.exceptions import IngestionError
from app.models.ingestion import IngestionEventStatus
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import Chunk
from app.services.ingestion.embedder import EmbeddingProvider, OpenAIEmbeddingProvider
from app.services.vectorstore.chroma_client import ChromaClientWrapper

logger = logging.getLogger(__name__)


class IndexingService:
    """Embed chunks and upsert them into ChromaDB."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        chroma_client: ChromaClientWrapper,
        ingestion_repo: IngestionRepository,
    ) -> None:
        """
        Initialize indexing service.

        Args:
            embedding_provider: Configured embedding provider implementation.
            chroma_client: Chroma client wrapper for upsert/query/delete.
            ingestion_repo: Repository for recording indexing events.
        """
        self._embedding_provider = embedding_provider
        self._chroma_client = chroma_client
        self._ingestion_repo = ingestion_repo

    async def index_chunks(self, *, chunks: list[Chunk], collection_name: str, job_id: UUID) -> int:
        """
        Embed chunks in batches and upsert into ChromaDB.

        Args:
            chunks: Indexable chunks (must include doc_id metadata).
            collection_name: Target collection name in ChromaDB.
            job_id: Ingestion job ID for event tracking.

        Returns:
            Number of chunk vectors indexed.

        Raises:
            IngestionError: If embedding or upsert fails.
        """
        if not chunks:
            return 0

        start_time = time.perf_counter()
        indexed_total = 0
        total_cost = 0.0

        for batch_start in range(0, len(chunks), settings.embed_batch_size):
            batch_end = min(batch_start + settings.embed_batch_size, len(chunks))
            batch_chunks = chunks[batch_start:batch_end]
            batch_texts = [chunk.text for chunk in batch_chunks]

            vectors = await self._embed_with_retry(batch_texts)
            batch_cost = 0.0
            if (
                isinstance(self._embedding_provider, OpenAIEmbeddingProvider)
                and self._embedding_provider.last_batch_telemetry
            ):
                batch_cost = float(self._embedding_provider.last_batch_telemetry.cost_usd)
            total_cost += batch_cost

            indexed = await self._chroma_client.upsert_chunks(
                collection_name=collection_name,
                chunks=batch_chunks,
                embeddings=vectors,
            )
            indexed_total += indexed

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "Indexing completed",
            extra={
                "job_id": str(job_id),
                "collection_name": collection_name,
                "chunks_indexed": indexed_total,
                "embedding_model": self._embedding_provider.model_name,
                "latency_ms": latency_ms,
                "cost_usd": round(total_cost, 8),
            },
        )

        # Record a single indexing event for the document (if doc_id present).
        doc_id = next((chunk.doc_id for chunk in chunks if chunk.doc_id), None)
        if doc_id:
            await self._ingestion_repo.log_ingestion_event(
                job_id=job_id,
                document_id=UUID(doc_id),
                stage="indexing",
                status=IngestionEventStatus.success,
                error_message=None,
                duration_ms=latency_ms,
            )

        return indexed_total

    async def delete_document_index(self, *, doc_id: str, collection_name: str) -> int:
        """
        Delete all chunk vectors for a document.

        Args:
            doc_id: Document UUID as string.
            collection_name: Chroma collection name.

        Returns:
            Deleted vector count (best-effort).
        """
        return await self._chroma_client.delete_document_chunks(collection_name=collection_name, doc_id=doc_id)

    async def reindex_collection(self, *, collection_name: str) -> None:
        """
        Clear a Chroma collection in preparation for reindexing.

        Note:
            Full corpus reindex orchestration (re-parsing source documents and re-embedding)
            is implemented in a later phase once document source locations are persisted.

        Args:
            collection_name: Target collection name in ChromaDB.

        Raises:
            IngestionError: If the collection cannot be cleared.
        """
        collection = await self._chroma_client.get_or_create_collection(collection_name)

        def _clear() -> None:
            collection.delete(where={})

        try:
            await anyio.to_thread.run_sync(_clear)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to clear Chroma collection for reindex",
                context={"collection_name": collection_name, "error": str(exc)},
            ) from exc

    async def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        """Embed with exponential backoff and jitter on transient failures."""
        last_error: Exception | None = None
        for attempt in range(1, settings.embed_max_retries + 1):
            try:
                batch_start = time.perf_counter()
                vectors = await self._embedding_provider.embed_texts(texts)
                latency_ms = (time.perf_counter() - batch_start) * 1000.0
                logger.info(
                    "Embedding batch completed (indexer)",
                    extra={
                        "embedding_model": self._embedding_provider.model_name,
                        "batch_size": len(texts),
                        "latency_ms": latency_ms,
                    },
                )
                if len(vectors) != len(texts):
                    raise IngestionError(
                        "Embedding provider returned mismatched vector count",
                        context={"requested": len(texts), "returned": len(vectors)},
                    )
                return vectors
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= settings.embed_max_retries:
                    break

                backoff_seconds = settings.embed_initial_backoff_seconds * (2**attempt) * random.uniform(0.8, 1.2)
                await anyio.sleep(backoff_seconds)

        raise IngestionError(
            "Embedding failed during indexing after retries", context={"error": str(last_error)}
        ) from last_error
