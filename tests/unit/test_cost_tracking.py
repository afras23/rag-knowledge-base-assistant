"""
Tests for cost tracking helpers and query repository cost APIs (Phase 11).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import AsyncOpenAI

from app.ai.llm_client import LlmClient, get_daily_cost_usd, reset_daily_cost_for_tests, set_daily_cost_for_tests
from app.config import Settings
from app.core.exceptions import CostLimitExceeded
from app.repositories.query_repo import QueryRepository


@pytest.mark.asyncio
async def test_get_daily_cost_sums_query_events() -> None:
    """``get_daily_cost`` returns the scalar sum from the ORM aggregate."""
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one = MagicMock(return_value=3.25)
    session.execute = AsyncMock(return_value=exec_result)
    repo = QueryRepository(session)
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    total = await repo.get_daily_cost(interval_start=start, interval_end=end)
    assert total == 3.25
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_cost_breakdown_groups_rows() -> None:
    """``get_cost_breakdown`` maps ORM rows to (call_type, model, cost) tuples."""
    session = MagicMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[("generation", "gpt-4o", 0.5)])
    session.execute = AsyncMock(return_value=result)
    repo = QueryRepository(session)
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=1)
    rows = await repo.get_cost_breakdown(interval_start=start, interval_end=end)
    assert rows == [("generation", "gpt-4o", 0.5)]


@pytest.mark.asyncio
async def test_get_refusal_breakdown_returns_counts() -> None:
    """``get_refusal_breakdown`` builds a dict from grouped reason rows."""
    session = MagicMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[("insufficient_evidence", 2), ("guardrail_violation", 1)])
    session.execute = AsyncMock(return_value=result)
    repo = QueryRepository(session)
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=1)
    out = await repo.get_refusal_breakdown(interval_start=start, interval_end=end)
    assert out["insufficient_evidence"] == 2
    assert out["guardrail_violation"] == 1


def test_llm_client_blocks_when_daily_budget_exhausted() -> None:
    """``_ensure_daily_budget`` raises when in-memory spend meets the limit."""
    reset_daily_cost_for_tests()
    settings = Settings()
    set_daily_cost_for_tests(settings.max_daily_cost_usd)
    fake_client = AsyncOpenAI(api_key="test-key-for-unit")
    client = LlmClient(settings=settings, async_client=fake_client)
    with pytest.raises(CostLimitExceeded):
        client._ensure_daily_budget()


def test_get_daily_cost_usd_in_memory() -> None:
    """In-memory counter reflects successful LLM calls (tests reset helper)."""
    reset_daily_cost_for_tests()
    set_daily_cost_for_tests(1.5)
    assert get_daily_cost_usd() == 1.5
