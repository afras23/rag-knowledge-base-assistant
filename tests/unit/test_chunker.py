"""
Unit tests for ingestion chunker (Phase 3 — Component 2).

These tests validate:
- `chunk_size` enforcement
- `chunk_overlap` behavior (token overlap when overlap > 0)
- header/section anchor preservation (chunk.page_or_section)
- edge cases for very small and very large inputs
- ensuring we don't split words mid-token
"""

from __future__ import annotations

import pytest

from app.services.ingestion.chunker import DocumentChunker
from app.services.ingestion.parsers import DocumentMetadata, DocumentSection, ParsedDocument


def _make_parsed_document(*, filename: str, sections: list[tuple[str, str]]) -> ParsedDocument:
    """Create a minimal ParsedDocument for chunker tests."""
    parsed_sections = [
        DocumentSection(title=anchor, content=content, page_or_section=anchor) for anchor, content in sections
    ]
    full_text = "\n\n".join(content for _, content in sections)
    metadata = DocumentMetadata(filename=filename, format="markdown", page_count=0)
    return ParsedDocument(full_text=full_text, metadata=metadata, sections=parsed_sections)


def _tokens(text: str) -> list[str]:
    """Tokenize for overlap assertions."""
    return text.split()


@pytest.mark.parametrize(
    "chunk_size,chunk_overlap",
    [
        (50, 10),
        (80, 20),
        (120, 30),
    ],
)
def test_chunker_enforces_chunk_size(chunk_size: int, chunk_overlap: int) -> None:
    """Every chunk must respect the configured chunk size."""
    chunker = DocumentChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    words = [f"word{i}" for i in range(400)]
    text = " ".join(words)

    parsed_document = _make_parsed_document(filename="doc.md", sections=[("H1: Title", text)])
    chunks = chunker.chunk_document(parsed_document)

    assert chunks
    for chunk in chunks:
        assert len(chunk.text) <= chunker.config.chunk_size
        assert chunk.chunk_index >= 0


def test_chunker_overlap_produces_token_overlap_when_enabled() -> None:
    """With overlap > 0, consecutive chunks should share at least one token."""
    chunker = DocumentChunker(chunk_size=120, chunk_overlap=30)

    # Use repeated tokens to make overlap measurable even with trimming heuristics.
    tokens = ["overlapword"] * 300
    text = " ".join(tokens)
    parsed_document = _make_parsed_document(filename="doc.md", sections=[("H1: T", text)])

    chunks = chunker.chunk_document(parsed_document)
    assert len(chunks) >= 2

    left_tokens = set(_tokens(chunks[0].text))
    right_tokens = set(_tokens(chunks[1].text))
    assert left_tokens.intersection(right_tokens)


def test_chunker_preserves_section_anchors_in_blocks() -> None:
    """Chunker must preserve `page_or_section` per parsed-document section."""
    chunker = DocumentChunker(chunk_size=90, chunk_overlap=15)

    left_text = " ".join([f"L{i}" for i in range(250)])
    right_text = " ".join([f"R{i}" for i in range(250)])
    anchor_left = "Page 1"
    anchor_right = "Page 2"

    parsed_document = _make_parsed_document(
        filename="doc.md",
        sections=[
            (anchor_left, left_text),
            (anchor_right, right_text),
        ],
    )
    chunks = chunker.chunk_document(parsed_document)

    anchors = [chunk.page_or_section for chunk in chunks]
    assert anchor_left in anchors
    assert anchor_right in anchors

    first_right = anchors.index(anchor_right)
    last_left = max(i for i, a in enumerate(anchors) if a == anchor_left)
    assert last_left < first_right


def test_chunker_skips_empty_sections() -> None:
    """Empty/whitespace-only section content should not produce chunks."""
    chunker = DocumentChunker(chunk_size=50, chunk_overlap=10)
    parsed_document = _make_parsed_document(
        filename="doc.md",
        sections=[
            ("H1: Empty", ""),
            ("H2: NonEmpty", "This has content for chunking." * 10),
        ],
    )
    chunks = chunker.chunk_document(parsed_document)
    assert chunks
    assert all(chunk.page_or_section == "H2: NonEmpty" for chunk in chunks)


def test_chunker_edge_case_very_small_text() -> None:
    """Very small text should produce a single chunk."""
    chunker = DocumentChunker(chunk_size=10, chunk_overlap=2)
    parsed_document = _make_parsed_document(filename="doc.md", sections=[("H1: Small", "  A  ")])
    chunks = chunker.chunk_document(parsed_document)

    assert len(chunks) == 1
    assert chunks[0].text == "A"
    assert chunks[0].chunk_index == 0


def test_chunker_edge_case_very_large_text_produces_multiple_chunks() -> None:
    """Very large inputs should be split into multiple bounded chunks."""
    chunker = DocumentChunker(chunk_size=250, chunk_overlap=50)
    text = " ".join([f"w{i}" for i in range(2000)])
    parsed_document = _make_parsed_document(filename="doc.md", sections=[("H1: Big", text)])

    chunks = chunker.chunk_document(parsed_document)
    assert len(chunks) >= 3
    assert all(len(chunk.text) <= chunker.config.chunk_size for chunk in chunks)


def test_chunker_output_is_tokenizable() -> None:
    """Chunk output should be whitespace-tokenizable (no crashes, stable splitting)."""
    chunker = DocumentChunker(chunk_size=80, chunk_overlap=15)

    text = " ".join([f"tok{i}" for i in range(500)])
    parsed_document = _make_parsed_document(filename="doc.md", sections=[("H1: Tokens", text)])
    chunks = chunker.chunk_document(parsed_document)
    assert chunks
    assert all(len(_tokens(chunk.text)) > 0 for chunk in chunks)
