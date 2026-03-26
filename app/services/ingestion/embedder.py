"""
Embedding service for ingestion (Phase 3 — Component 3).

This module converts chunks into vector embeddings using a LangChain embeddings
provider with batching, retries, and cost tracking.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import anyio
from pydantic import BaseModel, Field

from app.config import settings
from app.core.exceptions import IngestionError
from app.services.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

DEFAULT_EMBED_BATCH_SIZE = 32
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF_SECONDS = 0.5
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_EMBED_COST_PER_1K_TOKENS = 0.00002
CHARS_PER_TOKEN_ESTIMATE = 4


@runtime_checkable
class LangChainEmbeddingsProvider(Protocol):
    """Protocol for LangChain-compatible embeddings providers."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Synchronously embed a list of texts."""

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Asynchronously embed a list of texts."""


class EmbeddingResult(BaseModel):
    """Result summary for embedding execution."""

    total_chunks: int = Field(..., ge=0, description="Total chunks requested for embedding")
    embedded_chunks: int = Field(..., ge=0, description="Successfully embedded chunks")
    failed_chunks: int = Field(..., ge=0, description="Chunks that failed to embed")
    total_cost: float = Field(..., ge=0, description="Estimated total embedding cost in USD")


@dataclass(frozen=True)
class EmbedderConfig:
    """Configuration for embedding execution."""

    batch_size: int
    max_retries: int
    initial_backoff_seconds: float
    circuit_breaker_threshold: int
    cost_per_1k_tokens: float


class DocumentEmbedder:
    """Service to convert ingestion chunks into embeddings."""

    def __init__(
        self,
        *,
        embeddings_provider: LangChainEmbeddingsProvider | None = None,
        batch_size: int | None = None,
        max_retries: int | None = None,
        initial_backoff_seconds: float | None = None,
        circuit_breaker_threshold: int | None = None,
        cost_per_1k_tokens: float | None = None,
    ) -> None:
        """
        Initialize document embedder.

        Args:
            embeddings_provider: Optional injected LangChain embedding provider.
            batch_size: Number of chunks to embed per batch.
            max_retries: Max retries per failed batch.
            initial_backoff_seconds: Initial backoff duration for retries.
            circuit_breaker_threshold: Consecutive failed batches before abort.
            cost_per_1k_tokens: Estimated USD cost per 1k input tokens.
        """
        self._config = EmbedderConfig(
            batch_size=batch_size or int(getattr(settings, "embed_batch_size", DEFAULT_EMBED_BATCH_SIZE)),
            max_retries=max_retries or int(getattr(settings, "embed_max_retries", DEFAULT_MAX_RETRIES)),
            initial_backoff_seconds=initial_backoff_seconds
            if initial_backoff_seconds is not None
            else float(getattr(settings, "embed_initial_backoff_seconds", DEFAULT_INITIAL_BACKOFF_SECONDS)),
            circuit_breaker_threshold=circuit_breaker_threshold
            or int(getattr(settings, "embed_circuit_breaker_threshold", DEFAULT_CIRCUIT_BREAKER_THRESHOLD)),
            cost_per_1k_tokens=cost_per_1k_tokens
            if cost_per_1k_tokens is not None
            else float(getattr(settings, "embed_cost_per_1k_tokens", DEFAULT_EMBED_COST_PER_1K_TOKENS)),
        )
        self._validate_config(self._config)
        self._embeddings_provider = embeddings_provider or self._build_default_provider()

    @property
    def config(self) -> EmbedderConfig:
        """Return active embedder config."""
        return self._config

    async def embed_chunks(self, chunks: list[Chunk]) -> tuple[list[list[float] | None], EmbeddingResult]:
        """
        Embed chunks in batches while preserving alignment with input order.

        Args:
            chunks: Chunks to embed.

        Returns:
            A tuple containing:
            - embeddings aligned to chunk order (failed entries are None)
            - embedding result summary

        Raises:
            IngestionError: If circuit breaker is tripped or provider fails fatally.
        """
        total_chunks = len(chunks)
        if total_chunks == 0:
            return [], EmbeddingResult(total_chunks=0, embedded_chunks=0, failed_chunks=0, total_cost=0.0)

        embeddings: list[list[float] | None] = [None] * total_chunks
        embedded_chunks = 0
        failed_chunks = 0
        total_cost = 0.0
        consecutive_batch_failures = 0

        for batch_start in range(0, total_chunks, self._config.batch_size):
            batch_end = min(batch_start + self._config.batch_size, total_chunks)
            batch_chunks = chunks[batch_start:batch_end]
            batch_texts = [chunk.text for chunk in batch_chunks]

            logger.info(
                "Embedding batch started",
                extra={
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "batch_size": len(batch_texts),
                    "total_chunks": total_chunks,
                },
            )

            try:
                batch_vectors = await self._embed_with_retry(batch_texts)
                batch_cost = self._estimate_cost_for_texts(batch_texts)
                total_cost += batch_cost
                consecutive_batch_failures = 0

                for index_offset, vector in enumerate(batch_vectors):
                    embeddings[batch_start + index_offset] = vector
                embedded_chunks += len(batch_vectors)

                logger.info(
                    "Embedding batch completed",
                    extra={
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "embedded_batch_chunks": len(batch_vectors),
                        "estimated_batch_cost": batch_cost,
                        "processed_chunks": embedded_chunks + failed_chunks,
                    },
                )
            except IngestionError as exc:
                failed_count = len(batch_texts)
                failed_chunks += failed_count
                consecutive_batch_failures += 1

                logger.error(
                    "Embedding batch failed",
                    extra={
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "failed_batch_chunks": failed_count,
                        "consecutive_failures": consecutive_batch_failures,
                        "error_code": exc.error_code,
                        "error_message": exc.message,
                    },
                )

                if consecutive_batch_failures >= self._config.circuit_breaker_threshold:
                    raise IngestionError(
                        "Embedding circuit breaker tripped after repeated batch failures",
                        context={
                            "threshold": self._config.circuit_breaker_threshold,
                            "failed_chunks": failed_chunks,
                            "embedded_chunks": embedded_chunks,
                        },
                    ) from exc

        result = EmbeddingResult(
            total_chunks=total_chunks,
            embedded_chunks=embedded_chunks,
            failed_chunks=failed_chunks,
            total_cost=round(total_cost, 8),
        )
        logger.info(
            "Embedding run completed",
            extra={
                "total_chunks": result.total_chunks,
                "embedded_chunks": result.embedded_chunks,
                "failed_chunks": result.failed_chunks,
                "total_cost": result.total_cost,
            },
        )
        return embeddings, result

    async def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch with exponential backoff retries."""
        last_error: Exception | None = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                vectors = await self._embed_texts(texts)
                if len(vectors) != len(texts):
                    raise IngestionError(
                        "Embedding provider returned mismatched vector count",
                        context={"requested": len(texts), "returned": len(vectors)},
                    )
                return vectors
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                is_transient = self._is_transient_embedding_error(exc)
                if (not is_transient) or attempt == self._config.max_retries:
                    break

                backoff_seconds = self._config.initial_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Embedding batch retry scheduled",
                    extra={
                        "attempt": attempt,
                        "max_retries": self._config.max_retries,
                        "backoff_seconds": backoff_seconds,
                        "error": str(exc),
                    },
                )
                await anyio.sleep(backoff_seconds)

        raise IngestionError(
            "Embedding batch failed after retries",
            context={"error": str(last_error) if last_error else "unknown"},
        ) from last_error

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Call async/sync embed_documents from provider safely."""
        if hasattr(self._embeddings_provider, "aembed_documents"):
            return await self._embeddings_provider.aembed_documents(texts)
        return await anyio.to_thread.run_sync(self._embeddings_provider.embed_documents, texts)

    def _estimate_cost_for_texts(self, texts: list[str]) -> float:
        """Estimate embedding cost from approximate token count."""
        total_tokens = sum(self._estimate_tokens(text) for text in texts)
        return (total_tokens / 1000.0) * self._config.cost_per_1k_tokens

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Approximate token count from character length."""
        normalized_length = max(len(text.strip()), 1)
        return max(1, math.ceil(normalized_length / CHARS_PER_TOKEN_ESTIMATE))

    @staticmethod
    def _is_transient_embedding_error(exc: Exception) -> bool:
        """Detect likely transient failures (rate limits/timeouts/server errors)."""
        exception_name = exc.__class__.__name__.lower()
        message = str(exc).lower()
        transient_markers = (
            "rate",
            "timeout",
            "temporar",
            "503",
            "502",
            "connection",
            "unavailable",
        )
        if "rate" in exception_name or "timeout" in exception_name:
            return True
        return any(marker in message for marker in transient_markers)

    @staticmethod
    def _validate_config(config: EmbedderConfig) -> None:
        """Validate embedder configuration values."""
        if config.batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if config.max_retries <= 0:
            raise ValueError("max_retries must be > 0")
        if config.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be >= 0")
        if config.circuit_breaker_threshold <= 0:
            raise ValueError("circuit_breaker_threshold must be > 0")
        if config.cost_per_1k_tokens < 0:
            raise ValueError("cost_per_1k_tokens must be >= 0")

    def _build_default_provider(self) -> LangChainEmbeddingsProvider:
        """
        Build default LangChain embeddings provider.

        Uses OpenAIEmbeddings from langchain-openai if available.
        """
        model_name = str(getattr(settings, "embedding_model_name", "text-embedding-3-small"))
        try:
            langchain_openai = __import__("langchain_openai")
            provider = langchain_openai.OpenAIEmbeddings(model=model_name)
        except ModuleNotFoundError as exc:
            raise IngestionError(
                "LangChain OpenAI embeddings provider is not installed",
                context={"required_package": "langchain-openai", "model": model_name},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(
                "Failed to initialize default embeddings provider",
                context={"model": model_name, "error": str(exc)},
            ) from exc

        # Runtime protocol guard for clearer failures if the provider shape changes.
        if not isinstance(provider, LangChainEmbeddingsProvider):
            raise IngestionError(
                "Initialized embeddings provider does not match required interface",
                context={"provider_type": provider.__class__.__name__},
            )
        return provider
