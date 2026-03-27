"""
Repository for conversations and messages.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, ConversationMessage, ConversationRole
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ConversationRepository(BaseRepository):
    """Persistence for multi-turn chat state."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize conversation repository."""
        super().__init__(db_session)

    async def create_conversation(self, *, user_group: str | None) -> Conversation:
        """
        Create an empty conversation row.

        Args:
            user_group: Optional access-control group label.

        Returns:
            Persisted conversation.
        """
        row = Conversation(user_group=user_group)
        self.db_session.add(row)
        await self.db_session.commit()
        await self.db_session.refresh(row)
        logger.info(
            "Created conversation",
            extra={"conversation_id": str(row.id)},
        )
        return row

    async def add_message(
        self,
        *,
        conversation_id: UUID,
        role: ConversationRole,
        content: str,
        citations_json: list[dict[str, object]] | None,
        refused: bool,
    ) -> ConversationMessage:
        """
        Append a message to a conversation.

        Args:
            conversation_id: Parent conversation id.
            role: Speaker role.
            content: Message body.
            citations_json: Optional serialized citations for assistant turns.
            refused: Whether this assistant turn was a refusal.

        Returns:
            Persisted message row.
        """
        msg = ConversationMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations_json=citations_json,
            refused=refused,
        )
        self.db_session.add(msg)
        await self.db_session.commit()
        await self.db_session.refresh(msg)
        logger.info(
            "Added conversation message",
            extra={"conversation_id": str(conversation_id), "role": role.value},
        )
        return msg

    async def get_conversation(self, *, conversation_id: UUID) -> Conversation | None:
        """
        Load a conversation with messages ordered by creation time.

        Args:
            conversation_id: Conversation primary key.

        Returns:
            Conversation or None when missing.
        """
        stmt = (
            select(Conversation).where(Conversation.id == conversation_id).options(selectinload(Conversation.messages))
        )
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.messages.sort(key=lambda m: m.created_at)
        return row

    async def get_recent_message_history(
        self,
        *,
        conversation_id: UUID,
        max_messages: int,
    ) -> list[dict[str, str]]:
        """
        Return up to the last ``max_messages`` turns as role/content dicts.

        Args:
            conversation_id: Conversation to read.
            max_messages: Maximum messages to include (oldest dropped first).

        Returns:
            Ordered list of ``{"role": "...", "content": "..."}`` entries.
        """
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(max_messages)
        )
        result = await self.db_session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return [{"role": m.role.value, "content": m.content} for m in rows]

    async def get_conversations(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Conversation], int]:
        """
        Paginate conversations by most recently updated.

        Args:
            page: 1-indexed page number.
            page_size: Page size.

        Returns:
            (items, total_count)
        """
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be >= 1")
        offset = (page - 1) * page_size
        count_result = await self.db_session.execute(select(func.count()).select_from(Conversation))
        total = int(count_result.scalar_one())
        stmt = select(Conversation).order_by(Conversation.updated_at.desc()).offset(offset).limit(page_size)
        list_result = await self.db_session.execute(stmt)
        items = list(list_result.scalars().all())
        return items, total
