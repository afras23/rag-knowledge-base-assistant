"""
Integration tests for Alembic migrations and database repository CRUD.
"""

from __future__ import annotations

import os
import subprocess
import sys

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.collection import Collection
from app.repositories.document_repo import DocumentRepository
from app.repositories.ingestion_repo import IngestionRepository
from app.repositories.query_repo import QueryRepository


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


async def _reset_schema() -> None:
    """Downgrade to base and upgrade to head using Alembic in a subprocess."""

    def _downgrade() -> None:
        _run_python_module(["alembic", "downgrade", "base"])

    def _upgrade() -> None:
        _run_python_module(["alembic", "upgrade", "head"])

    await anyio.to_thread.run_sync(_downgrade)
    await _drop_enum_types()
    await anyio.to_thread.run_sync(_upgrade)


async def _drop_enum_types() -> None:
    """Drop PostgreSQL enum types not removed by Alembic downgrades."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TYPE IF EXISTS ingestionjobstatus CASCADE"))
            await connection.execute(text("DROP TYPE IF EXISTS ingestioneventstatus CASCADE"))
            await connection.execute(text("DROP TYPE IF EXISTS llmcalltype CASCADE"))
            await connection.execute(text("DROP TYPE IF EXISTS conversationrole CASCADE"))
    finally:
        await engine.dispose()


async def _table_regclass_exists(table_name: str) -> bool:
    """Return whether a table exists in the current schema."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(text("select to_regclass(:name)"), {"name": table_name})
            regclass = result.scalar_one()
            return regclass is not None
    finally:
        await engine.dispose()


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


@pytest.mark.anyio
async def test_alembic_migration_applies_to_clean_database() -> None:
    """Alembic should apply to a clean schema (no existing domain tables)."""
    await anyio.to_thread.run_sync(lambda: _run_python_module(["alembic", "downgrade", "base"]))

    documents_exists = await _table_regclass_exists("documents")
    query_events_exists = await _table_regclass_exists("query_events")
    assert documents_exists is False
    assert query_events_exists is False

    await _drop_enum_types()
    await anyio.to_thread.run_sync(lambda: _run_python_module(["alembic", "upgrade", "head"]))

    documents_exists_after = await _table_regclass_exists("documents")
    query_events_exists_after = await _table_regclass_exists("query_events")
    assert documents_exists_after is True
    assert query_events_exists_after is True


@pytest.mark.anyio
async def test_document_create_and_read() -> None:
    """DocumentRepository should create and read documents."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _seed_collection(session)
        repo = DocumentRepository(session)
        db_document = await repo.create_document(
            title="Travel Policy",
            file_format="pdf",
            collection_id="operations",
            restriction_level="public",
            content_hash="hash-1",
            version_label=None,
            supersedes_id=None,
            metadata_json={},
            chunk_count=0,
        )
        fetched = await repo.get_document(db_document.id)
        assert fetched.title == "Travel Policy"
        assert fetched.content_hash == "hash-1"


@pytest.mark.anyio
async def test_query_event_create_and_read() -> None:
    """QueryRepository should create and read query events."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = QueryRepository(session)
        query_event = await repo.log_query_event(
            question_hash="qhash-1",
            user_group=None,
            collection_ids_searched=["operations"],
            retrieval_strategy="mmr+hybrid",
            chunks_retrieved=3,
            top_relevance_score=0.8,
            confidence=0.75,
            refused=False,
            refusal_reason=None,
            tokens_used=1200,
            cost_usd=0.01,
            latency_ms=250.0,
            prompt_version="prompt_v1",
            model="gpt-4o",
        )
        fetched = await repo.get_query_event(query_event_id=query_event.id)
        assert fetched.question_hash == "qhash-1"
        assert fetched.refused is False


@pytest.mark.anyio
async def test_ingestion_job_create_and_read() -> None:
    """IngestionRepository should create and read ingestion jobs."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = IngestionRepository(session)
        job = await repo.create_job(total_documents=3, created_by="admin")
        fetched = await repo.get_job_status(job_id=job.id)
        assert fetched.total_documents == 3
        assert fetched.status.value == "pending"


@pytest.mark.anyio
async def test_content_hash_uniqueness_constraint() -> None:
    """Document.content_hash must be unique for idempotency."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _seed_collection(session)
        repo = DocumentRepository(session)
        await repo.create_document(
            title="Doc 1",
            file_format="pdf",
            collection_id="operations",
            restriction_level="public",
            content_hash="dup-hash",
            version_label=None,
            supersedes_id=None,
            metadata_json={},
            chunk_count=0,
        )
        with pytest.raises(IntegrityError):
            await repo.create_document(
                title="Doc 2",
                file_format="pdf",
                collection_id="operations",
                restriction_level="public",
                content_hash="dup-hash",
                version_label=None,
                supersedes_id=None,
                metadata_json={},
                chunk_count=0,
            )


@pytest.mark.anyio
async def test_document_supersedes_relationship() -> None:
    """Supersedes relationship should be bidirectional after mark_superseded."""
    await _reset_schema()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _seed_collection(session)
        repo = DocumentRepository(session)
        old_doc = await repo.create_document(
            title="Old Doc",
            file_format="pdf",
            collection_id="operations",
            restriction_level="public",
            content_hash="old-hash",
            version_label=None,
            supersedes_id=None,
            metadata_json={},
            chunk_count=0,
        )
        new_doc = await repo.create_document(
            title="New Doc",
            file_format="pdf",
            collection_id="operations",
            restriction_level="public",
            content_hash="new-hash",
            version_label=None,
            supersedes_id=None,
            metadata_json={},
            chunk_count=0,
        )

        await repo.mark_superseded(superseded_id=old_doc.id, supersedes_id=new_doc.id)

        old_fetched = await repo.get_document(old_doc.id)
        new_fetched = await repo.get_document(new_doc.id)

        assert old_fetched.superseded_by_id == new_doc.id
        assert new_fetched.supersedes_id == old_doc.id
