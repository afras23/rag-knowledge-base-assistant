"""
Integration tests for admin reindex endpoints (Phase 9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.main import app
from app.models.ingestion import IngestionJob, IngestionJobStatus
from tests.integration.conftest import admin_dependency_overrides


class _FakeReindexService:
    """Minimal stand-in for ReindexService (no Chroma)."""

    async def reindex_document(self, document_id: UUID, job_id: UUID) -> None:
        """No-op reindex for route wiring tests."""
        return None


def _client() -> TestClient:
    return TestClient(app)


def _ingestion_repo_mock(job_id: UUID, *, total_documents: int = 1) -> MagicMock:
    job = IngestionJob(
        id=job_id,
        status=IngestionJobStatus.completed,
        total_documents=total_documents,
        processed=total_documents,
        succeeded=total_documents,
        failed=0,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_by="admin",
    )
    mock_ing = MagicMock()
    mock_ing.create_job = AsyncMock(return_value=job)
    mock_ing.update_job_progress = AsyncMock()
    mock_ing.get_job_status = AsyncMock(return_value=job)
    return mock_ing


def test_reindex_document() -> None:
    job_id = uuid4()

    async def fake_ingestion_repo() -> MagicMock:
        return _ingestion_repo_mock(job_id, total_documents=1)

    async def fake_reindex_service() -> _FakeReindexService:
        return _FakeReindexService()

    with admin_dependency_overrides(
        {
            admin_routes._get_ingestion_repo: fake_ingestion_repo,
            admin_routes._get_reindex_service: fake_reindex_service,
        },
    ):
        doc_id = uuid4()
        response = _client().post(f"/api/v1/admin/reindex/document/{doc_id}")
    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == str(job_id)


def test_reindex_collection() -> None:
    job_id = uuid4()

    async def fake_ingestion_repo() -> MagicMock:
        return _ingestion_repo_mock(job_id, total_documents=1)

    async def fake_reindex_service() -> _FakeReindexService:
        return _FakeReindexService()

    mock_doc = MagicMock()
    mock_doc.list_document_ids_for_collection = AsyncMock(return_value=[uuid4()])

    async def fake_document_repo() -> MagicMock:
        return mock_doc

    with admin_dependency_overrides(
        {
            admin_routes._get_ingestion_repo: fake_ingestion_repo,
            admin_routes._get_reindex_service: fake_reindex_service,
            admin_routes._get_document_repo: fake_document_repo,
        },
    ):
        response = _client().post("/api/v1/admin/reindex/collection/operations")
    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == str(job_id)


def test_reindex_tracks_progress() -> None:
    """Job status endpoint reflects counters after reindex job creation."""
    job_id = uuid4()

    async def fake_ingestion_repo() -> MagicMock:
        return _ingestion_repo_mock(job_id, total_documents=1)

    async def fake_reindex_service() -> _FakeReindexService:
        return _FakeReindexService()

    mock_doc = MagicMock()
    mock_doc.list_all_document_ids = AsyncMock(return_value=[uuid4()])

    async def fake_document_repo() -> MagicMock:
        return mock_doc

    with admin_dependency_overrides(
        {
            admin_routes._get_ingestion_repo: fake_ingestion_repo,
            admin_routes._get_reindex_service: fake_reindex_service,
            admin_routes._get_document_repo: fake_document_repo,
        },
    ):
        client = _client()
        post = client.post("/api/v1/admin/reindex/all")
        assert post.status_code == 202
        jid = post.json()["data"]["job_id"]

        get = client.get(f"/api/v1/admin/ingest/{jid}")
    assert get.status_code == 200
    assert get.json()["data"]["processed"] == 1
