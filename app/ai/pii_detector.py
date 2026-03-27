"""
PII detection and handling for ingestion and query paths (Phase 7).
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings

logger = logging.getLogger(__name__)

PiiPolicy = Literal["block", "redact", "warn"]

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
)
_US_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(\s*\d{3}\s*\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b",
)
_UK_PHONE_RE = re.compile(
    r"(?:\+44\s?7\d{3}\s?\d{6}|\+44\s?\d{2,4}\s?\d{6,8}|07\d{9}|\d{5}\s?\d{6})\b",
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_UK_NI_RE = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
_CARD_RE = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")


class PiiScanResult(BaseModel):
    """Outcome of a PII scan."""

    has_pii: bool = Field(..., description="Whether any PII pattern matched")
    redacted_text: str = Field(..., description="Text after redaction (or original)")
    categories: list[str] = Field(default_factory=list, description="Matched PII categories")


class PiiDetector:
    """Detect common PII patterns with configurable enforcement."""

    def __init__(self, settings: Settings) -> None:
        """
        Initialize detector.

        Args:
            settings: Application settings including ``pii_policy``.
        """
        self._policy: PiiPolicy = settings.pii_policy

    def scan_text(self, text: str) -> PiiScanResult:
        """
        Scan text for PII and apply policy (redact when configured).

        Args:
            text: Input text (query or chunk body).

        Returns:
            Scan result with optional redacted text.
        """
        categories: list[str] = []
        if _EMAIL_RE.search(text):
            categories.append("email")
        if _US_PHONE_RE.search(text) or _UK_PHONE_RE.search(text):
            categories.append("phone")
        if _SSN_RE.search(text):
            categories.append("ssn")
        if _UK_NI_RE.search(text):
            categories.append("national_insurance")
        if _CARD_RE.search(text):
            categories.append("credit_card")

        has_pii = len(categories) > 0
        if not has_pii:
            return PiiScanResult(has_pii=False, redacted_text=text, categories=[])

        redacted = text
        redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
        redacted = _US_PHONE_RE.sub("[REDACTED_PHONE]", redacted)
        redacted = _UK_PHONE_RE.sub("[REDACTED_PHONE]", redacted)
        redacted = _SSN_RE.sub("[REDACTED_SSN]", redacted)
        redacted = _UK_NI_RE.sub("[REDACTED_NI]", redacted)
        redacted = _CARD_RE.sub("[REDACTED_CARD]", redacted)

        if self._policy == "warn":
            logger.warning(
                "PII detected in text",
                extra={"pii_categories": categories},
            )

        return PiiScanResult(has_pii=True, redacted_text=redacted, categories=categories)
