"""
Custom exception hierarchy for the RAG Knowledge Base Assistant.

All exceptions provide:
- status_code: HTTP status code or a sensible service status for error mapping
- error_code: stable machine-readable identifier
- message: user-friendly error message
- context: structured key/value data suitable for logs (never raw secrets/PII)
"""

from __future__ import annotations

from typing import Any


class BaseAppError(Exception):
    """Base application error with structured metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize a structured application error.

        Args:
            message: Human-readable error message.
            status_code: HTTP status code mapping for API responses.
            error_code: Stable machine-readable error identifier.
            context: Optional structured context for logs/diagnostics.

        Returns:
            None
        """
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.context = context or {}

    def to_error_detail(self) -> dict[str, Any]:
        """Convert error to a serializable detail dictionary."""
        return {
            "code": self.error_code,
            "message": self.message,
            "details": self.context,
        }


class IngestionError(BaseAppError):
    """Raised when document ingestion fails at any stage."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create an ingestion error."""
        super().__init__(
            message,
            status_code=400,
            error_code="INGESTION_FAILED",
            context=context,
        )


class ExtractionError(BaseAppError):
    """Raised when document parsing/text extraction fails."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create an extraction error."""
        super().__init__(
            message,
            status_code=400,
            error_code="EXTRACTION_FAILED",
            context=context,
        )


class RetrievalError(BaseAppError):
    """Raised when retrieval fails due to indexing or query issues."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a retrieval error."""
        super().__init__(
            message,
            status_code=503,
            error_code="RETRIEVAL_FAILED",
            context=context,
        )


class GenerationError(BaseAppError):
    """Raised when grounded generation fails."""

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a generation error."""
        super().__init__(
            message,
            status_code=503,
            error_code="GENERATION_FAILED",
            context=context,
        )


class GuardrailError(BaseAppError):
    """Base class for safety/guardrail enforcement failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a guardrail enforcement error."""
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            context=context,
        )


class PromptInjectionDetected(GuardrailError):
    """Raised when prompt injection patterns are detected in user input."""

    def __init__(
        self,
        message: str = "Potential prompt injection detected",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a prompt injection error."""
        super().__init__(
            message,
            status_code=400,
            error_code="PROMPT_INJECTION_DETECTED",
            context=context,
        )


class PiiDetected(GuardrailError):
    """Raised when PII is detected in user input or retrieved context."""

    def __init__(
        self,
        message: str = "PII detected and blocked",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a PII detection error."""
        super().__init__(
            message,
            status_code=400,
            error_code="PII_DETECTED",
            context=context,
        )


class InsufficientEvidence(GuardrailError):
    """Raised when retrieval does not provide enough evidence to answer safely."""

    def __init__(
        self,
        message: str = "Insufficient evidence to answer from documents",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create an insufficient evidence error."""
        super().__init__(
            message,
            status_code=200,
            error_code="INSUFFICIENT_EVIDENCE",
            context=context,
        )


class CostLimitExceeded(BaseAppError):
    """Raised when a configured daily or per-request cost limit is exceeded."""

    def __init__(
        self,
        message: str = "Daily cost limit exceeded",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a cost limit error."""
        super().__init__(
            message,
            status_code=503,
            error_code="COST_LIMIT_EXCEEDED",
            context=context,
        )


class RateLimitExceeded(BaseAppError):
    """Raised when an upstream provider is rate limited."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a rate limit error."""
        super().__init__(
            message,
            status_code=429,
            error_code="RATE_LIMIT_EXCEEDED",
            context=context,
        )


class RetryableError(BaseAppError):
    """Raised for failures where retries may succeed."""

    def __init__(
        self,
        message: str = "Retryable error",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a retryable error."""
        super().__init__(
            message,
            status_code=503,
            error_code="RETRYABLE_ERROR",
            context=context,
        )


class DocumentNotFoundError(BaseAppError):
    """Raised when a requested document does not exist."""

    def __init__(
        self,
        message: str = "Document not found",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a document not found error."""
        super().__init__(
            message,
            status_code=404,
            error_code="DOCUMENT_NOT_FOUND",
            context=context,
        )


class CollectionNotFoundError(BaseAppError):
    """Raised when a requested collection does not exist."""

    def __init__(
        self,
        message: str = "Collection not found",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a collection not found error."""
        super().__init__(
            message,
            status_code=404,
            error_code="COLLECTION_NOT_FOUND",
            context=context,
        )


class CollectionNotEmptyError(BaseAppError):
    """Raised when a collection cannot be deleted because it still has documents."""

    def __init__(
        self,
        message: str = "Collection is not empty",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a collection not empty error."""
        super().__init__(
            message,
            status_code=409,
            error_code="COLLECTION_NOT_EMPTY",
            context=context,
        )


class ConversationNotFoundError(BaseAppError):
    """Raised when a requested conversation does not exist."""

    def __init__(
        self,
        message: str = "Conversation not found",
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Create a conversation not found error."""
        super().__init__(
            message,
            status_code=404,
            error_code="CONVERSATION_NOT_FOUND",
            context=context,
        )
