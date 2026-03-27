"""
Unit tests for grounded generation service.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.ai.llm_client import LlmCallResult
from app.config import Settings
from app.services.generation.generation_service import GenerationService
from app.services.vectorstore.chroma_client import RetrievedChunk


def _chunk(doc_id: str, title: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text="body text",
        doc_id=doc_id,
        document_title=title,
        page_or_section="Sec 1",
        relevance_score=score,
        collection_id="c1",
        restriction_level="restricted",
        chunk_index=0,
    )


@pytest.mark.anyio
async def test_generates_answer_with_citations() -> None:
    settings = Settings(relevance_minimum=0.1, openai_api_key="sk-test")
    doc_id = str(uuid4())
    chunks = [_chunk(doc_id, "Policy Manual", 0.9)]
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="Answer [Source: Policy Manual, Sec 1]",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            latency_ms=12.0,
            model="gpt-4o",
            prompt_version="answer_generation_v1",
        )
    )
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=chunks,
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.refused is False
    assert "Answer" in result.answer
    assert len(result.citations) == 1
    assert result.citations[0].document_title == "Policy Manual"


@pytest.mark.anyio
async def test_refuses_when_no_chunks() -> None:
    settings = Settings()
    llm = MagicMock()
    llm.complete = AsyncMock()
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=[],
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.refused is True
    llm.complete.assert_not_called()


@pytest.mark.anyio
async def test_refuses_when_low_relevance() -> None:
    settings = Settings(relevance_minimum=0.9)
    chunks = [_chunk(str(uuid4()), "Low", 0.1)]
    llm = MagicMock()
    llm.complete = AsyncMock()
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    result = await svc.generate_answer(
        query="Q?",
        retrieved_chunks=chunks,
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.refused is True
    llm.complete.assert_not_called()


@pytest.mark.anyio
async def test_citation_matches_chunks() -> None:
    settings = Settings(relevance_minimum=0.1, openai_api_key="sk-test")
    doc_id = str(uuid4())
    chunks = [_chunk(doc_id, "Exact Title", 0.5)]
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="See [Source: Exact Title, Sec 1]",
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
        retrieved_chunks=chunks,
        conversation_history=None,
        prompt_version="v1",
    )
    assert len(result.citations) == 1
    assert str(result.citations[0].doc_id) == doc_id


@pytest.mark.anyio
async def test_unmatched_citation_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    settings = Settings(relevance_minimum=0.1, openai_api_key="sk-test")
    chunks = [_chunk(str(uuid4()), "Real Title", 0.8)]
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="Answer [Source: Unknown Doc, Sec 1]",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            latency_ms=1.0,
            model="gpt-4o",
            prompt_version="answer_generation_v1",
        )
    )
    svc = GenerationService(llm_client=llm, settings=settings, query_repo=None)
    await svc.generate_answer(
        query="Q?",
        retrieved_chunks=chunks,
        conversation_history=None,
        prompt_version="v1",
    )
    assert "Citation reference could not be matched" in caplog.text


@pytest.mark.anyio
async def test_confidence_calculation() -> None:
    settings = Settings(relevance_minimum=0.1, openai_api_key="sk-test")
    doc_id = str(uuid4())
    chunks = [_chunk(doc_id, "T", 0.95), _chunk(doc_id, "T", 0.95)]
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LlmCallResult(
            content="Answer [Source: T, Sec 1] [Source: T, Sec 1]",
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
        retrieved_chunks=chunks,
        conversation_history=None,
        prompt_version="v1",
    )
    assert result.confidence > 0.5
