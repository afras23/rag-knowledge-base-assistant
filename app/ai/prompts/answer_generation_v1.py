"""Answer generation prompt (v1)."""

from __future__ import annotations

SYSTEM_PROMPT = (
    "Answer ONLY using the provided context chunks. If the answer is not in the context, "
    "say 'I don't have enough information.' Never use knowledge outside provided context. "
    "Cite sources as [Source: title, section]."
)

USER_TEMPLATE = """Context chunks:
{chunks}

Question:
{question}
"""


def build(*, chunks: str, question: str) -> tuple[str, str, str]:
    """
    Build system and user prompts for grounded answer generation.

    Args:
        chunks: Formatted context text.
        question: User question.

    Returns:
        Tuple of (system_prompt, user_prompt, version_string).
    """
    user_prompt = USER_TEMPLATE.format(chunks=chunks, question=question)
    return SYSTEM_PROMPT, user_prompt, "answer_generation_v1"
