"""
LLM-as-judge for answer quality (optional; requires OpenAI credentials).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.llm_client import LlmClient
from app.config import settings
from app.core.exceptions import CostLimitExceeded, GenerationError

logger = logging.getLogger(__name__)

_JUDGE_SCHEMA_HINT = '{"groundedness":0-5,"correctness":0-5,"completeness":0-5,"rationale":"short"}'


async def score_answer_with_llm_judge(
    *,
    question: str,
    answer: str,
    evidence_excerpt: str,
    llm: LlmClient,
) -> dict[str, Any] | None:
    """
    Ask the configured LLM to score groundedness, correctness, and completeness (0-5).

    Args:
        question: User question.
        answer: Assistant answer to judge.
        evidence_excerpt: Concatenated retrieved evidence (truncated by caller).
        llm: Configured async LLM client.

    Returns:
        Parsed scores dict, or None when API key is missing or parsing fails.
    """
    if not settings.openai_api_key:
        logger.info("LLM judge skipped: openai_api_key not set")
        return None

    system = (
        "You are an evaluation judge. Reply with ONLY valid JSON matching this schema: "
        + _JUDGE_SCHEMA_HINT
        + ". Scores are integers 0-5."
    )
    user = f"Question:\n{question}\n\nEvidence:\n{evidence_excerpt[:8000]}\n\nAnswer:\n{answer}\n"
    try:
        result = await llm.complete(
            system_prompt=system,
            user_prompt=user,
            prompt_version="eval_judge_v1",
            correlation_id="evaluation-judge",
        )
    except (GenerationError, CostLimitExceeded) as exc:
        logger.warning("LLM judge call failed", extra={"error": str(exc)})
        return None

    payload = _extract_json_object(result.content)
    if payload is None:
        logger.warning("LLM judge returned non-JSON output")
        return None
    return payload


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse the first JSON object from model output."""
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", stripped)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return None
    return None
