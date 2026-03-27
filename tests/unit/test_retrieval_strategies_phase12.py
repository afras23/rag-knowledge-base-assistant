"""
Phase 12: hybrid score merge, MMR diversity, performance, and degraded retrieval paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.services.retrieval.retrieval_service import (
    RetrievalService,
    _merge_hybrid_scores,
    mmr_select,
)
from app.services.vectorstore.chroma_client import RetrievedChunk


def _chunk(
    doc_id: str,
    chunk_index: int,
    score: float,
    *,
    collection_id: str = "c1",
) -> RetrievedChunk:
    return RetrievedChunk(
        text=f"t-{doc_id}-{chunk_index}",
        doc_id=doc_id,
        document_title=f"Title-{doc_id}",
        page_or_section="p1",
        relevance_score=score,
        collection_id=collection_id,
        restriction_level="restricted",
        chunk_index=chunk_index,
    )


def test_merge_hybrid_scores_combines_dense_and_keyword_weights() -> None:
    """Dense + keyword maps merge with configured hybrid weights."""
    dense = {"a:0": (_chunk("a", 0, 0.9), 0.9)}
    keyword = {"b:0": (_chunk("b", 0, 0.5), 1.0)}
    ranked = _merge_hybrid_scores(dense, keyword)
    assert len(ranked) == 2
    # b: 0.6*0 + 0.4*1 = 0.4 ; a: 0.6*0.9 + 0.4*0 = 0.54
    assert ranked[0].doc_id == "a"
    assert ranked[1].doc_id == "b"


def test_mmr_diversity_prefers_distinct_documents_when_lambda_low() -> None:
    """MMR with low diversity_lambda selects chunks from different doc_ids."""
    query_embedding = [1.0, 0.0]
    candidates = [
        (_chunk("d1", 0, 0.95), [1.0, 0.0]),
        (_chunk("d1", 1, 0.94), [0.99, 0.01]),
        (_chunk("d2", 0, 0.5), [0.0, 1.0]),
    ]
    selected = mmr_select(
        candidates,
        query_embedding,
        max_chunks=2,
        diversity_lambda=0.2,
    )
    doc_ids = {chunk.doc_id for chunk in selected}
    assert doc_ids == {"d1", "d2"}


@pytest.mark.anyio
async def test_hybrid_strategy_resolves_to_mmr_when_feature_disabled() -> None:
    """When hybrid is disabled in settings, strategy falls back to MMR."""
    settings = Settings(retrieval_strategy="hybrid", enable_hybrid_retrieval=False)
    chroma = MagicMock()
    chroma.query_with_embeddings = AsyncMock(
        return_value=[
            (_chunk("x", 0, 0.9), [1.0, 0.0]),
        ],
    )
    embedder = MagicMock()
    embedder.embed_query = MagicMock(return_value=[0.1, 0.2])
    svc = RetrievalService(chroma_client=chroma, embedding_provider=embedder, settings=settings)
    result = await svc.retrieve("keywordterm here", collection_ids=["ops"], strategy="hybrid", max_chunks=1)
    assert result.strategy_used == "mmr"


@pytest.mark.anyio
async def test_hybrid_retrieval_runs_when_enabled() -> None:
    """Hybrid path queries Chroma for dense and optional keyword windows."""
    settings = Settings(retrieval_strategy="hybrid", enable_hybrid_retrieval=True)
    chroma = MagicMock()
    shared = (_chunk("a", 0, 0.9), [1.0, 0.0])
    chroma.query_with_embeddings = AsyncMock(
        side_effect=[
            [shared],
            [shared],
        ],
    )
    embedder = MagicMock()
    embedder.embed_query = MagicMock(return_value=[1.0, 0.0])
    svc = RetrievalService(chroma_client=chroma, embedding_provider=embedder, settings=settings)
    result = await svc.retrieve("keywordterm extra", collection_ids=["ops"], strategy="hybrid", max_chunks=2)
    assert result.strategy_used == "hybrid"
    assert chroma.query_with_embeddings.await_count >= 2


@pytest.mark.anyio
async def test_retrieval_latency_under_five_seconds_with_fast_chroma() -> None:
    """Retrieval completes within 5s when the vector store responds immediately."""
    settings = Settings(retrieval_strategy="similarity")
    chroma = MagicMock()
    chroma.query_with_embeddings = AsyncMock(
        return_value=[
            (_chunk("z", 0, 0.8), [1.0, 0.0]),
        ],
    )
    embedder = MagicMock()
    embedder.embed_query = MagicMock(return_value=[0.5, 0.5])
    svc = RetrievalService(chroma_client=chroma, embedding_provider=embedder, settings=settings)
    result = await svc.retrieve("question", collection_ids=["ops"], max_chunks=1)
    assert result.retrieval_latency_ms < 5000.0


@pytest.mark.anyio
async def test_vector_retrieval_does_not_use_postgres() -> None:
    """
    Degraded-mode contract: retrieval uses Chroma + embeddings only.

    PostgreSQL is not part of ``RetrievalService.retrieve``; answers that need
    conversation persistence still require Postgres in ``QueryService``.
    """
    settings = Settings(retrieval_strategy="mmr")
    chroma = MagicMock()
    chroma.query_with_embeddings = AsyncMock(
        return_value=[
            (_chunk("p", 0, 0.88), [0.2, 0.8]),
        ],
    )
    embedder = MagicMock()
    embedder.embed_query = MagicMock(return_value=[0.2, 0.8])
    svc = RetrievalService(chroma_client=chroma, embedding_provider=embedder, settings=settings)
    result = await svc.retrieve("hello world", collection_ids=["ops"], max_chunks=1)
    assert result.chunks[0].doc_id == "p"
    chroma.query_with_embeddings.assert_awaited()
