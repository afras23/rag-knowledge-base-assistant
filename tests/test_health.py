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


def test_openapi_schema_and_docs_routes_exist_when_debug() -> None:
    """OpenAPI JSON and Swagger UI are mounted under the API prefix when debug is on."""
    client = TestClient(app)
    openapi_response = client.get("/api/v1/openapi.json")
    assert openapi_response.status_code == 200
    assert "openapi" in openapi_response.json()

    docs_response = client.get("/api/v1/docs")
    assert docs_response.status_code == 200
    assert b"swagger" in docs_response.content.lower()
