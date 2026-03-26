"""
Embedding service for ingestion (Phase 4 — Embeddings + Indexing).

This module provides a provider abstraction for embeddings and a batching embedder.
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

import anyio
from pydantic import BaseModel, Field

from app.config import settings
from app.core.exceptions import IngestionError
from app.services.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

DEFAULT_EMBED_COST_PER_1K_TOKENS = 0.00002
CHARS_PER_TOKEN_ESTIMATE = 4
DEFAULT_LOCAL_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_OPENAI_EMBEDDING_MODEL_NAME = "text-embedding-3-small"
DEFAULT_OPENAI_PRICE_PER_1M_TOKENS_USD = 0.02


def _estimate_tokens_for_text(text: str) -> int:
    """Approximate token count from character length."""
    normalized_length = max(len(text.strip()), 1)
    return max(1, math.ceil(normalized_length / CHARS_PER_TOKEN_ESTIMATE))


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding provider interface used by indexing and query pipelines."""

    @property
    def model_name(self) -> str:
        """Return the embedding model name for audit logging."""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of input texts."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""


@dataclass
class EmbeddingBatchTelemetry:
    """Per-batch telemetry for embedding operations."""

    latency_ms: float
    input_tokens: int
    cost_usd: float


class LocalBgeSmallEmbeddingProvider:
    """Local embeddings provider using sentence-transformers BGE-small."""

    def __init__(self, *, model_name: str | None = None) -> None:
        self._model_name = model_name or DEFAULT_LOCAL_EMBEDDING_MODEL_NAME
        self._model: object | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _get_model(self) -> object:
        if self._model is None:
            try:
                sentence_transformers = __import__("sentence_transformers")
            except ModuleNotFoundError as exc:
                raise IngestionError(
                    "sentence-transformers is not installed",
                    context={"required_package": "sentence-transformers", "model": self._model_name},
                ) from exc
            self._model = sentence_transformers.SentenceTransformer(self._model_name)
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        start_time = time.perf_counter()

        def _encode() -> list[list[float]]:
            model = self._get_model()
            vectors = model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
            return [list(map(float, row)) for row in vectors]

        vectors = await anyio.to_thread.run_sync(_encode)
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "Local embeddings batch completed",
            extra={
                "embedding_provider": "local",
                "model": self._model_name,
                "batch_size": len(texts),
                "latency_ms": latency_ms,
            },
        )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        model = self._get_model()
        vector = model.encode([text], normalize_embeddings=True)[0]  # type: ignore[attr-defined]
        return [float(v) for v in vector]


class OpenAIEmbeddingProvider:
    """OpenAI embeddings provider using langchain-openai OpenAIEmbeddings."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str | None = None,
        price_per_1m_tokens_usd: float | None = None,
    ) -> None:
        if not api_key:
            raise IngestionError("OPENAI_API_KEY is required for OpenAI embeddings", context={"provider": "openai"})
        self._model_name = model_name or DEFAULT_OPENAI_EMBEDDING_MODEL_NAME
        self._price_per_1m_tokens_usd = (
            float(price_per_1m_tokens_usd)
            if price_per_1m_tokens_usd is not None
            else DEFAULT_OPENAI_PRICE_PER_1M_TOKENS_USD
        )
        try:
            langchain_openai = __import__("langchain_openai")
        except ModuleNotFoundError as exc:
            raise IngestionError(
                "langchain-openai is not installed",
                context={"required_package": "langchain-openai", "model": self._model_name},
            ) from exc
        self._client = langchain_openai.OpenAIEmbeddings(model=self._model_name, api_key=api_key)
        self.last_batch_telemetry: EmbeddingBatchTelemetry | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        start_time = time.perf_counter()
        vectors = await self._client.aembed_documents(texts)
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        input_tokens = sum(_estimate_tokens_for_text(text) for text in texts)
        cost_usd = (input_tokens / 1_000_000.0) * self._price_per_1m_tokens_usd
        self.last_batch_telemetry = EmbeddingBatchTelemetry(
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            cost_usd=round(cost_usd, 8),
        )
        logger.info(
            "OpenAI embeddings batch completed",
            extra={
                "embedding_provider": "openai",
                "model": self._model_name,
                "batch_size": len(texts),
                "input_tokens": input_tokens,
                "cost_usd": self.last_batch_telemetry.cost_usd,
                "latency_ms": latency_ms,
            },
        )
        return cast(list[list[float]], vectors)

    def embed_query(self, text: str) -> list[float]:
        vector = self._client.embed_query(text)
        return [float(v) for v in vector]


def get_embedding_provider() -> EmbeddingProvider:
    """
    Build an embedding provider from settings.

    Returns:
        EmbeddingProvider instance (local by default).

    Raises:
        IngestionError: If the configured provider cannot be initialized.
    """
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider(api_key=settings.openai_api_key)
    return LocalBgeSmallEmbeddingProvider()


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
        embedding_provider: EmbeddingProvider | None = None,
        batch_size: int | None = None,
        max_retries: int | None = None,
        initial_backoff_seconds: float | None = None,
        circuit_breaker_threshold: int | None = None,
    ) -> None:
        """
        Initialize document embedder.

        Args:
            embedding_provider: Optional injected embedding provider.
            batch_size: Number of chunks to embed per batch.
            max_retries: Max retries per failed batch.
            initial_backoff_seconds: Initial backoff duration for retries.
            circuit_breaker_threshold: Consecutive failed batches before abort.
        """
        self._config = EmbedderConfig(
            batch_size=int(batch_size) if batch_size is not None else settings.embed_batch_size,
            max_retries=int(max_retries) if max_retries is not None else settings.embed_max_retries,
            initial_backoff_seconds=initial_backoff_seconds
            if initial_backoff_seconds is not None
            else float(settings.embed_initial_backoff_seconds),
            circuit_breaker_threshold=circuit_breaker_threshold
            if circuit_breaker_threshold is not None
            else int(settings.embed_circuit_breaker_threshold),
            cost_per_1k_tokens=0.0,
        )
        self._validate_config(self._config)
        self._embedding_provider = embedding_provider or get_embedding_provider()
        self.last_batch_telemetry: EmbeddingBatchTelemetry | None = None

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
                batch_cost = 0.0
                if (
                    isinstance(self._embedding_provider, OpenAIEmbeddingProvider)
                    and self._embedding_provider.last_batch_telemetry
                ):
                    self.last_batch_telemetry = self._embedding_provider.last_batch_telemetry
                    batch_cost = float(self._embedding_provider.last_batch_telemetry.cost_usd)
                total_cost += float(batch_cost)
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
                        "cost_usd": batch_cost,
                        "embedding_model": self._embedding_provider.model_name,
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

                backoff_seconds = self._config.initial_backoff_seconds * (2**attempt) * random.uniform(0.8, 1.2)
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
        """Call embedding provider for a batch."""
        return await self._embedding_provider.embed_texts(texts)

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

    def _build_default_provider(self) -> EmbeddingProvider:
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
        if not isinstance(provider, EmbeddingProvider):
            raise IngestionError(
                "Initialized embeddings provider does not match required interface",
                context={"provider_type": provider.__class__.__name__},
            )
        return provider
