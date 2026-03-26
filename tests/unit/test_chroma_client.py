"""
Unit tests for Chroma client wrapper (Phase 4).
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.services.ingestion.chunker import Chunk
from app.services.vectorstore.chroma_client import ChromaClientWrapper


class _FakeCollection:
    def __init__(self) -> None:
        self._store: dict[str, tuple[str, dict[str, object], list[float]]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, object]],
        embeddings: list[list[float]],
    ) -> None:
        for identifier, doc, meta, emb in zip(ids, documents, metadatas, embeddings, strict=True):
            self._store[identifier] = (doc, meta, emb)

    def get(self, *, where: dict[str, object], include: list[str]) -> dict[str, object]:  # noqa: ARG002
        doc_id = where.get("doc_id")
        ids = [key for key, (_, meta, _) in self._store.items() if meta.get("doc_id") == doc_id]
        return {"ids": ids}

    def delete(self, *, where: dict[str, object]) -> None:
        doc_id = where.get("doc_id")
        to_delete = [key for key, (_, meta, _) in self._store.items() if meta.get("doc_id") == doc_id]
        for key in to_delete:
            self._store.pop(key, None)

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, object] | None,
        include: list[str],
    ) -> dict[str, object]:
        _ = (query_embeddings, include)
        items = list(self._store.items())
        if where:
            for k, v in where.items():
                items = [(i, rec) for i, rec in items if rec[1].get(k) == v]
        items = items[:n_results]
        documents = [[rec[0] for _, rec in items]]
        metadatas = [[rec[1] for _, rec in items]]
        distances = [[0.1 for _ in items]]
        return {"documents": documents, "metadatas": metadatas, "distances": distances}


class _FakeHttpClient:
    def __init__(self, host: str, port: int) -> None:  # noqa: ARG002
        self._collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, name: str) -> _FakeCollection:
        if name not in self._collections:
            self._collections[name] = _FakeCollection()
        return self._collections[name]

    def list_collections(self) -> list[str]:
        return list(self._collections.keys())


def _install_fake_chromadb(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("chromadb")
    fake_module.HttpClient = _FakeHttpClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chromadb", fake_module)


@pytest.mark.anyio
async def test_upsert_chunks_stores_with_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    chunks = [
        Chunk(
            text="hello",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=0,
            doc_id="00000000-0000-0000-0000-000000000000",
            document_title="Doc",
            collection_id="operations",
            restriction_level="restricted",
            version_label=None,
        )
    ]
    inserted = await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1, 0.2]])
    assert inserted == 1


@pytest.mark.anyio
async def test_upsert_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    chunks = [
        Chunk(
            text="hello",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=0,
            doc_id="00000000-0000-0000-0000-000000000000",
        )
    ]
    await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1, 0.2]])
    inserted = await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1, 0.2]])
    assert inserted == 1


@pytest.mark.anyio
async def test_delete_document_removes_all_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    chunks = [
        Chunk(
            text="hello",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=0,
            doc_id="00000000-0000-0000-0000-000000000000",
        ),
        Chunk(
            text="world",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=1,
            doc_id="00000000-0000-0000-0000-000000000000",
        ),
    ]
    await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1], [0.2]])
    deleted = await client.delete_document_chunks(
        collection_name="operations", doc_id="00000000-0000-0000-0000-000000000000"
    )
    assert deleted == 2


@pytest.mark.anyio
async def test_query_returns_scored_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    chunks = [
        Chunk(
            text="hello",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=0,
            doc_id="00000000-0000-0000-0000-000000000000",
        )
    ]
    await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1]])
    results = await client.query(collection_name="operations", query_embedding=[0.1], n_results=5, where_filters=None)
    assert results
    assert 0.0 <= results[0].relevance_score <= 1.0


@pytest.mark.anyio
async def test_query_with_collection_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    chunks = [
        Chunk(
            text="hello",
            source_document="doc.md",
            page_or_section="H1",
            chunk_index=0,
            doc_id="00000000-0000-0000-0000-000000000000",
            collection_id="operations",
        )
    ]
    await client.upsert_chunks(collection_name="operations", chunks=chunks, embeddings=[[0.1]])
    results = await client.query(
        collection_name="operations",
        query_embedding=[0.1],
        n_results=5,
        where_filters={"collection_id": "operations"},
    )
    assert results


@pytest.mark.anyio
async def test_health_check_returns_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_chromadb(monkeypatch)
    client = ChromaClientWrapper(chroma_host="x", chroma_port=1)
    ok = await client.health_check()
    assert ok is True
