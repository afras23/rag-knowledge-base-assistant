"""
Integration tests for admin collection endpoints (Phase 9).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.main import app
from app.models.collection import Collection
from tests.integration.conftest import admin_dependency_overrides


def _client() -> TestClient:
    return TestClient(app)


def test_create_collection() -> None:
    row = Collection(
        id="new_col",
        name="New",
        description="Desc",
        allowed_roles=["admin"],
    )

    mock_repo = MagicMock()
    mock_repo.create_collection = AsyncMock(return_value=row)

    async def fake_collection_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_collection_repo: fake_collection_repo}):
        response = _client().post(
            "/api/v1/admin/collections",
            json={
                "id": "new_col",
                "name": "New",
                "description": "Desc",
                "allowed_roles": ["admin"],
            },
        )
    assert response.status_code == 201
    assert response.json()["data"]["id"] == "new_col"


def test_list_collections() -> None:
    row = Collection(
        id="c1",
        name="One",
        description="D",
        allowed_roles=[],
    )
    mock_repo = MagicMock()
    mock_repo.list_collections = AsyncMock(return_value=([row], 1))

    async def fake_collection_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_collection_repo: fake_collection_repo}):
        response = _client().get("/api/v1/admin/collections?page=1&page_size=5")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1


def test_delete_empty_collection() -> None:
    row = Collection(
        id="empty",
        name="E",
        description="D",
        allowed_roles=[],
    )
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=row)
    mock_repo.count_documents_in_collection = AsyncMock(return_value=0)
    mock_repo.delete_collection_by_id = AsyncMock()

    async def fake_collection_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_collection_repo: fake_collection_repo}):
        response = _client().delete("/api/v1/admin/collections/empty")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "deleted"
    mock_repo.delete_collection_by_id.assert_awaited_once()


def test_delete_nonempty_rejected() -> None:
    row = Collection(
        id="full",
        name="F",
        description="D",
        allowed_roles=[],
    )
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=row)
    mock_repo.count_documents_in_collection = AsyncMock(return_value=3)

    async def fake_collection_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_collection_repo: fake_collection_repo}):
        response = _client().delete("/api/v1/admin/collections/full")
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "COLLECTION_NOT_EMPTY"
