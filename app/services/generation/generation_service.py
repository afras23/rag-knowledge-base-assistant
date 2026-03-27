"""
Grounded answer generation with citations and confidence (Phase 6).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.llm_client import LlmClient
from app.ai.prompts.loader import get_prompt
from app.ai.scoring import compute_confidence
from app.api.schemas.chat import CitationSchema
from app.config import Settings
from app.repositories.query_repo import QueryRepository
from app.services.generation.citation_formatter import build_citations_from_answer
from app.services.vectorstore.chroma_client import RetrievedChunk

logger = logging.getLogger(__name__)


class GenerationResult(BaseModel):
    """Outcome of grounded generation."""

    answer: str = Field(..., description="Model answer or refusal message")
    citations: list[CitationSchema] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    refused: bool = Field(..., description="Whether generation was skipped or blocked")
    refusal_reason: str | None = Field(default=None)
    tokens_used: int = Field(..., ge=0)
    cost_usd: float = Field(..., ge=0.0)
    latency_ms: float = Field(..., ge=0.0)
    model: str = Field(..., description="LLM model identifier")
    prompt_version: str = Field(..., description="Prompt template version used")


def _hash_question(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _format_chunks_for_prompt(chunks: list[RetrievedChunk]) -> str:
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        header = f"[{index}] {chunk.document_title} | {chunk.page_or_section}"
        blocks.append(f"{header}\n{chunk.text}")
    return "\n\n".join(blocks)


def _merge_question_with_history(query: str, history: list[dict[str, str]] | None) -> str:
    if not history:
        return query
    lines: list[str] = []
    for turn in history:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", ""))
        lines.append(f"{role}: {content}")
    prior = "\n".join(lines)
    return f"Prior conversation:\n{prior}\n\nCurrent question:\n{query}"


def _should_refuse_evidence(chunks: list[RetrievedChunk], threshold: float) -> bool:
    if not chunks:
        return True
    return max(chunk.relevance_score for chunk in chunks) < threshold


class GenerationService:
    """Orchestrate grounded generation, citations, and query analytics logging."""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        settings: Settings,
        query_repo: QueryRepository | None = None,
    ) -> None:
        """
        Initialize generation service.

        Args:
            llm_client: OpenAI client wrapper.
            settings: Application settings.
            query_repo: Optional repository for query + LLM audit rows.
        """
        self._llm = llm_client
        self._settings = settings
        self._query_repo = query_repo

    async def generate_answer(
        self,
        *,
        query: str,
        retrieved_chunks: list[RetrievedChunk],
        conversation_history: list[dict[str, str]] | None,
        prompt_version: str | None,
        correlation_id: str | None = None,
        question_hash: str | None = None,
        retrieval_strategy: str = "unknown",
    ) -> GenerationResult:
        """
        Generate an answer grounded in retrieved chunks.

        Args:
            query: User question.
            retrieved_chunks: Evidence chunks from retrieval.
            conversation_history: Optional prior turns.
            prompt_version: Optional prompt version key (defaults to ``v1``).
            correlation_id: Request correlation id for logs.
            question_hash: Optional stable hash; computed when omitted.
            retrieval_strategy: Strategy label for analytics.

        Returns:
            Generation result including citations and confidence.
        """
        start = time.perf_counter()
        q_hash = question_hash or _hash_question(query)
        threshold = self._settings.retrieval_relevance_threshold

        if _should_refuse_evidence(retrieved_chunks, threshold):
            latency_ms = (time.perf_counter() - start) * 1000.0
            result = self._refusal_result(reason="insufficient_evidence", latency_ms=latency_ms)
            await self._log_query_only(
                query_hash=q_hash,
                retrieval_strategy=retrieval_strategy,
                chunks=retrieved_chunks,
                result=result,
                correlation_id=correlation_id,
            )
            return result

        merged_question = _merge_question_with_history(query, conversation_history)
        context_text = _format_chunks_for_prompt(retrieved_chunks)
        version_key = prompt_version or "v1"
        system_prompt, user_prompt, pv = get_prompt(
            "answer_generation",
            version_key,
            chunks=context_text,
            question=merged_question,
        )

        llm = await self._llm.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_version=pv,
            correlation_id=correlation_id,
        )
        citations, unmatched = build_citations_from_answer(llm.content, retrieved_chunks)
        if unmatched:
            logger.warning(
                "Some citation references did not match chunks",
                extra={"unmatched_count": len(unmatched)},
            )

        confidence = compute_confidence(
            retrieved_chunks=retrieved_chunks,
            citations_found=len(citations),
            settings=self._settings,
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        gen_result = GenerationResult(
            answer=llm.content.strip(),
            citations=citations,
            confidence=confidence,
            refused=False,
            refusal_reason=None,
            tokens_used=llm.input_tokens + llm.output_tokens,
            cost_usd=llm.cost_usd,
            latency_ms=latency_ms,
            model=llm.model,
            prompt_version=llm.prompt_version,
        )

        event_id = await self._log_query_only(
            query_hash=q_hash,
            retrieval_strategy=retrieval_strategy,
            chunks=retrieved_chunks,
            result=gen_result,
            correlation_id=correlation_id,
        )
        await self._log_generation_audit(
            query_event_id=event_id,
            gen_result=gen_result,
            input_tokens=llm.input_tokens,
            output_tokens=llm.output_tokens,
            llm_latency_ms=llm.latency_ms,
        )
        return gen_result

    def _refusal_result(self, *, reason: str, latency_ms: float) -> GenerationResult:
        return GenerationResult(
            answer="I don't have enough information.",
            citations=[],
            confidence=0.0,
            refused=True,
            refusal_reason=reason,
            tokens_used=0,
            cost_usd=0.0,
            latency_ms=latency_ms,
            model=self._settings.llm_model,
            prompt_version="none",
        )

    async def _log_query_only(
        self,
        *,
        query_hash: str,
        retrieval_strategy: str,
        chunks: list[RetrievedChunk],
        result: GenerationResult,
        correlation_id: str | None,
    ) -> UUID | None:
        if self._query_repo is None:
            return None
        collection_ids = sorted({chunk.collection_id for chunk in chunks})
        top_score = max((c.relevance_score for c in chunks), default=0.0)
        extra: dict[str, Any] = {"query_hash": query_hash}
        if correlation_id is not None:
            extra["correlation_id"] = correlation_id
        logger.info("Persisting query event", extra=extra)
        event = await self._query_repo.log_query_event(
            question_hash=query_hash,
            user_group=None,
            collection_ids_searched=collection_ids,
            retrieval_strategy=retrieval_strategy,
            chunks_retrieved=len(chunks),
            top_relevance_score=top_score,
            confidence=result.confidence,
            refused=result.refused,
            refusal_reason=result.refusal_reason,
            tokens_used=result.tokens_used,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            prompt_version=result.prompt_version,
            model=result.model,
        )
        return event.id

    async def _log_generation_audit(
        self,
        *,
        query_event_id: UUID | None,
        gen_result: GenerationResult,
        input_tokens: int,
        output_tokens: int,
        llm_latency_ms: float,
    ) -> None:
        if self._query_repo is None or query_event_id is None:
            return
        await self._query_repo.log_llm_call(
            query_event_id=query_event_id,
            call_type="generation",
            model=gen_result.model,
            prompt_version=gen_result.prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=gen_result.cost_usd,
            latency_ms=llm_latency_ms,
        )
