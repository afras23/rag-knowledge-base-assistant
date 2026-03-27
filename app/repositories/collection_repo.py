"""
Repository for collection catalog lookups.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class CollectionRepository(BaseRepository):
    """Data access for collections table."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize collection repository."""
        super().__init__(db_session)

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
