"""
Deterministic query-rewrite triggers (Phase 5). LLM rewrite is Phase 6.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

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


class QueryRewriter:
    """Heuristic query rewrite triggers; LLM rewrite is not yet wired."""

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

    async def rewrite(self, query: str) -> str:
        """
        Return the effective query string (placeholder: always original).

        Args:
            query: Raw user query.

        Returns:
            Query text to use for retrieval (unchanged until Phase 6 LLM client).
        """
        analysis = self.analyze(query)
        if analysis.should_rewrite:
            logger.info(
                "Query rewrite recommended by heuristic",
                extra={"reason": analysis.reason, "word_count": len(query.split())},
            )
        return query

    @staticmethod
    def _looks_like_noun_phrase_without_verb(stripped: str, words: list[str]) -> bool:
        if len(words) > 4 or "?" in stripped:
            return False
        if _WH_WORD_RE.search(stripped) or _VERB_HINT_RE.search(stripped):
            return False
        return len(words) <= 3
