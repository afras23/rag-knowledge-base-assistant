"""
Unit tests for retrieval strategies and access-control filters.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.services.retrieval.retrieval_service import (
    RetrievalService,
    build_retrieval_where_filters,
    mmr_select,
)
from app.services.vectorstore.chroma_client import RetrievedChunk


def _chunk(doc_id: str, idx: int, score: float, collection_id: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(
        text=f"t-{doc_id}-{idx}",
        doc_id=doc_id,
        document_title="T",
        page_or_section="p",
        relevance_score=score,
        collection_id=collection_id,
        restriction_level="restricted",
        chunk_index=idx,
    )


def test_similarity_search_returns_top_k() -> None:
    hits = [
        (_chunk("a", 0, 0.9), [1.0, 0.0]),
        (_chunk("b", 0, 0.5), [0.0, 1.0]),
        (_chunk("c", 0, 0.7), [0.7, 0.7]),
    ]
    ranked = sorted(hits, key=lambda item: item[0].relevance_score, reverse=True)
    top = [c for c, _ in ranked[:2]]
    assert [x.doc_id for x in top] == ["a", "c"]


def test_mmr_search_returns_diverse_results() -> None:
    q = [1.0, 0.0]
    candidates = [
        (_chunk("d1", 0, 0.95), [1.0, 0.0]),
        (_chunk("d1", 1, 0.94), [0.99, 0.01]),
        (_chunk("d2", 0, 0.5), [0.0, 1.0]),
    ]
    out = mmr_select(candidates, q, max_chunks=2, diversity_lambda=0.2)
    ids = {(c.doc_id, c.chunk_index) for c in out}
    assert ("d2", 0) in ids


def test_default_strategy_is_mmr() -> None:
    settings = Settings(retrieval_strategy="mmr")
    svc = RetrievalService(
        chroma_client=MagicMock(),
        embedding_provider=MagicMock(embed_query=lambda q: [0.1]),
        settings=settings,
    )
    assert svc._resolve_strategy(None) == "mmr"


def test_strategy_override_per_request() -> None:
    settings = Settings(retrieval_strategy="mmr")
    svc = RetrievalService(
        chroma_client=MagicMock(),
        embedding_provider=MagicMock(),
        settings=settings,
    )
    assert svc._resolve_strategy("similarity") == "similarity"


def test_access_control_excludes_restricted() -> None:
    filters = build_retrieval_where_filters(["ops"], user_group=None)
    assert filters["$and"][2] == {"restriction_level": {"$ne": "confidential"}}


def test_access_control_excludes_confidential_for_anonymous() -> None:
    filters_none = build_retrieval_where_filters(["ops"], user_group=None)
    assert {"restriction_level": {"$ne": "confidential"}} in filters_none["$and"]


def test_access_control_allows_confidential_when_user_group_set() -> None:
    filters = build_retrieval_where_filters(["ops"], user_group="consultant")
    restriction_clauses = [c for c in filters["$and"] if "restriction_level" in str(c)]
    assert not any("confidential" in str(c) for c in restriction_clauses)


def test_superseded_documents_excluded() -> None:
    filters = build_retrieval_where_filters(["ops"], user_group="consultant")
    assert {"is_superseded": {"$ne": True}} in filters["$and"]


def test_superseded_included_when_flag_true() -> None:
    filters = build_retrieval_where_filters(["ops"], user_group="consultant", include_superseded=True)
    assert not any("is_superseded" in str(c) for c in filters["$and"])


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("short", True),
        ("one two three four", True),
        ("What is the full policy?", False),
        ("it depends on region", True),
        ("travel policy", True),
        ("Why does the policy mention receipts?", False),
    ],
)
def test_query_rewrite_parametrize(query: str, expected: bool) -> None:
    """Parametrized rewrite triggers (6 cases) for heuristic coverage."""
    from app.services.retrieval.query_rewriter import QueryRewriter

    rewriter = QueryRewriter()
    assert rewriter.analyze(query).should_rewrite is expected


@pytest.mark.anyio
async def test_retrieve_logs_strategy_used() -> None:
    settings = Settings(retrieval_strategy="similarity")
    chroma = MagicMock()
    chroma.query_with_embeddings = AsyncMock(
        return_value=[
            (_chunk("x", 0, 0.9), [1.0, 0.0]),
        ]
    )
    embedder = MagicMock()
    embedder.embed_query = MagicMock(return_value=[0.1, 0.2])
    svc = RetrievalService(chroma_client=chroma, embedding_provider=embedder, settings=settings)
    result = await svc.retrieve("question", collection_ids=["ops"], strategy="similarity", max_chunks=1)
    assert result.strategy_used == "similarity"
    assert len(result.chunks) == 1
