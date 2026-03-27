"""
Prompt injection and unsafe-input guardrails (Phase 7).
"""

from __future__ import annotations

import logging
import re
from re import Pattern

import anyio
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_IGNORE_INSTRUCTIONS: Pattern[str] = re.compile(
    r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions\b",
    re.IGNORECASE,
)
_IGNORE_ALL: Pattern[str] = re.compile(r"\bignore\s+all\s+instructions\b", re.IGNORECASE)
_ROLE_PLAY: Pattern[str] = re.compile(
    r"\b(?:you\s+are\s+now|act\s+as|pretend\s+(?:you\s+are|to\s+be))\b",
    re.IGNORECASE,
)
_ROLE_MARKERS: Pattern[str] = re.compile(
    r"(?:^|\n)\s*(?:system|assistant)\s*:|<<SYS>>",
    re.IGNORECASE | re.MULTILINE,
)
_EXTRACTION: Pattern[str] = re.compile(
    r"\b(?:repeat\s+your\s+instructions|what\s+are\s+your\s+rules)\b",
    re.IGNORECASE,
)
_CODE_FENCE_ROLES: Pattern[str] = re.compile(
    r"```(?:\w*\s*)?(?:system|assistant|user)\b",
    re.IGNORECASE,
)


class GuardrailResult(BaseModel):
    """Outcome of user input safety checks."""

    is_safe: bool = Field(..., description="False when policy violations were detected")
    violation_type: str | None = Field(default=None, description="Short machine-readable type")
    violation_category: str | None = Field(default=None, description="High-level category for logs")
    sanitised_input: str = Field(..., description="Sanitised or original text when safe")


class GuardrailService:
    """Detect prompt-injection patterns without logging raw user content."""

    async def check_input(self, user_input: str) -> GuardrailResult:
        """
        Scan user input for injection-like patterns.

        Args:
            user_input: Raw user message.

        Returns:
            Guardrail result; ``is_safe`` is False when a pattern matches.
        """
        return await anyio.to_thread.run_sync(self._check_input_sync, user_input)

    def _check_input_sync(self, user_input: str) -> GuardrailResult:
        stripped = user_input.strip()
        if not stripped:
            return GuardrailResult(is_safe=True, sanitised_input=user_input)

        if _IGNORE_INSTRUCTIONS.search(stripped) or _IGNORE_ALL.search(stripped):
            self._log_category("prompt_injection_ignore_instructions")
            return GuardrailResult(
                is_safe=False,
                violation_type="ignore_instructions",
                violation_category="prompt_injection_ignore_instructions",
                sanitised_input="",
            )

        if _ROLE_PLAY.search(stripped):
            self._log_category("prompt_injection_role_manipulation")
            return GuardrailResult(
                is_safe=False,
                violation_type="role_manipulation",
                violation_category="prompt_injection_role_manipulation",
                sanitised_input="",
            )

        if _ROLE_MARKERS.search(stripped):
            self._log_category("prompt_injection_role_markers")
            return GuardrailResult(
                is_safe=False,
                violation_type="role_markers",
                violation_category="prompt_injection_role_markers",
                sanitised_input="",
            )

        if _EXTRACTION.search(stripped):
            self._log_category("prompt_injection_instruction_extraction")
            return GuardrailResult(
                is_safe=False,
                violation_type="instruction_extraction",
                violation_category="prompt_injection_instruction_extraction",
                sanitised_input="",
            )

        if _CODE_FENCE_ROLES.search(stripped):
            self._log_category("prompt_injection_code_fence_roles")
            return GuardrailResult(
                is_safe=False,
                violation_type="code_fence_roles",
                violation_category="prompt_injection_code_fence_roles",
                sanitised_input="",
            )

        return GuardrailResult(is_safe=True, sanitised_input=stripped)

    @staticmethod
    def _log_category(violation_category: str) -> None:
        logger.warning(
            "Guardrail violation detected",
            extra={"violation_category": violation_category},
        )
