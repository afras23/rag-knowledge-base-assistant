"""
Integration tests for admin document endpoints (Phase 9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.main import app
from app.models.document import Document
from app.models.ingestion import IngestionJob, IngestionJobStatus
from app.services.ingestion.pipeline import IngestionResult
from tests.integration.conftest import admin_dependency_overrides


def _client() -> TestClient:
    return TestClient(app)


def test_upload_and_index() -> None:
    """POST /admin/documents returns job envelope after ingest."""
    job_id = uuid4()

    class _FakePipeline:
        async def ingest_documents(self, **kwargs: object) -> IngestionResult:
            return IngestionResult(
                job_id=job_id,
                total_documents=1,
                processed=1,
                failed=0,
                skipped=0,
            )

    async def _fake_get_job_status(*, job_id: UUID) -> IngestionJob:
        return IngestionJob(
            id=job_id,
            status=IngestionJobStatus.completed,
            total_documents=1,
            processed=1,
            succeeded=1,
            failed=0,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            created_by="admin",
        )

    async def fake_ingestion_repo() -> MagicMock:
        mock_ing_repo = MagicMock()
        mock_ing_repo.get_job_status = AsyncMock(side_effect=_fake_get_job_status)
        return mock_ing_repo

    with admin_dependency_overrides(
        {
            admin_routes._get_pipeline: lambda: _FakePipeline(),
            admin_routes._get_ingestion_repo: fake_ingestion_repo,
        },
    ):
        response = _client().post(
            "/api/v1/admin/documents",
            files={"file": ("test.md", b"# hello", "text/markdown")},
            data={"collection_id": "operations", "restriction_level": "public"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert str(body["data"]["job_id"]) == str(job_id)


def test_delete_from_db_and_chroma() -> None:
    """DELETE removes DB row and calls Chroma delete."""
    doc_id = uuid4()
    db_doc = MagicMock()
    db_doc.id = doc_id
    db_doc.collection_id = "ops"

    mock_doc_repo = MagicMock()
    mock_doc_repo.get_document = AsyncMock(return_value=db_doc)
    mock_doc_repo.delete_document = AsyncMock()

    mock_chroma = MagicMock()
    mock_chroma.delete_document = AsyncMock(return_value=3)

    async def fake_document_repo() -> MagicMock:
        return mock_doc_repo

    def fake_chroma_client() -> MagicMock:
        return mock_chroma

    with admin_dependency_overrides(
        {
            admin_routes._get_document_repo: fake_document_repo,
            admin_routes._get_chroma_client: fake_chroma_client,
        },
    ):
        response = _client().delete(f"/api/v1/admin/documents/{doc_id}")
    assert response.status_code == 200
    assert response.json()["data"]["deleted_vectors"] == 3
    mock_doc_repo.delete_document.assert_awaited_once()


def test_list_paginated() -> None:
    """GET /admin/documents returns PaginatedResponse."""
    d = MagicMock(spec=Document)
    d.id = uuid4()
    d.title = "T"
    d.file_format = "pdf"
    d.collection_id = "c1"
    d.restriction_level = "public"
    d.version_label = None
    d.supersedes_id = None
    d.superseded_by_id = None
    d.chunk_count = 1
    d.created_at = datetime.now(timezone.utc)
    d.updated_at = datetime.now(timezone.utc)

    mock_repo = MagicMock()
    mock_repo.list_documents = AsyncMock(return_value=([d], 1))

    async def fake_document_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_document_repo: fake_document_repo}):
        response = _client().get("/api/v1/admin/documents?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert len(data["items"]) == 1


def test_supersede() -> None:
    """POST supersede updates DB and Chroma metadata."""
    old_id = uuid4()
    new_id = uuid4()

    old_doc = MagicMock(spec=Document)
    old_doc.id = old_id
    old_doc.collection_id = "ops"
    old_doc.title = "Old"
    old_doc.file_format = "pdf"
    old_doc.restriction_level = "public"
    old_doc.version_label = None
    old_doc.supersedes_id = None
    old_doc.superseded_by_id = new_id
    old_doc.chunk_count = 1
    old_doc.created_at = datetime.now(timezone.utc)
    old_doc.updated_at = datetime.now(timezone.utc)
    new_doc = MagicMock(spec=Document)
    new_doc.id = new_id
    new_doc.collection_id = "ops"

    mock_repo = MagicMock()
    mock_repo.get_document = AsyncMock(side_effect=[old_doc, new_doc, old_doc])
    mock_repo.mark_superseded = AsyncMock()

    mock_chroma = MagicMock()
    mock_chroma.update_document_superseded_metadata = AsyncMock(return_value=2)

    async def fake_document_repo() -> MagicMock:
        return mock_repo

    def fake_chroma_wrapper() -> MagicMock:
        return mock_chroma

    with admin_dependency_overrides(
        {
            admin_routes._get_document_repo: fake_document_repo,
            admin_routes._get_chroma_wrapper: fake_chroma_wrapper,
        },
    ):
        response = _client().post(
            f"/api/v1/admin/documents/{old_id}/supersede",
            json={"new_document_id": str(new_id)},
        )
    assert response.status_code == 200
    mock_repo.mark_superseded.assert_awaited_once()
    mock_chroma.update_document_superseded_metadata.assert_awaited_once()


def test_superseded_excluded_from_list() -> None:
    """list_documents is called with include_superseded=False by default."""
    mock_repo = MagicMock()
    mock_repo.list_documents = AsyncMock(return_value=([], 0))

    async def fake_document_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_document_repo: fake_document_repo}):
        _client().get("/api/v1/admin/documents")
    mock_repo.list_documents.assert_awaited_once()
    assert mock_repo.list_documents.await_args is not None
    assert mock_repo.list_documents.await_args.kwargs.get("include_superseded") is False


def test_view_superseded_with_flag() -> None:
    """include_superseded=true is passed to repository."""
    mock_repo = MagicMock()
    mock_repo.list_documents = AsyncMock(return_value=([], 0))

    async def fake_document_repo() -> MagicMock:
        return mock_repo

    with admin_dependency_overrides({admin_routes._get_document_repo: fake_document_repo}):
        _client().get("/api/v1/admin/documents?include_superseded=true")
    assert mock_repo.list_documents.await_args is not None
    assert mock_repo.list_documents.await_args.kwargs.get("include_superseded") is True
