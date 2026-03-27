"""
Unit tests for OpenAI LLM client wrapper.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIStatusError, APITimeoutError

from app.ai.llm_client import (
    LlmCallResult,
    LlmClient,
    get_daily_cost_usd,
    reset_daily_cost_for_tests,
    set_daily_cost_for_tests,
)
from app.config import Settings
from app.core.exceptions import CostLimitExceeded, GenerationError


def _mock_response(content: str, in_tok: int, out_tok: int) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    response.usage = MagicMock(prompt_tokens=in_tok, completion_tokens=out_tok)
    return response


@pytest.mark.anyio
async def test_returns_llm_call_result() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test", llm_model="gpt-4o")
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_response("hello", 10, 5))
    client = LlmClient(settings=settings, async_client=mock_client)
    result = await client.complete(
        system_prompt="s",
        user_prompt="u",
        prompt_version="query_rewrite_v1",
        correlation_id="cid-1",
    )
    assert isinstance(result, LlmCallResult)
    assert result.content == "hello"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.prompt_version == "query_rewrite_v1"
    assert result.model == "gpt-4o"
    assert result.cost_usd > 0.0


@pytest.mark.anyio
async def test_cost_tracking_per_call() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test")
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_response("x", 1_000_000, 0))
    client = LlmClient(settings=settings, async_client=mock_client)
    await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")
    assert get_daily_cost_usd() == pytest.approx(2.5, rel=1e-3)


@pytest.mark.anyio
async def test_daily_cost_limit_enforced() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test", max_daily_cost_usd=10.0)
    set_daily_cost_for_tests(10.0)
    mock_client = MagicMock()
    client = LlmClient(settings=settings, async_client=mock_client)
    with pytest.raises(CostLimitExceeded):
        await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")


@pytest.mark.anyio
async def test_retry_on_timeout() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test", llm_max_retries=3)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[APITimeoutError("timeout"), APITimeoutError("timeout"), _mock_response("ok", 1, 1)],
    )
    client = LlmClient(settings=settings, async_client=mock_client)
    result = await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")
    assert result.content == "ok"
    assert mock_client.chat.completions.create.await_count == 3


@pytest.mark.anyio
async def test_circuit_breaker_opens() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test", llm_max_retries=1, llm_circuit_breaker_threshold=5)
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=APITimeoutError("timeout"))
    client = LlmClient(settings=settings, async_client=mock_client)
    for _ in range(5):
        with pytest.raises(GenerationError):
            await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")
    with pytest.raises(GenerationError) as exc_info:
        await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")
    assert "circuit" in str(exc_info.value.message).lower()


@pytest.mark.anyio
async def test_non_retryable_client_error_raises_generation_error() -> None:
    """4xx errors are not retried and surface as ``GenerationError``."""
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test", llm_max_retries=3)
    mock_client = MagicMock()
    http_response = MagicMock()
    http_response.status_code = 400
    client_error = APIStatusError("bad request", response=http_response, body=None)
    mock_client.chat.completions.create = AsyncMock(side_effect=client_error)
    client = LlmClient(settings=settings, async_client=mock_client)
    with pytest.raises(GenerationError):
        await client.complete(system_prompt="s", user_prompt="u", prompt_version="pv")
    assert mock_client.chat.completions.create.await_count == 1


@pytest.mark.anyio
async def test_prompt_version_recorded() -> None:
    reset_daily_cost_for_tests()
    settings = Settings(openai_api_key="sk-test")
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_response("z", 1, 1))
    client = LlmClient(settings=settings, async_client=mock_client)
    result = await client.complete(
        system_prompt="s",
        user_prompt="u",
        prompt_version="answer_generation_v1",
    )
    assert result.prompt_version == "answer_generation_v1"
