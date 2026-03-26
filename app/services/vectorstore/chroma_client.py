"""
ChromaDB client wrapper for ingestion vector storage operations.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio

from app.config import settings
from app.core.exceptions import IngestionError
from app.services.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

DEFAULT_CHROMA_PERSIST_DIRECTORY = "./.chroma"
DEFAULT_CHROMA_COLLECTION_PREFIX = ""
DEFAULT_CHROMA_BATCH_SIZE = 128


class ChromaClient:
    """Thin ChromaDB wrapper focused on ingestion-time write/delete operations."""

    def __init__(self) -> None:
        """Initialize persistent ChromaDB client from application settings."""
        self._persist_directory = str(getattr(settings, "chroma_persist_directory", DEFAULT_CHROMA_PERSIST_DIRECTORY))
        self._collection_prefix = str(getattr(settings, "chroma_collection_prefix", DEFAULT_CHROMA_COLLECTION_PREFIX))
        self._batch_size = int(getattr(settings, "chroma_batch_size", DEFAULT_CHROMA_BATCH_SIZE))
        if self._batch_size <= 0:
            raise IngestionError("chroma_batch_size must be > 0")

        try:
            chromadb = __import__("chromadb")
            self._client = chromadb.PersistentClient(path=self._persist_directory)
        except ModuleNotFoundError as exc:
            raise IngestionError(
                "chromadb package is not installed",
                context={"required_package": "chromadb"},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to initialize ChromaDB client",
                context={"persist_directory": self._persist_directory, "error": str(exc)},
            ) from exc

        logger.info(
            "Initialized ChromaDB client",
            extra={
                "persist_directory": self._persist_directory,
                "collection_prefix": self._collection_prefix,
                "batch_size": self._batch_size,
            },
        )

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
        collection_name = self._build_collection_name(collection_id)

        def _get_or_create() -> Any:
            return self._client.get_or_create_collection(name=collection_name)

        try:
            collection = await anyio.to_thread.run_sync(_get_or_create)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to get or create Chroma collection",
                context={"collection_id": collection_id, "collection_name": collection_name, "error": str(exc)},
            ) from exc

        logger.info(
            "Chroma collection ready",
            extra={"collection_id": collection_id, "collection_name": collection_name},
        )
        return collection

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
        if len(chunks) != len(embeddings):
            raise IngestionError(
                "Chunk/embedding alignment mismatch",
                context={"chunk_count": len(chunks), "embedding_count": len(embeddings)},
            )

        collection = await self.get_or_create_collection(collection_id)
        records: list[tuple[str, str, dict[str, object], list[float]]] = []

        for chunk, embedding in zip(chunks, embeddings, strict=True):
            if embedding is None:
                continue
            chunk_identifier = f"{document_id}_{chunk.chunk_index}"
            metadata = {
                "document_id": document_id,
                "source_document": chunk.source_document,
                "page_or_section": chunk.page_or_section,
                "chunk_index": chunk.chunk_index,
            }
            records.append((chunk_identifier, chunk.text, metadata, embedding))

        if not records:
            logger.info(
                "No chunk vectors inserted (all embeddings missing)",
                extra={"collection_id": collection_id, "document_id": document_id, "chunk_count": len(chunks)},
            )
            return 0

        inserted_count = 0
        for start_index in range(0, len(records), self._batch_size):
            batch_records = records[start_index : start_index + self._batch_size]
            ids = [record[0] for record in batch_records]
            documents = [record[1] for record in batch_records]
            metadatas = [record[2] for record in batch_records]
            batch_embeddings = [record[3] for record in batch_records]

            def _add_batch(
                batch_ids: list[str] = ids,
                batch_documents: list[str] = documents,
                batch_metadatas: list[dict[str, object]] = metadatas,
                batch_vectors: list[list[float]] = batch_embeddings,
            ) -> None:
                collection.add(
                    ids=batch_ids,
                    documents=batch_documents,
                    metadatas=batch_metadatas,
                    embeddings=batch_vectors,
                )

            try:
                await anyio.to_thread.run_sync(_add_batch)
            except Exception as exc:  # noqa: BLE001
                raise IngestionError(
                    "Failed to insert chunk vectors into Chroma",
                    context={
                        "collection_id": collection_id,
                        "document_id": document_id,
                        "batch_start": start_index,
                        "batch_size": len(batch_records),
                        "error": str(exc),
                    },
                ) from exc
            inserted_count += len(batch_records)

        logger.info(
            "Inserted chunk vectors into Chroma",
            extra={
                "collection_id": collection_id,
                "document_id": document_id,
                "inserted_count": inserted_count,
                "requested_chunks": len(chunks),
            },
        )
        return inserted_count

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
        collection = await self.get_or_create_collection(collection_id)

        def _get_ids() -> list[str]:
            payload = collection.get(where={"document_id": document_id}, include=[])
            ids = payload.get("ids", [])
            return [str(identifier) for identifier in ids]

        try:
            ids_to_delete = await anyio.to_thread.run_sync(_get_ids)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to query document vectors before delete",
                context={"collection_id": collection_id, "document_id": document_id, "error": str(exc)},
            ) from exc

        if not ids_to_delete:
            logger.info(
                "No vectors found to delete",
                extra={"collection_id": collection_id, "document_id": document_id},
            )
            return 0

        def _delete() -> None:
            collection.delete(where={"document_id": document_id})

        try:
            await anyio.to_thread.run_sync(_delete)
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to delete document vectors",
                context={
                    "collection_id": collection_id,
                    "document_id": document_id,
                    "candidate_count": len(ids_to_delete),
                    "error": str(exc),
                },
            ) from exc

        logger.info(
            "Deleted document vectors from Chroma",
            extra={
                "collection_id": collection_id,
                "document_id": document_id,
                "deleted_count": len(ids_to_delete),
            },
        )
        return len(ids_to_delete)

    async def health_check(self) -> bool:
        """
        Verify ChromaDB availability with a lightweight operation.

        Returns:
            True if collection listing succeeds; False otherwise.
        """

        def _list_collections() -> list[Any]:
            return list(self._client.list_collections())

        try:
            collection_list = await anyio.to_thread.run_sync(_list_collections)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Chroma health check failed",
                extra={"persist_directory": self._persist_directory, "error": str(exc)},
            )
            return False

        logger.info(
            "Chroma health check passed",
            extra={"collection_count": len(collection_list), "persist_directory": self._persist_directory},
        )
        return True

    def _build_collection_name(self, collection_id: str) -> str:
        """Build physical collection name from optional prefix plus collection id."""
        normalized_collection_id = collection_id.strip()
        if not normalized_collection_id:
            raise IngestionError("collection_id is required")
        if not self._collection_prefix:
            return normalized_collection_id
        return f"{self._collection_prefix}{normalized_collection_id}"
