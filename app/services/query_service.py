"""
Chat query orchestration (Phase 8): guardrails, retrieval, generation, persistence.
"""

from __future__ import annotations

import hashlib
import logging
import time
from uuid import UUID

from app.ai.guardrails import GuardrailService
from app.ai.pii_detector import PiiDetector, PiiScanResult
from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse, CitationSchema
from app.config import Settings
from app.core.exceptions import ConversationNotFoundError
from app.models.conversation import ConversationRole
from app.repositories.collection_repo import CollectionRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.query_repo import QueryRepository
from app.services.generation.generation_service import GenerationResult, GenerationService
from app.services.retrieval.retrieval_service import RetrievalService
from app.services.vectorstore.chroma_client import RetrievedChunk

logger = logging.getLogger(__name__)

GUARD_REFUSAL_TEXT = "This request cannot be processed due to a safety policy violation."
PII_BLOCK_REFUSAL_TEXT = "This request cannot be processed because it appears to contain sensitive information."


def _question_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence_refusal_copy(collection_ids: list[str]) -> str:
    cols = ", ".join(sorted(collection_ids)) if collection_ids else "(none)"
    return (
        "I don't have enough information from the retrieved documents.\n"
        f"Searched collections: {cols}.\n"
        "Try rephrasing with more specific terms or a narrower topic."
    )


class QueryService:
    """Orchestrate chat queries with guardrails, retrieval, generation, and persistence."""

    def __init__(
        self,
        *,
        settings: Settings,
        guardrail_service: GuardrailService,
        pii_detector: PiiDetector,
        retrieval_service: RetrievalService,
        generation_service: GenerationService,
        conversation_repo: ConversationRepository,
        collection_repo: CollectionRepository,
        query_repo: QueryRepository,
    ) -> None:
        """
        Initialize query orchestrator.

        Args:
            settings: Application settings.
            guardrail_service: Prompt injection guardrails.
            pii_detector: PII policy enforcement.
            retrieval_service: Vector retrieval.
            generation_service: Grounded generation (expects query analytics repo).
            conversation_repo: Conversation/message persistence.
            collection_repo: Collection catalog for default retrieval scope.
            query_repo: Query analytics when orchestrator logs early refusals.
        """
        self._settings = settings
        self._guardrails = guardrail_service
        self._pii = pii_detector
        self._retrieval = retrieval_service
        self._generation = generation_service
        self._conversations = conversation_repo
        self._collections = collection_repo
        self._queries = query_repo

    async def query(
        self,
        request: ChatQueryRequest,
        *,
        correlation_id: str | None,
    ) -> ChatQueryResponse:
        """
        Run the full chat pipeline and persist the exchange.

        Args:
            request: Incoming chat query.
            correlation_id: Optional request correlation id.

        Returns:
            Chat response envelope payload (caller wraps in API success envelope).
        """
        wall_start = time.perf_counter()
        raw_question = request.question.strip()

        guard = await self._guardrails.check_input(raw_question)
        if not guard.is_safe:
            return await self._finish_refusal_path(
                request=request,
                correlation_id=correlation_id,
                wall_start=wall_start,
                assistant_text=GUARD_REFUSAL_TEXT,
                citations=[],
                refused=True,
                refusal_reason="guardrail_violation",
                tokens_used=0,
                cost_usd=0.0,
                confidence=0.0,
                low_confidence=False,
                chunks=[],
                retrieval_strategy="not_executed",
                collection_ids=[],
                log_refusal_reason="guardrail_violation",
                user_stored_text=raw_question,
            )

        pii_scan = self._pii.scan_text(raw_question)
        if self._settings.pii_policy == "block" and pii_scan.has_pii:
            scope_ids = await _resolve_collection_ids(request, self._collections)
            return await self._finish_refusal_path(
                request=request,
                correlation_id=correlation_id,
                wall_start=wall_start,
                assistant_text=PII_BLOCK_REFUSAL_TEXT,
                citations=[],
                refused=True,
                refusal_reason="pii_blocked",
                tokens_used=0,
                cost_usd=0.0,
                confidence=0.0,
                low_confidence=False,
                chunks=[],
                retrieval_strategy="not_executed",
                collection_ids=scope_ids,
                log_refusal_reason="pii_blocked",
                user_stored_text=pii_scan.redacted_text.strip(),
            )

        effective_question = self._effective_query_text(raw_question, pii_scan)

        if request.conversation_id is None:
            created = await self._conversations.create_conversation(user_group=request.user_group)
            conversation_id = created.id
        else:
            existing = await self._conversations.get_conversation(conversation_id=request.conversation_id)
            if existing is None:
                raise ConversationNotFoundError(
                    context={"conversation_id": str(request.conversation_id)},
                )
            conversation_id = existing.id

        history = await self._conversations.get_recent_message_history(
            conversation_id=conversation_id,
            max_messages=self._settings.conversation_context_max_messages,
        )

        collection_ids = await _resolve_collection_ids(request, self._collections)
        if not collection_ids:
            return await self._finish_refusal_path(
                request=request,
                correlation_id=correlation_id,
                wall_start=wall_start,
                assistant_text="No document collections are configured for search.",
                citations=[],
                refused=True,
                refusal_reason="no_collections",
                tokens_used=0,
                cost_usd=0.0,
                confidence=0.0,
                low_confidence=False,
                chunks=[],
                retrieval_strategy="not_executed",
                collection_ids=[],
                log_refusal_reason="no_collections",
                conversation_id_override=conversation_id,
                user_text_override=effective_question,
                user_stored_text=self._user_message_for_storage(raw_question, pii_scan),
            )

        retrieval_result = await self._retrieval.retrieve(
            effective_question,
            collection_ids=collection_ids,
            user_group=request.user_group,
            strategy=self._settings.retrieval_strategy,
            max_chunks=request.max_chunks,
            correlation_id=correlation_id,
        )
        chunks = retrieval_result.chunks
        best = max((c.relevance_score for c in chunks), default=0.0)
        if not chunks or best < self._settings.relevance_minimum:
            answer_text = _evidence_refusal_copy(collection_ids)
            return await self._finish_refusal_path(
                request=request,
                correlation_id=correlation_id,
                wall_start=wall_start,
                assistant_text=answer_text,
                citations=[],
                refused=True,
                refusal_reason="insufficient_evidence",
                tokens_used=0,
                cost_usd=0.0,
                confidence=0.0,
                low_confidence=False,
                chunks=chunks,
                retrieval_strategy=retrieval_result.strategy_used,
                collection_ids=collection_ids,
                log_refusal_reason="insufficient_evidence",
                conversation_id_override=conversation_id,
                user_text_override=effective_question,
                user_stored_text=self._user_message_for_storage(raw_question, pii_scan),
            )

        q_hash = _question_hash(effective_question)
        gen_result = await self._generation.generate_answer(
            query=effective_question,
            retrieved_chunks=chunks,
            conversation_history=history,
            prompt_version="v1",
            correlation_id=correlation_id,
            question_hash=q_hash,
            retrieval_strategy=retrieval_result.strategy_used,
            collection_ids_searched=collection_ids,
            input_safety_mode="skip",
            relevance_gate_mode="skip",
        )

        total_ms = (time.perf_counter() - wall_start) * 1000.0
        user_stored = self._user_message_for_storage(raw_question, pii_scan)
        await self._persist_exchange(
            conversation_id=conversation_id,
            user_text=user_stored,
            gen_result=gen_result,
        )
        return ChatQueryResponse(
            answer=gen_result.answer,
            citations=gen_result.citations,
            confidence=gen_result.confidence,
            conversation_id=conversation_id,
            refused=gen_result.refused,
            refusal_reason=gen_result.refusal_reason,
            tokens_used=gen_result.tokens_used,
            cost_usd=gen_result.cost_usd,
            latency_ms=total_ms,
            low_confidence=gen_result.low_confidence,
        )

    def _effective_query_text(self, raw_question: str, pii_scan: PiiScanResult) -> str:
        if self._settings.pii_policy == "redact" and pii_scan.has_pii:
            return pii_scan.redacted_text.strip()
        return raw_question.strip()

    def _user_message_for_storage(self, raw_question: str, pii_scan: PiiScanResult) -> str:
        if self._settings.pii_policy == "redact" and pii_scan.has_pii:
            return pii_scan.redacted_text.strip()
        return raw_question.strip()

    async def _persist_exchange(
        self,
        *,
        conversation_id: UUID,
        user_text: str,
        gen_result: GenerationResult,
    ) -> None:
        citations_payload: list[dict[str, object]] | None = None
        if gen_result.citations:
            citations_payload = [c.model_dump() for c in gen_result.citations]
        await self._conversations.add_message(
            conversation_id=conversation_id,
            role=ConversationRole.user,
            content=user_text,
            citations_json=None,
            refused=False,
        )
        await self._conversations.add_message(
            conversation_id=conversation_id,
            role=ConversationRole.assistant,
            content=gen_result.answer,
            citations_json=citations_payload,
            refused=gen_result.refused,
        )

    async def _finish_refusal_path(
        self,
        *,
        request: ChatQueryRequest,
        correlation_id: str | None,
        wall_start: float,
        assistant_text: str,
        citations: list[CitationSchema],
        refused: bool,
        refusal_reason: str | None,
        tokens_used: int,
        cost_usd: float,
        confidence: float,
        low_confidence: bool,
        chunks: list[RetrievedChunk],
        retrieval_strategy: str,
        collection_ids: list[str],
        log_refusal_reason: str,
        conversation_id_override: UUID | None = None,
        user_text_override: str | None = None,
        user_stored_text: str | None = None,
    ) -> ChatQueryResponse:
        if conversation_id_override is not None:
            conversation_id = conversation_id_override
            effective_for_user = user_text_override or request.question.strip()
            stored_user = user_stored_text if user_stored_text is not None else effective_for_user
        elif request.conversation_id is None:
            conv = await self._conversations.create_conversation(user_group=request.user_group)
            conversation_id = conv.id
            pii_scan = self._pii.scan_text(request.question.strip())
            stored_user = (
                user_stored_text
                if user_stored_text is not None
                else self._user_message_for_storage(request.question.strip(), pii_scan)
            )
            effective_for_user = user_text_override or request.question.strip()
        else:
            existing_conv = await self._conversations.get_conversation(conversation_id=request.conversation_id)
            if existing_conv is None:
                raise ConversationNotFoundError(
                    context={"conversation_id": str(request.conversation_id)},
                )
            conversation_id = existing_conv.id
            pii_scan = self._pii.scan_text(request.question.strip())
            stored_user = (
                user_stored_text
                if user_stored_text is not None
                else self._user_message_for_storage(request.question.strip(), pii_scan)
            )
            effective_for_user = user_text_override or request.question.strip()

        top_score = max((c.relevance_score for c in chunks), default=0.0)
        await self._queries.log_query_event(
            question_hash=_question_hash(effective_for_user),
            user_group=request.user_group,
            collection_ids_searched=sorted(collection_ids),
            retrieval_strategy=retrieval_strategy,
            chunks_retrieved=len(chunks),
            top_relevance_score=top_score,
            confidence=confidence,
            refused=True,
            refusal_reason=log_refusal_reason,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            latency_ms=(time.perf_counter() - wall_start) * 1000.0,
            prompt_version="none",
            model=self._settings.llm_model,
        )

        await self._conversations.add_message(
            conversation_id=conversation_id,
            role=ConversationRole.user,
            content=stored_user,
            citations_json=None,
            refused=False,
        )
        await self._conversations.add_message(
            conversation_id=conversation_id,
            role=ConversationRole.assistant,
            content=assistant_text,
            citations_json=[c.model_dump() for c in citations] if citations else None,
            refused=refused,
        )

        total_ms = (time.perf_counter() - wall_start) * 1000.0
        return ChatQueryResponse(
            answer=assistant_text,
            citations=citations,
            confidence=confidence,
            conversation_id=conversation_id,
            refused=refused,
            refusal_reason=refusal_reason,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            latency_ms=total_ms,
            low_confidence=low_confidence,
        )


async def _resolve_collection_ids(
    request: ChatQueryRequest,
    collection_repo: CollectionRepository,
) -> list[str]:
    if request.collection_ids:
        return list(request.collection_ids)
    return await collection_repo.list_all_collection_ids()
