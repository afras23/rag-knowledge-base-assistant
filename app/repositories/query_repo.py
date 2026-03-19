"""
Repository for logging query events and LLM calls, and aggregating analytics.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.query import LlmCallAudit, LlmCallType, QueryEvent
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class QueryRepository(BaseRepository):
    """Data access layer for query analytics and LLM auditing."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize query repository."""
        super().__init__(db_session)

    async def log_query_event(
        self,
        *,
        question_hash: str,
        user_group: str | None,
        collection_ids_searched: list[str],
        retrieval_strategy: str,
        chunks_retrieved: int,
        top_relevance_score: float,
        confidence: float,
        refused: bool,
        refusal_reason: str | None,
        tokens_used: int,
        cost_usd: float,
        latency_ms: float,
        prompt_version: str,
        model: str,
    ) -> QueryEvent:
        """
        Persist a query analytics event.

        Returns:
            The created QueryEvent.
        """
        db_query_event = QueryEvent(
            question_hash=question_hash,
            user_group=user_group,
            collection_ids_searched=collection_ids_searched,
            retrieval_strategy=retrieval_strategy,
            chunks_retrieved=chunks_retrieved,
            top_relevance_score=top_relevance_score,
            confidence=confidence,
            refused=refused,
            refusal_reason=refusal_reason,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            prompt_version=prompt_version,
            model=model,
        )
        self.db_session.add(db_query_event)
        await self.db_session.commit()
        await self.db_session.refresh(db_query_event)
        logger.info(
            "Logged query event",
            extra={"query_event_id": str(db_query_event.id), "refused": refused},
        )
        return db_query_event

    async def log_llm_call(
        self,
        *,
        query_event_id: UUID | None,
        call_type: Literal["generation", "rewrite", "evaluation", "embedding"],
        model: str,
        prompt_version: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: float,
    ) -> LlmCallAudit:
        """
        Persist an audit record for an LLM call.
        """
        llm_call_type = LlmCallType(call_type)
        db_llm_call = LlmCallAudit(
            query_event_id=query_event_id,
            call_type=llm_call_type,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self.db_session.add(db_llm_call)
        await self.db_session.commit()
        await self.db_session.refresh(db_llm_call)
        logger.info(
            "Logged LLM call audit",
            extra={"llm_call_id": str(db_llm_call.id), "call_type": call_type},
        )
        return db_llm_call

    async def get_query_analytics(
        self,
        *,
        limit: int = 10,
    ) -> tuple[list[tuple[str, int, int]], int]:
        """
        Aggregate query analytics.

        Returns:
            (top_queries, total_refusals)
            where top_queries is a list of tuples:
            (question_hash, total_count, refusal_count)
        """
        refusal_count_subq = (
            select(
                QueryEvent.question_hash,
                func.count(QueryEvent.id).label("total_count"),
                func.sum(func.case((QueryEvent.refused.is_(True), 1), else_=0)).label("refusal_count"),
            )
            .group_by(QueryEvent.question_hash)
            .order_by(func.count(QueryEvent.id).desc())
            .limit(limit)
        )
        result = await self.db_session.execute(refusal_count_subq)
        rows = result.all()
        top_queries: list[tuple[str, int, int]] = [(str(row[0]), int(row[1]), int(row[2])) for row in rows]

        total_refusals_result = await self.db_session.execute(
            select(func.count()).select_from(QueryEvent).where(QueryEvent.refused.is_(True))
        )
        total_refusals = int(total_refusals_result.scalar_one())
        return top_queries, total_refusals

    async def get_query_event(self, *, query_event_id: UUID) -> QueryEvent:
        """Fetch a query event by ID or raise LookupError."""
        query = select(QueryEvent).where(QueryEvent.id == query_event_id)
        result = await self.db_session.execute(query)
        db_query_event = result.scalar_one_or_none()
        if db_query_event is None:
            raise LookupError(f"QueryEvent not found: {query_event_id}")
        return db_query_event
