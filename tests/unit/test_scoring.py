"""
Unit tests for deterministic confidence scoring.
"""

from __future__ import annotations

import pytest

from app.ai.scoring import compute_confidence
from app.config import Settings
from app.services.vectorstore.chroma_client import RetrievedChunk


def _chunk(score: float) -> RetrievedChunk:
    return RetrievedChunk(
        text="t",
        doc_id="00000000-0000-0000-0000-000000000001",
        document_title="T",
        page_or_section="p",
        relevance_score=score,
        collection_id="c",
        restriction_level="restricted",
        chunk_index=0,
    )


def test_high_evidence_high_confidence() -> None:
    settings = Settings(relevance_minimum=0.2)
    chunks = [_chunk(0.95), _chunk(0.95)]
    conf = compute_confidence(retrieved_chunks=chunks, citations_found=2, settings=settings)
    assert conf >= 0.7


def test_low_evidence_low_confidence() -> None:
    settings = Settings(relevance_minimum=0.2)
    chunks = [_chunk(0.1), _chunk(0.1)]
    conf = compute_confidence(retrieved_chunks=chunks, citations_found=0, settings=settings)
    assert conf < 0.5


def test_no_citations_reduces_confidence() -> None:
    settings = Settings(relevance_minimum=0.2)
    chunks = [_chunk(0.9)]
    with_cite = compute_confidence(retrieved_chunks=chunks, citations_found=1, settings=settings)
    without = compute_confidence(retrieved_chunks=chunks, citations_found=0, settings=settings)
    assert with_cite > without


@pytest.mark.parametrize(
    ("scores", "citations", "expected_min", "expected_max"),
    [
        ([0.9, 0.9], 2, 0.7, 1.0),
        ([0.0, 0.0], 0, 0.0, 0.35),
        ([0.5, 0.5], 1, 0.3, 0.7),
        ([0.3, 0.8], 0, 0.0, 0.6),
    ],
)
def test_confidence_parametrize(
    scores: list[float],
    citations: int,
    expected_min: float,
    expected_max: float,
) -> None:
    settings = Settings(relevance_minimum=0.25)
    chunks = [_chunk(s) for s in scores]
    conf = compute_confidence(retrieved_chunks=chunks, citations_found=citations, settings=settings)
    assert expected_min <= conf <= expected_max
