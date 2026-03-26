"""
ChromaDB client wrapper for ingestion vector storage operations.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import anyio
from pydantic import BaseModel, Field

from app.config import settings
from app.core.exceptions import IngestionError
from app.services.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

DEFAULT_CHROMA_COLLECTION_PREFIX = ""
DEFAULT_CHROMA_BATCH_SIZE = 128


class RetrievedChunk(BaseModel):
    """Retrieved chunk from vector search."""

    text: str = Field(..., description="Chunk text")
    doc_id: str = Field(..., description="Document UUID as string")
    document_title: str = Field(..., description="Document title")
    page_or_section: str = Field(..., description="Page or section anchor")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Normalized relevance score (0..1)")
    collection_id: str = Field(..., description="Collection ID")
    restriction_level: str = Field(..., description="Restriction level")
    chunk_index: int = Field(..., ge=0, description="Chunk index within document")


class ChromaClientWrapper:
    """ChromaDB wrapper supporting idempotent upserts and queries via HTTP client."""

    def __init__(self, *, chroma_host: str | None = None, chroma_port: int | None = None) -> None:
        self._host = chroma_host or settings.chroma_host
        self._port = int(chroma_port) if chroma_port is not None else int(settings.chroma_port)
        self._collection_prefix = str(getattr(settings, "chroma_collection_prefix", DEFAULT_CHROMA_COLLECTION_PREFIX))
        self._batch_size = int(getattr(settings, "chroma_batch_size", DEFAULT_CHROMA_BATCH_SIZE))
        if self._batch_size <= 0:
            raise IngestionError("chroma_batch_size must be > 0")

        try:
            chromadb = __import__("chromadb")
            self._client = chromadb.HttpClient(host=self._host, port=self._port)
        except ModuleNotFoundError as exc:
            raise IngestionError(
                "chromadb package is not installed",
                context={"required_package": "chromadb"},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to initialize ChromaDB HTTP client",
                context={"host": self._host, "port": self._port, "error": str(exc)},
            ) from exc

        logger.info(
            "Initialized ChromaDB client wrapper",
            extra={"host": self._host, "port": self._port, "collection_prefix": self._collection_prefix},
        )

    async def get_or_create_collection(self, name: str) -> Any:
        collection_name = self._build_collection_name(name)

        def _get_or_create() -> Any:
            return self._client.get_or_create_collection(name=collection_name)

        try:
            return await anyio.to_thread.run_sync(_get_or_create)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to get or create Chroma collection",
                context={"collection_name": collection_name, "error": str(exc)},
            ) from exc

    async def upsert_chunks(self, *, collection_name: str, chunks: list[Chunk], embeddings: list[list[float]]) -> int:
        if len(chunks) != len(embeddings):
            raise IngestionError(
                "Chunk/embedding alignment mismatch",
                context={"chunk_count": len(chunks), "embedding_count": len(embeddings)},
            )
        collection = await self.get_or_create_collection(collection_name)

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, object]] = []

        for chunk in chunks:
            if not chunk.doc_id:
                raise IngestionError("Chunk is missing doc_id for indexing", context={"chunk_index": chunk.chunk_index})
            ids.append(f"{chunk.doc_id}_{chunk.chunk_index}")
            documents.append(chunk.text)
            metadatas.append(
                {
                    "doc_id": chunk.doc_id,
                    "document_title": chunk.document_title or chunk.source_document,
                    "collection_id": chunk.collection_id or collection_name,
                    "restriction_level": chunk.restriction_level or "restricted",
                    "page_or_section": chunk.page_or_section,
                    "chunk_index": chunk.chunk_index,
                    "version_label": chunk.version_label,
                }
            )

        inserted = 0
        for start in range(0, len(ids), self._batch_size):
            batch_ids = ids[start : start + self._batch_size]
            batch_docs = documents[start : start + self._batch_size]
            batch_meta = metadatas[start : start + self._batch_size]
            batch_vecs = embeddings[start : start + self._batch_size]

            def _upsert(
                bids: list[str] = batch_ids,
                bdocs: list[str] = batch_docs,
                bmeta: list[dict[str, object]] = batch_meta,
                bvecs: list[list[float]] = batch_vecs,
            ) -> None:
                collection.upsert(ids=bids, documents=bdocs, metadatas=bmeta, embeddings=bvecs)

            try:
                await anyio.to_thread.run_sync(_upsert)
            except Exception as exc:  # noqa: BLE001
                raise IngestionError(
                    "Failed to upsert chunks into Chroma",
                    context={"collection_name": collection_name, "batch_start": start, "error": str(exc)},
                ) from exc
            inserted += len(batch_ids)

        logger.info(
            "Upserted chunks into Chroma",
            extra={"collection_name": collection_name, "chunk_count": len(chunks), "inserted": inserted},
        )
        return inserted

    async def delete_document_chunks(self, *, collection_name: str, doc_id: str) -> int:
        collection = await self.get_or_create_collection(collection_name)

        def _get_ids() -> list[str]:
            payload = collection.get(where={"doc_id": doc_id}, include=[])
            return [str(identifier) for identifier in payload.get("ids", [])]

        try:
            ids_to_delete = await anyio.to_thread.run_sync(_get_ids)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to query vectors before delete",
                context={"collection_name": collection_name, "doc_id": doc_id, "error": str(exc)},
            ) from exc

        if not ids_to_delete:
            return 0

        def _delete() -> None:
            collection.delete(where={"doc_id": doc_id})

        try:
            await anyio.to_thread.run_sync(_delete)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to delete document chunks",
                context={"collection_name": collection_name, "doc_id": doc_id, "error": str(exc)},
            ) from exc
        return len(ids_to_delete)

    async def query(
        self,
        *,
        collection_name: str,
        query_embedding: list[float],
        n_results: int,
        where_filters: dict[str, object] | None,
    ) -> list[RetrievedChunk]:
        collection = await self.get_or_create_collection(collection_name)

        def _query() -> dict[str, Any]:
            return cast(
                dict[str, Any],
                collection.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    where=where_filters,
                    include=["documents", "metadatas", "distances"],
                ),
            )

        try:
            payload = await anyio.to_thread.run_sync(_query)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Chroma query failed",
                context={"collection_name": collection_name, "error": str(exc)},
            ) from exc

        documents = (payload.get("documents") or [[]])[0]
        metadatas = (payload.get("metadatas") or [[]])[0]
        distances = (payload.get("distances") or [[]])[0]

        retrieved: list[RetrievedChunk] = []
        for doc_text, meta, distance in zip(documents, metadatas, distances, strict=False):
            if not isinstance(meta, dict):
                continue
            raw_distance = float(distance) if distance is not None else 0.0
            relevance_score = max(0.0, min(1.0, 1.0 / (1.0 + raw_distance)))
            retrieved.append(
                RetrievedChunk(
                    text=str(doc_text),
                    doc_id=str(meta.get("doc_id", "")),
                    document_title=str(meta.get("document_title", "")),
                    page_or_section=str(meta.get("page_or_section", "")),
                    relevance_score=relevance_score,
                    collection_id=str(meta.get("collection_id", "")),
                    restriction_level=str(meta.get("restriction_level", "")),
                    chunk_index=int(meta.get("chunk_index", 0)),
                )
            )
        return retrieved

    async def health_check(self) -> bool:
        def _list() -> list[Any]:
            return list(self._client.list_collections())

        try:
            await anyio.to_thread.run_sync(_list)
        except Exception:
            return False
        return True

    def _build_collection_name(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise IngestionError("collection_name is required")
        if not self._collection_prefix:
            return normalized
        return f"{self._collection_prefix}{normalized}"


class ChromaClient:
    """Backwards-compatible adapter for Phase 3 ingestion/admin code."""

    def __init__(self) -> None:
        self._wrapper = ChromaClientWrapper()

    async def get_or_create_collection(self, collection_id: str) -> Any:
        """
        Get or create a Chroma collection.

        Args:
            collection_id: Logical collection ID.

        Returns:
            Chroma collection handle.

        Raises:
            IngestionError: If collection retrieval/creation fails.
        """
        return await self._wrapper.get_or_create_collection(collection_id)

    async def add_documents(
        self,
        *,
        collection_id: str,
        document_id: str,
        chunks: list[Chunk],
        embeddings: list[list[float] | None],
    ) -> int:
        """
        Add embedded chunks to a Chroma collection.

        Args:
            collection_id: Logical collection target.
            document_id: Source document identifier.
            chunks: Chunk list aligned with embeddings.
            embeddings: Embeddings aligned with chunks; None entries are skipped.

        Returns:
            Number of inserted chunk vectors.

        Raises:
            IngestionError: If input alignment is invalid or insertion fails.
        """
        filtered_chunks: list[Chunk] = []
        filtered_embeddings: list[list[float]] = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            if embedding is None:
                continue
            filtered_chunks.append(
                chunk.model_copy(
                    update={
                        "doc_id": document_id,
                        "document_title": chunk.document_title or chunk.source_document,
                        "collection_id": collection_id,
                        "restriction_level": chunk.restriction_level or "restricted",
                    }
                )
            )
            filtered_embeddings.append(embedding)
        if not filtered_chunks:
            return 0
        return await self._wrapper.upsert_chunks(
            collection_name=collection_id,
            chunks=filtered_chunks,
            embeddings=filtered_embeddings,
        )

    async def delete_document(self, *, collection_id: str, document_id: str) -> int:
        """
        Delete all vectors for a document from a collection.

        Args:
            collection_id: Collection identifier.
            document_id: Document identifier.

        Returns:
            Number of deleted vectors when available, otherwise 0.

        Raises:
            IngestionError: If deletion fails.
        """
        return await self._wrapper.delete_document_chunks(collection_name=collection_id, doc_id=document_id)

    async def health_check(self) -> bool:
        """
        Verify ChromaDB availability with a lightweight operation.

        Returns:
            True if collection listing succeeds; False otherwise.
        """

        return await self._wrapper.health_check()
