"""
Unit tests for idempotent ingestion (content-hash skip path).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.config import Settings
from app.models.document import Document
from app.services.ingestion.pipeline import IngestionPipeline


@pytest.mark.anyio
async def test_reingest_skips_when_content_hash_exists(tmp_path: Path) -> None:
    """Second ingest of identical content returns skipped without calling the indexer."""
    md_path = tmp_path / "note.md"
    md_path.write_text("# Hello\n\nSame body for hash.\n", encoding="utf-8")

    existing_id = uuid4()
    existing_doc = MagicMock(spec=Document)
    existing_doc.id = existing_id

    ingestion_repo = MagicMock()
    ingestion_repo.create_job = AsyncMock(return_value=MagicMock(id=uuid4()))
    ingestion_repo.update_job_progress = AsyncMock()
    ingestion_repo.log_ingestion_event = AsyncMock()

    document_repo = MagicMock()
    document_repo.find_by_content_hash = AsyncMock(return_value=existing_doc)

    chunker = MagicMock()
    indexer = MagicMock()
    indexer.index_chunks = AsyncMock()

    pipeline = IngestionPipeline(
        ingestion_repo=ingestion_repo,
        document_repo=document_repo,
        chunker=chunker,
        indexer=indexer,
        settings=Settings(),
    )

    result = await pipeline.ingest_documents(
        collection_id="default",
        file_paths=[md_path],
    )

    assert result.skipped == 1
    assert result.processed == 1
    indexer.index_chunks.assert_not_awaited()
