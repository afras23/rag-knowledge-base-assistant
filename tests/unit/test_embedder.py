"""
Unit tests for embedding providers and factory (Phase 4).
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.core.exceptions import IngestionError
from app.services.ingestion.chunker import Chunk
from app.services.ingestion.embedder import (
    DocumentEmbedder,
    LocalBgeSmallEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)


class _FakeSentenceTransformer:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:  # noqa: ARG002
        dim = 3
        return [[float(len(text))] * dim for text in texts]


def _install_fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("sentence_transformers")

    def _sentence_transformer(model_name: str) -> _FakeSentenceTransformer:
        return _FakeSentenceTransformer(model_name)

    fake_module.SentenceTransformer = _sentence_transformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)


class _FakeOpenAIEmbeddings:
    def __init__(self, model: str, api_key: str) -> None:  # noqa: ARG002
        self.model = model

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 2.0, 3.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
        return [1.0, 2.0, 3.0]


def _install_fake_langchain_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("langchain_openai")
    fake_module.OpenAIEmbeddings = _FakeOpenAIEmbeddings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)


@pytest.mark.anyio
async def test_local_provider_returns_correct_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    provider = LocalBgeSmallEmbeddingProvider()
    vectors = await provider.embed_texts(["a", "bb"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 3


@pytest.mark.anyio
async def test_openai_provider_tracks_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_openai(monkeypatch)
    provider = OpenAIEmbeddingProvider(api_key="test-key")
    vectors = await provider.embed_texts(["hello world"])
    assert vectors and len(vectors[0]) == 3
    assert provider.last_batch_telemetry is not None
    assert provider.last_batch_telemetry.cost_usd >= 0.0
    assert provider.last_batch_telemetry.input_tokens >= 1


def test_factory_returns_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    monkeypatch.setattr("app.config.settings.embedding_provider", "local", raising=False)
    provider = get_embedding_provider()
    assert provider.model_name


def test_factory_returns_openai_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_openai(monkeypatch)
    monkeypatch.setattr("app.config.settings.embedding_provider", "openai", raising=False)
    monkeypatch.setattr("app.config.settings.openai_api_key", "test-key", raising=False)
    provider = get_embedding_provider()
    assert provider.model_name


@pytest.mark.anyio
async def test_embed_query_returns_single_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sentence_transformers(monkeypatch)
    provider = LocalBgeSmallEmbeddingProvider()
    vector = provider.embed_query("hello")
    assert len(vector) == 3


def test_openai_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_langchain_openai(monkeypatch)
    with pytest.raises(IngestionError):
        OpenAIEmbeddingProvider(api_key="")


@pytest.mark.anyio
async def test_batch_embedding_respects_batch_size() -> None:
    class _CountingProvider:
        model_name = "counting"

        def __init__(self) -> None:
            self.calls = 0

        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return [[0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:  # noqa: ARG002
            return [0.0]

    provider = _CountingProvider()
    embedder = DocumentEmbedder(
        embedding_provider=provider,
        batch_size=2,
        max_retries=1,
        initial_backoff_seconds=0.0,
        circuit_breaker_threshold=3,
    )
    chunks = [Chunk(text=f"c{i}", source_document="d", page_or_section="s", chunk_index=i) for i in range(5)]
    embeddings, _ = await embedder.embed_chunks(chunks)
    assert len(embeddings) == 5
    assert provider.calls == 3
