"""
Integration tests for GET /api/v1/metrics (Phase 11).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import get_db_session
from app.main import app


def _client() -> TestClient:
    return TestClient(app)


def test_metrics_returns_success_envelope() -> None:
    """Metrics endpoint returns 200 with standard success wrapper."""
    mock_q = MagicMock()
    mock_q.aggregate_query_metrics_for_interval = AsyncMock(return_value=(4, 1, 120.0, 2.0))
    mock_q.get_daily_cost = AsyncMock(return_value=1.25)
    mock_q.get_top_queries = AsyncMock(return_value=[("deadbeef", 3, 1)])
    mock_q.get_refusal_breakdown = AsyncMock(return_value={"insufficient_evidence": 1})
    mock_d = MagicMock()
    mock_d.count_documents = AsyncMock(return_value=42)
    mock_d.count_distinct_collections_with_documents = AsyncMock(return_value=3)

    async def _fake_session() -> MagicMock:
        yield MagicMock()

    with (
        patch("app.api.routes.health.QueryRepository", return_value=mock_q),
        patch("app.api.routes.health.DocumentRepository", return_value=mock_d),
    ):
        app.dependency_overrides[get_db_session] = _fake_session
        try:
            response = _client().get("/api/v1/metrics")
        finally:
            app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["queries_today"] == 4
    assert body["data"]["cost_today_usd"] == 1.25
    assert body["data"]["cost_utilisation_pct"] >= 0.0
    assert len(body["data"]["top_queries"]) == 1
    assert body["data"]["top_queries"][0]["question_hash"] == "deadbeef"


def test_metrics_includes_correlation_metadata() -> None:
    """Response metadata carries correlation id for tracing."""
    mock_q = MagicMock()
    mock_q.aggregate_query_metrics_for_interval = AsyncMock(return_value=(0, 0, 0.0, 0.0))
    mock_q.get_daily_cost = AsyncMock(return_value=0.0)
    mock_q.get_top_queries = AsyncMock(return_value=[])
    mock_q.get_refusal_breakdown = AsyncMock(return_value={})
    mock_d = MagicMock()
    mock_d.count_documents = AsyncMock(return_value=0)
    mock_d.count_distinct_collections_with_documents = AsyncMock(return_value=0)

    async def _fake_session() -> MagicMock:
        yield MagicMock()

    with (
        patch("app.api.routes.health.QueryRepository", return_value=mock_q),
        patch("app.api.routes.health.DocumentRepository", return_value=mock_d),
    ):
        app.dependency_overrides[get_db_session] = _fake_session
        try:
            response = _client().get("/api/v1/metrics", headers={"X-Correlation-ID": "fixed-cid"})
        finally:
            app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 200
    assert response.json()["metadata"]["correlation_id"] == "fixed-cid"


def test_metrics_refusal_breakdown_shape() -> None:
    """Refusal breakdown is a string-keyed count map."""
    mock_q = MagicMock()
    mock_q.aggregate_query_metrics_for_interval = AsyncMock(return_value=(1, 1, 10.0, 0.0))
    mock_q.get_daily_cost = AsyncMock(return_value=0.0)
    mock_q.get_top_queries = AsyncMock(return_value=[])
    mock_q.get_refusal_breakdown = AsyncMock(return_value={"guardrail_violation": 1})
    mock_d = MagicMock()
    mock_d.count_documents = AsyncMock(return_value=1)
    mock_d.count_distinct_collections_with_documents = AsyncMock(return_value=1)

    async def _fake_session() -> MagicMock:
        yield MagicMock()

    with (
        patch("app.api.routes.health.QueryRepository", return_value=mock_q),
        patch("app.api.routes.health.DocumentRepository", return_value=mock_d),
    ):
        app.dependency_overrides[get_db_session] = _fake_session
        try:
            response = _client().get("/api/v1/metrics")
        finally:
            app.dependency_overrides.pop(get_db_session, None)

    data = response.json()["data"]
    assert data["refusal_breakdown"]["guardrail_violation"] == 1
