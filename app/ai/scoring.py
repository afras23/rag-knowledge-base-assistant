"""
Deterministic confidence scoring for grounded answers (Phase 6).
"""

from __future__ import annotations

from app.config import Settings
from app.services.vectorstore.chroma_client import RetrievedChunk


def compute_confidence(
    *,
    retrieved_chunks: list[RetrievedChunk],
    citations_found: int,
    settings: Settings,
) -> float:
    """
    Compute composite confidence in [0, 1].

    Components:
        - Evidence sufficiency (0.4): share of chunks above relevance threshold.
        - Score distribution (0.3): mean relevance of retrieved chunks.
        - Citation density (0.3): citations matched vs expected count.

    Args:
        retrieved_chunks: Evidence chunks used for generation.
        citations_found: Number of citations resolved to chunks.
        settings: Thresholds and weights from configuration.

    Returns:
        Confidence score between 0 and 1.
    """
    threshold = settings.relevance_minimum
    total = len(retrieved_chunks)
    if total == 0:
        return 0.0

    above = sum(1 for chunk in retrieved_chunks if chunk.relevance_score >= threshold)
    evidence_ratio = above / float(total)
    mean_rel = sum(chunk.relevance_score for chunk in retrieved_chunks) / float(total)

    expected = max(1, min(total, 5))
    citation_ratio = min(1.0, citations_found / float(expected))

    score = 0.4 * evidence_ratio + 0.3 * mean_rel + 0.3 * citation_ratio
    return max(0.0, min(1.0, round(score, 4)))
