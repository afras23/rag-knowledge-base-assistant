"""
Multi-strategy retrieval over ChromaDB (Phase 5, ADR 002).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Literal, cast

from pydantic import BaseModel, Field

from app.ai.llm_client import LlmClient
from app.config import Settings
from app.services.ingestion.embedder import EmbeddingProvider
from app.services.retrieval.query_rewriter import QueryRewriter
from app.services.vectorstore.chroma_client import ChromaClientWrapper, RetrievedChunk

logger = logging.getLogger(__name__)

MMR_CANDIDATE_MULTIPLIER = 4
HYBRID_DENSE_WEIGHT = 0.6
HYBRID_KEYWORD_WEIGHT = 0.4
HYBRID_KEYWORD_MIN_TERM_LEN = 3


class RetrievalResult(BaseModel):
    """Outcome of a retrieval request."""

    chunks: list[RetrievedChunk] = Field(..., description="Retrieved chunks in final ranking order")
    strategy_used: str = Field(..., description="Strategy applied after defaults and overrides")
    query_rewritten: bool = Field(..., description="True when LLM rewrite changed the query (Phase 6)")
    rewritten_query: str | None = Field(default=None, description="Rewritten query text when applied")
    retrieval_latency_ms: float = Field(..., ge=0.0, description="End-to-end retrieval latency")


def build_retrieval_where_filters(
    collection_ids: list[str],
    *,
    user_group: str | None,
    include_superseded: bool = False,
) -> dict[str, object]:
    """
    Build Chroma metadata ``where`` filters for access control and superseded docs.

    Args:
        collection_ids: Logical collection identifiers to search.
        user_group: When None, confidential documents are excluded.
        include_superseded: When True, chunks with ``is_superseded`` metadata are not filtered out.

    Returns:
        Chroma-compatible metadata filter dictionary.
    """
    clauses: list[dict[str, object]] = [{"collection_id": {"$in": collection_ids}}]
    if not include_superseded:
        clauses.append({"is_superseded": {"$ne": True}})
    if user_group is None:
        clauses.append({"restriction_level": {"$ne": "confidential"}})
    return {"$and": clauses}


def _chunk_key(chunk: RetrievedChunk) -> str:
    return f"{chunk.doc_id}:{chunk.chunk_index}"


def _dedupe_best_relevance(
    hits: list[tuple[RetrievedChunk, list[float]]],
) -> list[tuple[RetrievedChunk, list[float]]]:
    """Keep the highest-relevance row per chunk key when merging collections."""
    best: dict[str, tuple[RetrievedChunk, list[float]]] = {}
    for chunk, emb in hits:
        key = _chunk_key(chunk)
        prev = best.get(key)
        if prev is None or chunk.relevance_score > prev[0].relevance_score:
            best[key] = (chunk, emb)
    return list(best.values())


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vector_a, vector_b, strict=False))
    na = math.sqrt(sum(a * a for a in vector_a))
    nb = math.sqrt(sum(b * b for b in vector_b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _max_pairwise_similarity(candidate: list[float], selected: list[list[float]]) -> float:
    if not selected:
        return 0.0
    return max(_cosine_similarity(candidate, vector) for vector in selected)


def mmr_select(
    candidates: list[tuple[RetrievedChunk, list[float]]],
    query_embedding: list[float],
    *,
    max_chunks: int,
    diversity_lambda: float,
) -> list[RetrievedChunk]:
    """Apply MMR on dense candidates using stored embeddings."""
    if not candidates or max_chunks <= 0:
        return []
    pool = list(candidates)
    first_idx = max(
        range(len(pool)),
        key=lambda i: _cosine_similarity(query_embedding, pool[i][1]),
    )
    selected_chunks: list[RetrievedChunk] = [pool[first_idx][0]]
    selected_embs: list[list[float]] = [pool[first_idx][1]]
    del pool[first_idx]
    while len(selected_chunks) < max_chunks and pool:
        best_mmr = -math.inf
        best_i = -1
        for idx, (_chunk, emb) in enumerate(pool):
            sim_q = _cosine_similarity(query_embedding, emb)
            div_penalty = _max_pairwise_similarity(emb, selected_embs)
            mmr_score = diversity_lambda * sim_q - (1.0 - diversity_lambda) * div_penalty
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_i = idx
        chosen = pool.pop(best_i)
        selected_chunks.append(chosen[0])
        selected_embs.append(chosen[1])
    return selected_chunks


def _merge_hybrid_scores(
    dense: dict[str, tuple[RetrievedChunk, float]],
    keyword: dict[str, tuple[RetrievedChunk, float]],
) -> list[RetrievedChunk]:
    keys = set(dense) | set(keyword)
    ranked: list[tuple[float, RetrievedChunk]] = []
    for key in keys:
        d_score = dense.get(key, (None, 0.0))[1]
        k_score = keyword.get(key, (None, 0.0))[1]
        chunk = dense.get(key, keyword.get(key, (None, 0.0)))[0]
        if chunk is None:
            continue
        combined = HYBRID_DENSE_WEIGHT * d_score + HYBRID_KEYWORD_WEIGHT * k_score
        ranked.append((combined, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in ranked]


def _validate_retrieve_inputs(collection_ids: list[str], max_chunks: int) -> None:
    if max_chunks < 1:
        raise ValueError("max_chunks must be >= 1")
    if not collection_ids:
        raise ValueError("collection_ids must be non-empty")


class RetrievalService:
    """Retrieve relevant chunks using similarity, MMR, or hybrid strategies."""

    def __init__(
        self,
        *,
        chroma_client: ChromaClientWrapper,
        embedding_provider: EmbeddingProvider,
        settings: Settings,
        llm_client: LlmClient | None = None,
    ) -> None:
        """
        Initialize retrieval service.

        Args:
            chroma_client: Chroma wrapper for dense (and optional keyword) search.
            embedding_provider: Embeds the user query.
            settings: Application settings (strategy defaults and feature flags).
            llm_client: Optional LLM client for query rewrite when heuristics trigger.
        """
        self._chroma = chroma_client
        self._embedder = embedding_provider
        self._settings = settings
        self._rewriter = QueryRewriter(llm_client=llm_client, settings=settings)

    async def retrieve(
        self,
        query: str,
        collection_ids: list[str],
        user_group: str | None = None,
        strategy: str | None = None,
        max_chunks: int = 5,
        correlation_id: str | None = None,
        include_superseded: bool = False,
    ) -> RetrievalResult:
        """
        Retrieve top chunks for a question across collections.

        Args:
            query: User question.
            collection_ids: Collections to search.
            user_group: When None, confidential chunks are excluded.
            strategy: Optional override for ``settings.retrieval_strategy``.
            max_chunks: Maximum chunks to return.
            correlation_id: Optional id propagated to query rewrite LLM calls.
            include_superseded: Include chunks marked superseded in metadata (default False).

        Returns:
            Retrieval result with chunks and telemetry.

        Raises:
            ValueError: When inputs are invalid.
        """
        _validate_retrieve_inputs(collection_ids, max_chunks)

        start = time.perf_counter()
        rewrite_out = await self._rewriter.rewrite(query, correlation_id=correlation_id)
        effective_query = rewrite_out.effective_query
        resolved = self._resolve_strategy(strategy)
        where_filters = build_retrieval_where_filters(
            collection_ids,
            user_group=user_group,
            include_superseded=include_superseded,
        )
        query_embedding = self._embedder.embed_query(effective_query)

        chunks = await self._run_strategy(
            resolved,
            query_embedding=query_embedding,
            query_text=effective_query,
            where_filters=where_filters,
            collection_ids=collection_ids,
            max_chunks=max_chunks,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "Retrieval completed",
            extra={
                "strategy": resolved,
                "collections": collection_ids,
                "user_group_set": user_group is not None,
                "result_count": len(chunks),
                "latency_ms": latency_ms,
            },
        )
        return RetrievalResult(
            chunks=chunks,
            strategy_used=resolved,
            query_rewritten=rewrite_out.was_rewritten,
            rewritten_query=rewrite_out.rewritten_query,
            retrieval_latency_ms=latency_ms,
        )

    def _resolve_strategy(self, strategy: str | None) -> Literal["similarity", "mmr", "hybrid"]:
        chosen = strategy or self._settings.retrieval_strategy
        if chosen == "hybrid" and not self._settings.enable_hybrid_retrieval:
            logger.warning(
                "Hybrid retrieval requested but disabled; falling back to mmr",
                extra={"enable_hybrid_retrieval": self._settings.enable_hybrid_retrieval},
            )
            return "mmr"
        if chosen not in ("similarity", "mmr", "hybrid"):
            raise ValueError("strategy must be similarity, mmr, or hybrid")
        return cast(Literal["similarity", "mmr", "hybrid"], chosen)

    async def _run_strategy(
        self,
        strategy: Literal["similarity", "mmr", "hybrid"],
        *,
        query_embedding: list[float],
        query_text: str,
        where_filters: dict[str, object],
        collection_ids: list[str],
        max_chunks: int,
    ) -> list[RetrievedChunk]:
        if strategy == "similarity":
            return await self._retrieve_similarity(
                query_embedding=query_embedding,
                where_filters=where_filters,
                collection_ids=collection_ids,
                max_chunks=max_chunks,
            )
        if strategy == "mmr":
            return await self._retrieve_mmr(
                query_embedding=query_embedding,
                where_filters=where_filters,
                collection_ids=collection_ids,
                max_chunks=max_chunks,
            )
        return await self._retrieve_hybrid(
            query_embedding=query_embedding,
            query_text=query_text,
            where_filters=where_filters,
            collection_ids=collection_ids,
            max_chunks=max_chunks,
        )

    async def _gather_dense_hits(
        self,
        *,
        query_embedding: list[float],
        where_filters: dict[str, object],
        collection_ids: list[str],
        n_results: int,
        where_document: dict[str, object] | None = None,
    ) -> list[tuple[RetrievedChunk, list[float]]]:
        merged: list[tuple[RetrievedChunk, list[float]]] = []
        for collection_name in collection_ids:
            hits = await self._chroma.query_with_embeddings(
                collection_name=collection_name,
                query_embedding=query_embedding,
                n_results=n_results,
                where_filters=where_filters,
                where_document=where_document,
            )
            merged.extend(hits)
        return merged

    async def _retrieve_similarity(
        self,
        *,
        query_embedding: list[float],
        where_filters: dict[str, object],
        collection_ids: list[str],
        max_chunks: int,
    ) -> list[RetrievedChunk]:
        hits = await self._gather_dense_hits(
            query_embedding=query_embedding,
            where_filters=where_filters,
            collection_ids=collection_ids,
            n_results=max_chunks,
        )
        deduped = _dedupe_best_relevance(hits)
        ranked = sorted(deduped, key=lambda item: item[0].relevance_score, reverse=True)
        return [chunk for chunk, _emb in ranked[:max_chunks]]

    async def _retrieve_mmr(
        self,
        *,
        query_embedding: list[float],
        where_filters: dict[str, object],
        collection_ids: list[str],
        max_chunks: int,
    ) -> list[RetrievedChunk]:
        pool_size = max(max_chunks * MMR_CANDIDATE_MULTIPLIER, max_chunks)
        hits = await self._gather_dense_hits(
            query_embedding=query_embedding,
            where_filters=where_filters,
            collection_ids=collection_ids,
            n_results=pool_size,
        )
        candidates = _dedupe_best_relevance(hits)
        return mmr_select(
            candidates,
            query_embedding,
            max_chunks=max_chunks,
            diversity_lambda=self._settings.mmr_diversity_lambda,
        )

    async def _retrieve_hybrid(
        self,
        *,
        query_embedding: list[float],
        query_text: str,
        where_filters: dict[str, object],
        collection_ids: list[str],
        max_chunks: int,
    ) -> list[RetrievedChunk]:
        pool_size = max(max_chunks * MMR_CANDIDATE_MULTIPLIER, max_chunks)
        dense_hits = await self._gather_dense_hits(
            query_embedding=query_embedding,
            where_filters=where_filters,
            collection_ids=collection_ids,
            n_results=pool_size,
        )
        keyword_term = self._pick_keyword_term(query_text)
        kw_hits: list[tuple[RetrievedChunk, list[float]]] = []
        if keyword_term:
            where_doc: dict[str, object] = {"$contains": keyword_term}
            kw_hits = await self._gather_dense_hits(
                query_embedding=query_embedding,
                where_filters=where_filters,
                collection_ids=collection_ids,
                n_results=pool_size,
                where_document=where_doc,
            )

        dense_map: dict[str, tuple[RetrievedChunk, float]] = {}
        for chunk, _e in dense_hits:
            dense_map[_chunk_key(chunk)] = (chunk, chunk.relevance_score)

        kw_map: dict[str, tuple[RetrievedChunk, float]] = {}
        for chunk, _e in kw_hits:
            kw_map[_chunk_key(chunk)] = (chunk, chunk.relevance_score)

        merged = _merge_hybrid_scores(dense_map, kw_map)
        if merged:
            return merged[:max_chunks]
        return await self._retrieve_mmr(
            query_embedding=query_embedding,
            where_filters=where_filters,
            collection_ids=collection_ids,
            max_chunks=max_chunks,
        )

    @staticmethod
    def _pick_keyword_term(query_text: str) -> str | None:
        for raw in query_text.split():
            cleaned = raw.strip(".,?!\"'()[]{}").lower()
            if len(cleaned) >= HYBRID_KEYWORD_MIN_TERM_LEN:
                return cleaned
        return None
