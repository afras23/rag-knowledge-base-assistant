"""
FastAPI dependency providers for database sessions and query orchestration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.guardrails import GuardrailService
from app.ai.llm_client import LlmClient
from app.ai.pii_detector import PiiDetector
from app.config import settings
from app.core.database import async_session_factory
from app.repositories.collection_repo import CollectionRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.query_repo import QueryRepository
from app.services.generation.generation_service import GenerationService
from app.services.ingestion.embedder import get_embedding_provider
from app.services.query_service import QueryService
from app.services.retrieval.retrieval_service import RetrievalService
from app.services.vectorstore.chroma_client import ChromaClientWrapper


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped async SQLAlchemy session."""
    async with async_session_factory() as session:
        yield session


async def get_query_service(
    session: AsyncSession = Depends(get_db_session),
) -> QueryService:
    """
    Build a QueryService with shared LLM, Chroma, and embedding clients per request.

    Args:
        session: Database session for repositories.

    Returns:
        Configured ``QueryService`` instance.
    """
    llm_client = LlmClient(settings=settings)
    chroma_client = ChromaClientWrapper()
    embedding_provider = get_embedding_provider()
    retrieval_service = RetrievalService(
        chroma_client=chroma_client,
        embedding_provider=embedding_provider,
        settings=settings,
        llm_client=llm_client,
    )
    query_repo = QueryRepository(session)
    generation_service = GenerationService(
        llm_client=llm_client,
        settings=settings,
        query_repo=query_repo,
    )
    return QueryService(
        settings=settings,
        guardrail_service=GuardrailService(),
        pii_detector=PiiDetector(settings),
        retrieval_service=retrieval_service,
        generation_service=generation_service,
        conversation_repo=ConversationRepository(session),
        collection_repo=CollectionRepository(session),
        query_repo=query_repo,
    )
