"""
Repository for document metadata persistence and versioning operations.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DocumentNotFoundError
from app.models.document import Document
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class DocumentRepository(BaseRepository):
    """Data access layer for documents."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize document repository."""
        super().__init__(db_session)

    async def create_document(
        self,
        *,
        title: str,
        file_format: str,
        collection_id: str,
        restriction_level: Literal["public", "restricted", "confidential"],
        content_hash: str,
        version_label: str | None,
        supersedes_id: UUID | None,
        metadata_json: dict[str, Any],
        chunk_count: int,
    ) -> Document:
        """
        Create a document record.

        Raises:
            IntegrityError: If content_hash uniqueness is violated.
        """
        db_document = Document(
            title=title,
            file_format=file_format,
            collection_id=collection_id,
            restriction_level=restriction_level,
            content_hash=content_hash,
            version_label=version_label,
            supersedes_id=supersedes_id,
            chunk_count=chunk_count,
            metadata_json=metadata_json,
        )
        self.db_session.add(db_document)
        try:
            await self.db_session.commit()
        except IntegrityError as exc:
            await self.db_session.rollback()
            logger.error(
                "Failed to create document (integrity error)",
                extra={"content_hash": content_hash, "error": str(exc)},
            )
            raise
        await self.db_session.refresh(db_document)
        logger.info(
            "Created document",
            extra={"document_id": str(db_document.id), "collection_id": collection_id},
        )
        return db_document

    async def find_by_content_hash(self, content_hash: str) -> Document | None:
        """Find a document by content hash (idempotency lookup)."""
        query = select(Document).where(Document.content_hash == content_hash)
        result = await self.db_session.execute(query)
        return result.scalar_one_or_none()

    async def get_document(self, document_id: UUID) -> Document:
        """Fetch a document by ID or raise DocumentNotFoundError."""
        query = select(Document).where(Document.id == document_id)
        result = await self.db_session.execute(query)
        db_document = result.scalar_one_or_none()
        if db_document is None:
            raise DocumentNotFoundError(
                context={"document_id": str(document_id)},
            )
        return db_document

    async def list_documents(
        self,
        *,
        collection_id: str | None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Document], int]:
        """
        List documents with pagination.

        Returns:
            (items, total_count)
        """
        offset = (page - 1) * page_size
        count_query = select(func.count()).select_from(Document)
        if collection_id:
            count_query = count_query.where(Document.collection_id == collection_id)
        total_result = await self.db_session.execute(count_query)
        total_count = int(total_result.scalar_one())

        query = select(Document).order_by(Document.created_at.desc()).offset(offset).limit(page_size)
        if collection_id:
            query = query.where(Document.collection_id == collection_id)
        items_result = await self.db_session.execute(query)
        items = list(items_result.scalars().all())
        return items, total_count

    async def mark_superseded(self, *, superseded_id: UUID, supersedes_id: UUID) -> None:
        """
        Mark an existing document as superseded by another.

        This updates both documents for consistent bidirectional versioning.
        """
        # Update old doc -> superseded_by_id
        await self.db_session.execute(
            update(Document).where(Document.id == superseded_id).values(superseded_by_id=supersedes_id)
        )
        # Update new doc -> supersedes_id
        await self.db_session.execute(
            update(Document).where(Document.id == supersedes_id).values(supersedes_id=superseded_id)
        )
        await self.db_session.commit()
        logger.info(
            "Marked document as superseded",
            extra={"superseded_id": str(superseded_id), "supersedes_id": str(supersedes_id)},
        )

    async def delete_document(self, document_id: UUID) -> None:
        """Delete a document by ID."""
        await self.db_session.execute(delete(Document).where(Document.id == document_id))
        await self.db_session.commit()
        logger.info("Deleted document", extra={"document_id": str(document_id)})
