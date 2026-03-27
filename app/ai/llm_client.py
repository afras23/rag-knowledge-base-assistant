"""
OpenAI LLM client with retries, circuit breaker, and cost controls (Phase 6).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextvars import Token
from datetime import datetime, timezone
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, Field

from app.config import Settings
from app.core.exceptions import CostLimitExceeded, GenerationError
from app.core.middleware.correlation import correlation_id_ctx

logger = logging.getLogger(__name__)

_daily_cost_by_utc_date: dict[str, float] = {}


def _utc_date_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_daily_cost_usd() -> float:
    """Return accumulated LLM spend for the current UTC day (for tests/metrics)."""
    return _daily_cost_by_utc_date.get(_utc_date_key(), 0.0)


def reset_daily_cost_for_tests() -> None:
    """Clear in-memory daily cost counters (tests only)."""
    _daily_cost_by_utc_date.clear()


def set_daily_cost_for_tests(amount_usd: float) -> None:
    """Seed daily spend for limit tests (tests only)."""
    _daily_cost_by_utc_date[_utc_date_key()] = amount_usd


def _add_daily_cost(amount_usd: float) -> None:
    key = _utc_date_key()
    _daily_cost_by_utc_date[key] = _daily_cost_by_utc_date.get(key, 0.0) + amount_usd


class LlmCallResult(BaseModel):
    """Structured result from a single chat completion call."""

    content: str = Field(..., description="Assistant message content")
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cost_usd: float = Field(..., ge=0.0)
    latency_ms: float = Field(..., ge=0.0)
    model: str = Field(..., description="Resolved model name")
    prompt_version: str = Field(..., description="Prompt template version string")


def _is_retryable_error(exc: OpenAIError) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code is not None:
        return int(exc.status_code) >= 500
    return False


class LlmClient:
    """Async OpenAI chat client with resilience and spend limits."""

    def __init__(
        self,
        *,
        settings: Settings,
        async_client: AsyncOpenAI | None = None,
    ) -> None:
        """
        Initialize the LLM client.

        Args:
            settings: Application settings (model, keys, pricing, limits).
            async_client: Optional injected client (tests).
        """
        self._settings = settings
        base_client = async_client or AsyncOpenAI(api_key=settings.openai_api_key or None)
        self._client = self._maybe_wrap_langsmith(base_client)
        self._consecutive_failures = 0
        self._circuit_open = False

    def _maybe_wrap_langsmith(self, client: AsyncOpenAI) -> AsyncOpenAI:
        """Optionally wrap the OpenAI client for LangSmith tracing."""
        if not self._settings.langsmith_api_key:
            return client
        try:
            from langsmith.wrappers import wrap_openai

            return wrap_openai(client)
        except ImportError:
            return client

    def reset_circuit_for_tests(self) -> None:
        """Reset breaker state (tests only)."""
        self._consecutive_failures = 0
        self._circuit_open = False

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        prompt_version: str,
        correlation_id: str | None = None,
    ) -> LlmCallResult:
        """
        Run a chat completion with retries and accounting.

        Args:
            system_prompt: System message content.
            user_prompt: User message content.
            prompt_version: Prompt template version for auditing.
            correlation_id: Optional request correlation id for logs.

        Returns:
            Parsed LLM call result.

        Raises:
            CostLimitExceeded: When the daily budget is already exhausted.
            GenerationError: When the circuit is open or the model fails after retries.
        """
        ctx_token = self._set_correlation_context(correlation_id)
        try:
            self._ensure_circuit_closed()
            self._ensure_daily_budget()
            return await self._complete_with_retries(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                prompt_version=prompt_version,
                correlation_id=correlation_id,
            )
        finally:
            self._reset_correlation_context(ctx_token)

    def _set_correlation_context(self, correlation_id: str | None) -> Token[str | None] | None:
        if correlation_id is None:
            return None
        return correlation_id_ctx.set(correlation_id)

    @staticmethod
    def _reset_correlation_context(token: Token[str | None] | None) -> None:
        if token is not None:
            correlation_id_ctx.reset(token)

    def _ensure_circuit_closed(self) -> None:
        if self._circuit_open:
            raise GenerationError(
                "LLM circuit breaker is open",
                context={"circuit_open": True},
            )

    def _ensure_daily_budget(self) -> None:
        """
        Block calls when in-process daily spend exceeds the configured limit.

        Spend is accumulated in memory on each successful completion; use
        ``QueryRepository.get_daily_cost`` for persisted totals in metrics.
        """
        if get_daily_cost_usd() >= self._settings.max_daily_cost_usd:
            raise CostLimitExceeded(
                "Daily LLM cost limit reached",
                context={"max_daily_cost_usd": self._settings.max_daily_cost_usd},
            )

    async def _complete_with_retries(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        prompt_version: str,
        correlation_id: str | None,
    ) -> LlmCallResult:
        last_error: OpenAIError | None = None
        max_attempts = self._settings.llm_max_retries
        for attempt in range(1, max_attempts + 1):
            try:
                result = await self._invoke_model(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    prompt_version=prompt_version,
                    correlation_id=correlation_id,
                )
                self._on_call_succeeded()
                return result
            except OpenAIError as exc:
                if not _is_retryable_error(exc):
                    self._on_call_failed()
                    raise GenerationError(
                        "LLM call failed",
                        context={"error": str(exc)},
                    ) from exc
                last_error = exc
                if attempt >= max_attempts:
                    break
                backoff = self._settings.embed_initial_backoff_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(backoff * random.uniform(0.8, 1.2))

        self._on_call_failed()
        assert last_error is not None
        raise GenerationError(
            "LLM call failed after retries",
            context={"attempts": max_attempts, "error": str(last_error)},
        ) from last_error

    def _on_call_succeeded(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open = False

    def _on_call_failed(self) -> None:
        self._consecutive_failures += 1
        threshold = self._settings.llm_circuit_breaker_threshold
        if self._consecutive_failures >= threshold:
            self._circuit_open = True
            logger.error(
                "LLM circuit breaker opened",
                extra={"consecutive_failures": self._consecutive_failures},
            )

    async def _invoke_model(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        prompt_version: str,
        correlation_id: str | None,
    ) -> LlmCallResult:
        start = time.perf_counter()
        model = self._settings.llm_model
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        input_tokens = int(usage.prompt_tokens) if usage else 0
        output_tokens = int(usage.completion_tokens) if usage else 0
        cost = _compute_cost_usd(
            input_tokens,
            output_tokens,
            input_price_per_1m=self._settings.llm_input_price_per_1m_tokens_usd,
            output_price_per_1m=self._settings.llm_output_price_per_1m_tokens_usd,
        )
        _add_daily_cost(cost)
        extra: dict[str, Any] = {
            "model": model,
            "prompt_version": prompt_version,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "latency_ms": latency_ms,
        }
        if correlation_id is not None:
            extra["correlation_id"] = correlation_id
        logger.info("LLM completion succeeded", extra=extra)
        return LlmCallResult(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            model=model,
            prompt_version=prompt_version,
        )


def _compute_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    input_price_per_1m: float,
    output_price_per_1m: float,
) -> float:
    in_cost = (input_tokens / 1_000_000.0) * input_price_per_1m
    out_cost = (output_tokens / 1_000_000.0) * output_price_per_1m
    return round(in_cost + out_cost, 8)
