"""
Integration tests for the Phase 3 ingestion pipeline.

Coverage:
- parse -> chunk -> embed -> store (vector storage mocked)
- documents created in DB + chunk_count reflects generated chunks
- embeddings stored (mocked) with alignment to chunks
- idempotency: re-ingesting the same file skips embedding + vector insertion
- failure paths:
  - malformed file parsing fails cleanly (no DB doc created, no vectors inserted)
  - embedding circuit breaker aborts a document ingestion (no partial success)
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.collection import Collection
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import DocumentChunker
from app.services.ingestion.indexer import IndexingService
from app.services.ingestion.parsers import get_parser
from app.services.ingestion.pipeline import IngestionPipeline


def _run_python_module(module_args: list[str]) -> None:
    """Run a Python module with the current interpreter."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    subprocess.run(
        [sys.executable, "-m", *module_args],
        cwd=project_root,
        env=os.environ.copy(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def _drop_enum_types() -> None:
    """Drop PostgreSQL enum types not removed by Alembic downgrades."""
    from sqlalchemy import text as _text

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(_text("DROP TYPE IF EXISTS ingestionjobstatus CASCADE"))
            await connection.execute(_text("DROP TYPE IF EXISTS ingestioneventstatus CASCADE"))
            await connection.execute(_text("DROP TYPE IF EXISTS llmcalltype CASCADE"))
            await connection.execute(_text("DROP TYPE IF EXISTS conversationrole CASCADE"))
    finally:
        await engine.dispose()


async def _reset_schema() -> None:
    """Downgrade to base and upgrade to head using Alembic in a subprocess."""

    def _downgrade() -> None:
        _run_python_module(["alembic", "downgrade", "base"])

    def _upgrade() -> None:
        _run_python_module(["alembic", "upgrade", "head"])

    await anyio.to_thread.run_sync(_downgrade)
    await _drop_enum_types()

    await anyio.to_thread.run_sync(_upgrade)


async def _seed_collection(session: AsyncSession, *, collection_id: str = "operations") -> None:
    """Seed a minimal collection so documents FK constraints are satisfied."""
    collection = Collection(
        id=collection_id,
        name="Operations",
        description="Operations policies and templates",
        allowed_roles=["consultant"],
    )
    session.add(collection)
    await session.commit()


@dataclass(frozen=True)
class _IngestVectorsRecord:
    """Tracking record for FakeChromaClient insertions."""

    inserted_count: int


class _MockEmbeddingsProvider:
    """Mock embedding provider for indexing service."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.embed_texts_calls = 0

    @property
    def model_name(self) -> str:
        return "mock-embeddings"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch (mocked)."""
        self.embed_texts_calls += 1
        if self.should_fail:
            raise RuntimeError("embedding failure")
        return [[float(len(text))] for text in texts]

    def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
        return [1.0]


class _FakeChromaClient:
    """Fake vector storage client used to validate ingestion writes."""

    def __init__(self) -> None:
        self.upsert_calls = 0
        self._records: list[_IngestVectorsRecord] = []

    async def get_or_create_collection(self, name: str) -> object:  # noqa: ARG002
        return object()

    async def upsert_chunks(
        self,
        *,
        collection_name: str,  # noqa: ARG002
        chunks: list[Any],
        embeddings: list[list[float]],
    ) -> int:
        """Mock upsert: record inserted vectors count."""
        self.upsert_calls += 1
        inserted_count = len(embeddings)
        self._records.append(_IngestVectorsRecord(inserted_count=inserted_count))
        return inserted_count

    async def delete_document_chunks(self, *, collection_name: str, doc_id: str) -> int:  # noqa: ARG002
        return 0

    async def query(
        self,
        *,
        collection_name: str,  # noqa: ARG002
        query_embedding: list[float],  # noqa: ARG002
        n_results: int,  # noqa: ARG002
        where_filters: dict[str, object] | None,  # noqa: ARG002
    ) -> list[Any]:
        return []

    async def health_check(self) -> bool:
        return True

    @property
    def records(self) -> list[_IngestVectorsRecord]:
        """Return insertion records."""
        return list(self._records)


@pytest.mark.anyio
async def test_ingestion_pipeline_happy_path_and_idempotency() -> None:
    """Ingest a Markdown document end-to-end and verify skip/idempotency on re-ingest."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sample_md_path = Path("tests/fixtures/sample_inputs/sample.md").resolve()

    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)

            embeddings_provider = _MockEmbeddingsProvider(should_fail=False)
            chunker = DocumentChunker(chunk_size=200, chunk_overlap=50)
            chroma_client = _FakeChromaClient()
            indexer = IndexingService(
                embedding_provider=embeddings_provider,
                chroma_client=chroma_client,  # type: ignore[arg-type]
                ingestion_repo=ingestion_repo,
            )

            pipeline = IngestionPipeline(
                ingestion_repo=ingestion_repo,
                document_repo=document_repo,
                chunker=chunker,
                indexer=indexer,
            )

            first = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[sample_md_path],
                restriction_level="restricted",
                created_by=None,
            )

            assert first.failed == 0
            assert first.skipped == 0
            assert first.processed == 1

            documents, total = await document_repo.list_documents(collection_id="operations", page=1, page_size=20)
            assert total == 1
            assert len(documents) == 1

            db_document = documents[0]

            parsed_document = await get_parser("markdown").parse_path(sample_md_path)
            expected_chunks = chunker.chunk_document(parsed_document)

            assert db_document.chunk_count == len(expected_chunks)

            embedded_metadata = db_document.metadata_json.get("embedding", {})
            assert embedded_metadata in (None, {}) or isinstance(embedded_metadata, dict)

            assert chroma_client.upsert_calls == 1
            assert chroma_client.records[-1].inserted_count == len(expected_chunks)
            assert embeddings_provider.embed_texts_calls >= 1
            embed_calls_after_first = embeddings_provider.embed_texts_calls

            second = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[sample_md_path],
                restriction_level="restricted",
                created_by=None,
            )

            assert second.failed == 0
            assert second.skipped == 1
            assert second.processed == 1

            documents_after, total_after = await document_repo.list_documents(
                collection_id="operations",
                page=1,
                page_size=20,
            )
            assert total_after == 1
            assert len(documents_after) == 1

            # No extra vector inserts or embedding calls after idempotent skip.
            assert chroma_client.upsert_calls == 1
            assert embeddings_provider.embed_texts_calls == embed_calls_after_first

    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_ingestion_pipeline_embedding_failure_circuit_breaks() -> None:
    """Embedding failures should abort the document (no partial success)."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    sample_md_path = Path("tests/fixtures/sample_inputs/sample.md").resolve()

    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)

            embeddings_provider = _MockEmbeddingsProvider(should_fail=True)
            chunker = DocumentChunker(chunk_size=200, chunk_overlap=50)
            chroma_client = _FakeChromaClient()
            indexer = IndexingService(
                embedding_provider=embeddings_provider,
                chroma_client=chroma_client,  # type: ignore[arg-type]
                ingestion_repo=ingestion_repo,
            )

            pipeline = IngestionPipeline(
                ingestion_repo=ingestion_repo,
                document_repo=document_repo,
                chunker=chunker,
                indexer=indexer,
            )

            ingest_result = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[sample_md_path],
                restriction_level="restricted",
                created_by=None,
            )

            assert ingest_result.total_documents == 1
            assert ingest_result.failed == 1
            assert ingest_result.skipped == 0
            assert ingest_result.processed == 1

            documents, total = await document_repo.list_documents(collection_id="operations", page=1, page_size=20)
            assert total == 0
            assert documents == []
            assert chroma_client.upsert_calls == 0
            assert embeddings_provider.embed_texts_calls >= 1

    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_ingestion_pipeline_malformed_file_fails_cleanly() -> None:
    """Malformed inputs should fail without creating DB docs or vectors."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    malformed_path = Path("tests/fixtures/sample_inputs/malformed.pdf").resolve()

    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)

            embeddings_provider = _MockEmbeddingsProvider(should_fail=False)
            chunker = DocumentChunker(chunk_size=200, chunk_overlap=50)
            chroma_client = _FakeChromaClient()
            indexer = IndexingService(
                embedding_provider=embeddings_provider,
                chroma_client=chroma_client,  # type: ignore[arg-type]
                ingestion_repo=ingestion_repo,
            )

            pipeline = IngestionPipeline(
                ingestion_repo=ingestion_repo,
                document_repo=document_repo,
                chunker=chunker,
                indexer=indexer,
            )

            ingest_result = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[malformed_path],
                restriction_level="restricted",
                created_by=None,
            )

            assert ingest_result.total_documents == 1
            assert ingest_result.failed == 1
            assert ingest_result.skipped == 0
            assert ingest_result.processed == 1

            documents, total = await document_repo.list_documents(collection_id="operations", page=1, page_size=20)
            assert total == 0
            assert documents == []

            assert chroma_client.upsert_calls == 0
            # Parser should fail before any embeddings are attempted.
            assert embeddings_provider.embed_texts_calls == 0

    finally:
        await engine.dispose()
