"""
Information-retrieval metrics for evaluation (precision@k, recall@k).
"""

from __future__ import annotations


def precision_at_k(relevant_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """
    Compute precision@k: fraction of the top-k retrieved ids that are relevant.

    Args:
        relevant_ids: Set of document or chunk identifiers considered relevant.
        retrieved_ids: Ordered retrieval list (most relevant first).
        k: Cut-off rank (inclusive).

    Returns:
        Precision in [0, 1]. Zero when k <= 0.
    """
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for doc_id in top_k if doc_id in relevant_ids)
    return hits / float(k)


def recall_at_k(relevant_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """
    Compute recall@k: fraction of all relevant ids that appear in the top-k list.

    Args:
        relevant_ids: Ground-truth relevant identifiers (non-empty for meaningful recall).
        retrieved_ids: Ordered retrieval list.
        k: Cut-off rank (inclusive).

    Returns:
        Recall in [0, 1]. When ``relevant_ids`` is empty, returns 1.0 (vacuous truth).
    """
    if not relevant_ids:
        return 1.0
    if k <= 0:
        return 0.0
    top_k = set(retrieved_ids[:k])
    hits = len(relevant_ids & top_k)
    return hits / float(len(relevant_ids))
