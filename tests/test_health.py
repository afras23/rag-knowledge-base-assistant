"""
Tests for health endpoints.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok() -> None:
    """`/api/v1/health` should return HTTP 200 and a success envelope."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["status"] == "healthy"
    assert "timestamp" in payload["data"]
    assert "correlation_id" in payload["metadata"]
