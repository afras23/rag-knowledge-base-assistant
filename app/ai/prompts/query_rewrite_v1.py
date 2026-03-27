"""Query rewrite prompt (v1)."""

from __future__ import annotations

SYSTEM_PROMPT = "Rewrite the question to be more specific for document search. Return ONLY the rewritten question."

USER_TEMPLATE = """Question to rewrite:
{question}
"""


def build(*, question: str) -> tuple[str, str, str]:
    """
    Build system and user prompts for query rewriting.

    Args:
        question: Original user question.

    Returns:
        Tuple of (system_prompt, user_prompt, version_string).
    """
    user_prompt = USER_TEMPLATE.format(question=question)
    return SYSTEM_PROMPT, user_prompt, "query_rewrite_v1"
