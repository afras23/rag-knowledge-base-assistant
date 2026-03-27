"""
Repository for collection catalog CRUD (Phase 9 admin).
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.document import Document
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class CollectionRepository(BaseRepository):
    """Persistence for logical document collections."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize collection repository."""
        super().__init__(db_session)

    async def create_collection(
        self,
        *,
        collection_id: str,
        name: str,
        description: str,
        allowed_roles: list[str],
    ) -> Collection:
        """
        Insert a new collection row.

        Raises:
            IntegrityError: When ``collection_id`` already exists.
        """
        row = Collection(
            id=collection_id,
            name=name,
            description=description,
            allowed_roles=list(allowed_roles),
        )
        self.db_session.add(row)
        try:
            await self.db_session.commit()
        except IntegrityError as exc:
            await self.db_session.rollback()
            logger.error(
                "Failed to create collection",
                extra={"collection_id": collection_id, "error": str(exc)},
            )
            raise
        await self.db_session.refresh(row)
        logger.info("Created collection", extra={"collection_id": collection_id})
        return row

    async def get_by_id(self, *, collection_id: str) -> Collection | None:
        """Return collection by primary key or None."""
        stmt = select(Collection).where(Collection.id == collection_id)
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all_collection_ids(self) -> list[str]:
        """
        Return all collection primary keys (for default retrieval scope).

        Returns:
            Stable collection id strings, possibly empty.
        """
        result = await self.db_session.execute(select(Collection.id))
        ids = [str(row) for row in result.scalars().all()]
        logger.info(
            "Listed collection ids",
            extra={"count": len(ids)},
        )
        return ids

    async def list_collections(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Collection], int]:
        """
        Paginate collections by id ascending.

        Returns:
            (items, total_count)
        """
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be >= 1")
        offset = (page - 1) * page_size
        count_result = await self.db_session.execute(select(func.count()).select_from(Collection))
        total = int(count_result.scalar_one())
        stmt = select(Collection).order_by(Collection.id.asc()).offset(offset).limit(page_size)
        list_result = await self.db_session.execute(stmt)
        return list(list_result.scalars().all()), total

    async def update_collection(
        self,
        *,
        collection_id: str,
        name: str,
        description: str,
        allowed_roles: list[str],
    ) -> Collection | None:
        """Update collection fields. Returns updated row or None if missing."""
        existing = await self.get_by_id(collection_id=collection_id)
        if existing is None:
            return None
        await self.db_session.execute(
            update(Collection)
            .where(Collection.id == collection_id)
            .values(
                name=name,
                description=description,
                allowed_roles=list(allowed_roles),
            )
        )
        await self.db_session.commit()
        logger.info("Updated collection", extra={"collection_id": collection_id})
        return await self.get_by_id(collection_id=collection_id)

    async def count_documents_in_collection(self, *, collection_id: str) -> int:
        """Return how many documents reference this collection."""
        stmt = select(func.count()).select_from(Document).where(Document.collection_id == collection_id)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one())

    async def delete_collection_if_empty(self, *, collection_id: str) -> bool:
        """
        Delete collection when it has no documents.

        Returns:
            True when a row was deleted, False when collection had documents or did not exist.
        """
        existing = await self.get_by_id(collection_id=collection_id)
        if existing is None:
            return False
        count = await self.count_documents_in_collection(collection_id=collection_id)
        if count > 0:
            return False
        await self.db_session.delete(existing)
        await self.db_session.commit()
        logger.info("Deleted empty collection", extra={"collection_id": collection_id})
        return True

    async def delete_collection_by_id(self, *, collection_id: str) -> None:
        """Delete a collection row when the caller has verified it is safe to remove."""
        existing = await self.get_by_id(collection_id=collection_id)
        if existing is None:
            return
        await self.db_session.delete(existing)
        await self.db_session.commit()
        logger.info("Deleted collection", extra={"collection_id": collection_id})
