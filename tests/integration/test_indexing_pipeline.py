"""
Integration tests for indexing pipeline (Phase 4).

These tests validate: parse -> chunk -> embed -> index -> query.
External services are mocked (embedding provider and Chroma client).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.collection import Collection
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.services.ingestion.chunker import Chunk, DocumentChunker
from app.services.ingestion.indexer import IndexingService
from app.services.ingestion.pipeline import IngestionPipeline
from app.services.vectorstore.chroma_client import ChromaClientWrapper


class _FakeEmbeddingProvider:
    model_name = "fake-embeddings"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]


class _FakeChromaClient(ChromaClientWrapper):
    def __init__(self) -> None:
        self._store: dict[str, dict[str, object]] = {}

    async def get_or_create_collection(self, name: str) -> object:  # noqa: ARG002
        return SimpleNamespace(delete=lambda where: self._store.clear())  # type: ignore[arg-type]

    async def upsert_chunks(self, *, collection_name: str, chunks: list[object], embeddings: list[list[float]]) -> int:
        for chunk, emb in zip(chunks, embeddings, strict=True):
            key = f"{chunk.doc_id}_{chunk.chunk_index}"
            self._store[key] = {"chunk": chunk, "embedding": emb, "collection": collection_name}
        return len(chunks)

    async def delete_document_chunks(self, *, collection_name: str, doc_id: str) -> int:  # noqa: ARG002
        keys = [k for k in self._store if k.startswith(f"{doc_id}_")]
        for k in keys:
            del self._store[k]
        return len(keys)

    async def query(
        self,
        *,
        collection_name: str,  # noqa: ARG002
        query_embedding: list[float],  # noqa: ARG002
        n_results: int,  # noqa: ARG002
        where_filters: dict[str, object] | None,  # noqa: ARG002
    ) -> list[object]:
        return []

    async def health_check(self) -> bool:
        return True


async def _seed_collection(session: AsyncSession) -> None:
    """Ensure the operations collection exists (idempotent across shared DB state)."""
    if await session.get(Collection, "operations") is not None:
        return
    session.add(
        Collection(
            id="operations",
            name="Operations",
            description="Ops",
            allowed_roles=["consultant"],
        )
    )
    await session.commit()


def _unique_sample_path(tmp_path: Path) -> Path:
    """Copy fixture sample with unique suffix so content_hash differs between tests."""
    source = Path("tests/fixtures/sample_inputs/sample.md").resolve()
    dest = tmp_path / "sample.md"
    dest.write_text(f"{source.read_text(encoding='utf-8')}\n<!-- {uuid4()} -->\n", encoding="utf-8")
    return dest


@pytest.mark.anyio
async def test_ingestion_with_indexing_end_to_end(tmp_path: Path) -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)

            indexer = IndexingService(
                embedding_provider=_FakeEmbeddingProvider(),
                chroma_client=_FakeChromaClient(),
                ingestion_repo=ingestion_repo,
            )
            pipeline = IngestionPipeline(
                ingestion_repo=ingestion_repo,
                document_repo=document_repo,
                chunker=DocumentChunker(chunk_size=200, chunk_overlap=50),
                indexer=indexer,
            )

            sample_path = _unique_sample_path(tmp_path)
            result = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[sample_path],
                restriction_level="restricted",
                created_by=None,
            )
            assert result.failed == 0
            assert result.skipped == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_indexing_failure_doesnt_abort_batch(tmp_path: Path) -> None:
    class _FailChroma(_FakeChromaClient):
        async def upsert_chunks(
            self, *, collection_name: str, chunks: list[object], embeddings: list[list[float]]
        ) -> int:  # noqa: ARG002
            raise RuntimeError("chroma down")

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)

            indexer = IndexingService(
                embedding_provider=_FakeEmbeddingProvider(),
                chroma_client=_FailChroma(),
                ingestion_repo=ingestion_repo,
            )
            pipeline = IngestionPipeline(
                ingestion_repo=ingestion_repo,
                document_repo=document_repo,
                chunker=DocumentChunker(chunk_size=200, chunk_overlap=50),
                indexer=indexer,
            )

            sample_path = _unique_sample_path(tmp_path)
            result = await pipeline.ingest_documents(
                collection_id="operations",
                file_paths=[sample_path],
                restriction_level="restricted",
                created_by=None,
            )
            assert result.failed == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_reindex_replaces_existing_chunks() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await _seed_collection(session)
            ingestion_repo = IngestionRepository(session)
            document_repo = DocumentRepository(session)
            chroma = _FakeChromaClient()
            indexer = IndexingService(
                embedding_provider=_FakeEmbeddingProvider(),
                chroma_client=chroma,
                ingestion_repo=ingestion_repo,
            )

            db_document = await document_repo.create_document(
                title="Reindex test doc",
                file_format="md",
                collection_id="operations",
                restriction_level="restricted",
                content_hash=f"reindex-{uuid4()}",
                version_label=None,
                supersedes_id=None,
                metadata_json={},
                chunk_count=0,
            )
            doc_id = str(db_document.id)
            chunks = [
                Chunk(
                    text="a",
                    source_document="reindex.md",
                    page_or_section="H1",
                    chunk_index=0,
                    doc_id=doc_id,
                    collection_id="operations",
                ),
                Chunk(
                    text="b",
                    source_document="reindex.md",
                    page_or_section="H1",
                    chunk_index=1,
                    doc_id=doc_id,
                    collection_id="operations",
                ),
            ]
            job = await ingestion_repo.create_job(total_documents=1, created_by=None)
            await indexer.index_chunks(chunks=chunks, collection_name="operations", job_id=job.id)
            assert len(chroma._store) == 2

            await indexer.reindex_collection(collection_name="operations")
            assert len(chroma._store) == 0
    finally:
        await engine.dispose()
