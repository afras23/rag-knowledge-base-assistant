"""Prompt template loader (name + version dispatch)."""

from __future__ import annotations

from typing import Any


def get_prompt(name: str, version: str, **kwargs: Any) -> tuple[str, str, str]:
    """
    Resolve a prompt template by logical name and version.

    Args:
        name: Template family (e.g. ``answer_generation``, ``query_rewrite``).
        version: Version tag (e.g. ``v1``).
        kwargs: Template-specific placeholders.

    Returns:
        ``(system_prompt, user_prompt, version_string)`` for auditing.

    Raises:
        ValueError: If the name/version pair is unknown.
    """
    key = f"{name}:{version}"
    if key == "answer_generation:v1":
        from app.ai.prompts.answer_generation_v1 import build as build_answer

        return build_answer(**kwargs)
    if key == "query_rewrite:v1":
        from app.ai.prompts.query_rewrite_v1 import build as build_rewrite

        return build_rewrite(**kwargs)
    raise ValueError(f"Unknown prompt template: {key}")
