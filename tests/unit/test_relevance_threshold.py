"""
Unit tests for relevance-based generation gating (Phase 7).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.ai.llm_client import LlmCallResult
from app.config import Settings
from app.services.generation.generation_service import GenerationService
from app.services.vectorstore.chroma_client import RetrievedChunk


def _chunk(score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text="t",
        doc_id=str(uuid4()),
        document_title="T",
        page_or_section="p",
        relevance_score=score,
        collection_id="ops",
        restriction_level="restricted",
        chunk_index=0,
    )


@pytest.mark.anyio
async def test_all_low_refuses() -> None:
    settings = Settings(relevance_minimum=0.5, relevance_strong_threshold=0.8)
    llm = MagicMock()
    llm.complete = AsyncMock()
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=[_chunk(0.1), _chunk(0.2)],
        conversation_history=None,
        prompt_version="v1",
        collection_ids_searched=["ops"],
    )
    assert result.refused is True
    assert "Searched collections" in result.answer
    assert "ops" in result.answer
    llm.complete.assert_not_called()


@pytest.mark.anyio
async def test_one_high_proceeds() -> None:
    settings = Settings(
        relevance_minimum=0.1,
        relevance_strong_threshold=0.8,
        openai_api_key="sk-test",
    )
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="Answer",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            latency_ms=1.0,
            model="gpt-4o",
            prompt_version="answer_generation_v1",
        )
    )
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="What is the policy?",
        retrieved_chunks=[_chunk(0.95)],
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.refused is False
    llm.complete.assert_called_once()


@pytest.mark.anyio
async def test_boundary_value() -> None:
    """Exactly at relevance_minimum should allow generation (best >= minimum)."""
    settings = Settings(
        relevance_minimum=0.25,
        relevance_strong_threshold=0.99,
        openai_api_key="sk-test",
    )
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="Answer",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            latency_ms=1.0,
            model="gpt-4o",
            prompt_version="answer_generation_v1",
        )
    )
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=[_chunk(0.25)],
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.refused is False
    assert result.low_confidence is True


@pytest.mark.anyio
async def test_empty_chunks_no_llm_call() -> None:
    settings = Settings()
    llm = MagicMock()
    llm.complete = AsyncMock()
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=[],
        conversation_history=None,
        prompt_version="v1",
        collection_ids_searched=["ops"],
    )
    assert result.refused is True
    llm.complete.assert_not_called()
