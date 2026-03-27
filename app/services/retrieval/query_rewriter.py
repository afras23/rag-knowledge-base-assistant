"""
Deterministic query-rewrite triggers plus optional LLM rewrite (Phase 5–6).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.ai.llm_client import LlmClient
from app.ai.prompts.loader import get_prompt
from app.config import Settings

logger = logging.getLogger(__name__)

_PRONOUN_RE = re.compile(
    r"\b(it|they|them|their|this|that|these|those|its)\b",
    re.IGNORECASE,
)
_VERB_HINT_RE = re.compile(
    r"\b(is|are|was|were|be|been|being|have|has|had|do|does|did|can|could|should|would|will|may|might)\b",
    re.IGNORECASE,
)
_WH_WORD_RE = re.compile(r"\b(what|when|where|why|how|who|which)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryRewriteAnalysis:
    """Heuristic analysis for optional query rewriting."""

    should_rewrite: bool
    reason: str | None


@dataclass(frozen=True)
class QueryRewriteResult:
    """Effective query text after optional LLM rewrite."""

    effective_query: str
    was_rewritten: bool
    rewritten_query: str | None


class QueryRewriter:
    """Heuristic triggers with optional LLM-backed rewrite."""

    def __init__(self, llm_client: LlmClient | None = None, settings: Settings | None = None) -> None:
        """
        Initialize rewriter.

        Args:
            llm_client: Optional LLM client; when omitted, rewrite is heuristic-only.
            settings: Settings bundle (required when ``llm_client`` is set).
        """
        self._llm = llm_client
        self._settings = settings

    def analyze(self, query: str) -> QueryRewriteAnalysis:
        """
        Determine whether rewrite is recommended by deterministic rules.

        Args:
            query: Raw user query.

        Returns:
            Analysis with trigger flag and short reason for logging.
        """
        stripped = query.strip()
        if not stripped:
            return QueryRewriteAnalysis(should_rewrite=False, reason=None)

        words = stripped.split()
        word_count = len(words)

        if word_count < 5:
            return QueryRewriteAnalysis(should_rewrite=True, reason="short_query")

        if _PRONOUN_RE.search(stripped):
            return QueryRewriteAnalysis(should_rewrite=True, reason="pronoun_without_context")

        if self._looks_like_noun_phrase_without_verb(stripped, words):
            return QueryRewriteAnalysis(should_rewrite=True, reason="noun_phrase_no_verb")

        return QueryRewriteAnalysis(should_rewrite=False, reason=None)

    async def rewrite(self, query: str, correlation_id: str | None = None) -> QueryRewriteResult:
        """
        Return the effective query string, optionally via LLM rewrite.

        Args:
            query: Raw user query.
            correlation_id: Optional correlation id for LLM logging.

        Returns:
            Effective query and rewrite flags.
        """
        stripped = query.strip()
        if not stripped:
            return QueryRewriteResult(effective_query=query, was_rewritten=False, rewritten_query=None)

        analysis = self.analyze(query)
        if analysis.should_rewrite:
            logger.info(
                "Query rewrite recommended by heuristic",
                extra={"reason": analysis.reason, "word_count": len(query.split())},
            )

        if not analysis.should_rewrite:
            return QueryRewriteResult(effective_query=stripped, was_rewritten=False, rewritten_query=None)

        if self._llm is None or self._settings is None:
            return QueryRewriteResult(effective_query=stripped, was_rewritten=False, rewritten_query=None)

        system_prompt, user_prompt, pv = get_prompt("query_rewrite", "v1", question=stripped)
        llm_result = await self._llm.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_version=pv,
            correlation_id=correlation_id,
        )
        candidate = (llm_result.content or "").strip() or stripped
        changed = candidate.lower() != stripped.lower()
        return QueryRewriteResult(
            effective_query=candidate,
            was_rewritten=changed,
            rewritten_query=candidate if changed else None,
        )

    @staticmethod
    def _looks_like_noun_phrase_without_verb(stripped: str, words: list[str]) -> bool:
        if len(words) > 4 or "?" in stripped:
            return False
        if _WH_WORD_RE.search(stripped) or _VERB_HINT_RE.search(stripped):
            return False
        return len(words) <= 3
