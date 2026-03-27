"""
Re-embedding and Chroma upsert for existing indexed documents (Phase 9).
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.core.exceptions import IngestionError
from app.models.document import Document
from app.repositories.document_repo import DocumentRepository
from app.services.ingestion.chunker import Chunk
from app.services.ingestion.indexer import IndexingService
from app.services.vectorstore.chroma_client import ChromaClientWrapper

logger = logging.getLogger(__name__)


class ReindexService:
    """Rebuild embeddings for documents already present in ChromaDB."""

    def __init__(
        self,
        *,
        indexer: IndexingService,
        chroma_client: ChromaClientWrapper,
        document_repo: DocumentRepository,
    ) -> None:
        """
        Initialize reindex orchestrator.

        Args:
            indexer: Embedding + upsert service.
            chroma_client: Vector store access.
            document_repo: Document metadata.
        """
        self._indexer = indexer
        self._chroma = chroma_client
        self._documents = document_repo

    async def reindex_document(self, *, document_id: UUID, job_id: UUID) -> int:
        """
        Re-embed all chunks for a document using text stored in Chroma.

        Args:
            document_id: Document UUID.
            job_id: Parent ingestion job id for audit events.

        Returns:
            Number of chunk vectors written.

        Raises:
            IngestionError: When no chunks exist or indexing fails.
        """
        doc = await self._documents.get_document(document_id)
        rows = await self._chroma.get_document_reindex_rows(
            collection_name=doc.collection_id,
            doc_id=str(doc.id),
        )
        if not rows:
            raise IngestionError(
                "No indexed chunks found for document",
                context={"document_id": str(document_id), "collection_id": doc.collection_id},
            )
        chunks = [_row_to_chunk(doc=doc, text=text, meta=meta) for _vid, text, meta in rows]
        count = await self._indexer.index_chunks(
            chunks=chunks,
            collection_name=doc.collection_id,
            job_id=job_id,
        )
        logger.info(
            "Reindexed document vectors",
            extra={"document_id": str(document_id), "chunk_count": count},
        )
        return count


def _row_to_chunk(
    *,
    doc: Document,
    text: str,
    meta: dict[str, object],
) -> Chunk:
    """Rebuild a Chunk model from Chroma metadata + stored text."""
    v_label = meta.get("version_label", doc.version_label)
    raw_idx = meta.get("chunk_index", 0)
    chunk_index_val = int(raw_idx) if isinstance(raw_idx, int) else int(str(raw_idx or 0))
    return Chunk(
        text=text,
        source_document=doc.title,
        page_or_section=str(meta.get("page_or_section", "")),
        chunk_index=chunk_index_val,
        doc_id=str(doc.id),
        document_title=str(meta.get("document_title", doc.title)),
        collection_id=str(meta.get("collection_id", doc.collection_id)),
        restriction_level=str(meta.get("restriction_level", doc.restriction_level)),
        version_label=str(v_label) if v_label is not None else None,
        is_superseded=bool(meta.get("is_superseded", False)),
    )
