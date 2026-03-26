"""
Document chunking service for ingestion (Phase 3 — Component 2).

This module consumes ParsedDocument objects and produces section-aware chunks
using a recursive splitting strategy aligned with ADR003.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.config import settings
from app.services.ingestion.parsers import ParsedDocument

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class Chunk(BaseModel):
    """A single text chunk ready for downstream embedding/indexing."""

    text: str = Field(..., description="Chunk text content")
    source_document: str = Field(..., description="Source document filename")
    page_or_section: str = Field(..., description="Anchor inherited from parser section metadata")
    chunk_index: int = Field(..., ge=0, description="0-based chunk index within the whole document")


@dataclass(frozen=True)
class ChunkingConfig:
    """Chunking parameters used by DocumentChunker."""

    chunk_size: int
    chunk_overlap: int


class DocumentChunker:
    """Section-aware recursive character chunker."""

    def __init__(
        self,
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        """
        Initialize chunker with configured sizes.

        Args:
            chunk_size: Maximum characters per chunk.
            chunk_overlap: Character overlap between adjacent chunks.
        """
        configured_chunk_size = int(chunk_size) if chunk_size is not None else settings.chunk_size
        configured_chunk_overlap = int(chunk_overlap) if chunk_overlap is not None else settings.chunk_overlap

        if configured_chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if configured_chunk_overlap < 0:
            raise ValueError("chunk_overlap must be >= 0")
        if configured_chunk_overlap >= configured_chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        self._config = ChunkingConfig(
            chunk_size=configured_chunk_size,
            chunk_overlap=configured_chunk_overlap,
        )

    @property
    def config(self) -> ChunkingConfig:
        """Return active chunking configuration."""
        return self._config

    def chunk_document(self, parsed_document: ParsedDocument) -> list[Chunk]:
        """
        Chunk a parsed document while preserving section anchors.

        Args:
            parsed_document: Parsed document from parsers.py.

        Returns:
            List of chunk objects in deterministic order.
        """
        chunk_list: list[Chunk] = []
        next_chunk_index = 0

        for section in parsed_document.sections:
            normalized_section_text = self._normalize_chunk_text(section.content)
            if not normalized_section_text:
                continue

            section_chunks = self._split_recursive(normalized_section_text)
            for section_chunk_text in section_chunks:
                chunk_list.append(
                    Chunk(
                        text=section_chunk_text,
                        source_document=parsed_document.metadata.filename,
                        page_or_section=section.page_or_section,
                        chunk_index=next_chunk_index,
                    )
                )
                next_chunk_index += 1

        logger.info(
            "Chunked parsed document",
            extra={
                "source_filename": parsed_document.metadata.filename,
                "format": parsed_document.metadata.format,
                "section_count": len(parsed_document.sections),
                "chunk_count": len(chunk_list),
                "chunk_size": self._config.chunk_size,
                "chunk_overlap": self._config.chunk_overlap,
            },
        )
        return chunk_list

    def _split_recursive(self, text: str) -> list[str]:
        """Split text recursively: paragraph -> sentence -> word-aware character fallback."""
        if len(text) <= self._config.chunk_size:
            return [text]

        paragraph_segments = self._split_by_paragraph(text)
        return self._merge_segments_with_overlap(paragraph_segments)

    def _split_by_paragraph(self, text: str) -> list[str]:
        """Split text into paragraph segments; oversize paragraphs fall back to sentence split."""
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        segments: list[str] = []

        for paragraph in paragraphs:
            if len(paragraph) <= self._config.chunk_size:
                segments.append(paragraph)
                continue
            sentence_segments = self._split_by_sentence(paragraph)
            segments.extend(sentence_segments)

        return segments

    def _split_by_sentence(self, text: str) -> list[str]:
        """Split text into sentence segments; oversize sentences fall back to word-safe char split."""
        sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
        segments: list[str] = []

        for sentence in sentences:
            if len(sentence) <= self._config.chunk_size:
                segments.append(sentence)
                continue
            segments.extend(self._split_word_safe(sentence))

        return segments

    def _split_word_safe(self, text: str) -> list[str]:
        """
        Split long text by character windows while avoiding mid-word boundaries.

        Overlap is applied between resulting chunks.
        """
        chunks: list[str] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(start + self._config.chunk_size, text_length)
            if end < text_length and not text[end].isspace():
                backtrack = end
                while backtrack > start and not text[backtrack - 1].isspace():
                    backtrack -= 1
                if backtrack > start:
                    end = backtrack

            piece = text[start:end].strip()
            if piece:
                chunks.append(piece)

            if end >= text_length:
                break

            next_start = max(end - self._config.chunk_overlap, start + 1)
            while next_start < text_length and text[next_start].isspace():
                next_start += 1
            start = next_start

        return chunks

    def _merge_segments_with_overlap(self, segments: list[str]) -> list[str]:
        """Merge normalized segments into bounded chunks and apply overlap."""
        merged_chunks: list[str] = []
        current = ""

        for segment in segments:
            segment_text = self._normalize_chunk_text(segment)
            if not segment_text:
                continue

            if not current:
                current = segment_text
                continue

            candidate = f"{current}\n\n{segment_text}"
            if len(candidate) <= self._config.chunk_size:
                current = candidate
                continue

            merged_chunks.append(current)
            overlap_prefix = current[-self._config.chunk_overlap :] if self._config.chunk_overlap > 0 else ""
            overlap_prefix = self._trim_leading_partial_word(overlap_prefix)
            current = f"{overlap_prefix}\n\n{segment_text}".strip() if overlap_prefix else segment_text

            if len(current) > self._config.chunk_size:
                overflow_chunks = self._split_word_safe(current)
                if overflow_chunks:
                    merged_chunks.extend(overflow_chunks[:-1])
                    current = overflow_chunks[-1]

        if current:
            merged_chunks.append(current)

        return [chunk for chunk in merged_chunks if chunk.strip()]

    @staticmethod
    def _normalize_chunk_text(text: str) -> str:
        """Normalize basic whitespace while preserving paragraph boundaries."""
        stripped = text.strip()
        if not stripped:
            return ""
        collapsed_inline = re.sub(r"[ \t]+", " ", stripped)
        normalized_newlines = collapsed_inline.replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{3,}", "\n\n", normalized_newlines)

    @staticmethod
    def _trim_leading_partial_word(text: str) -> str:
        """
        Trim a likely partial word from overlap prefix start.

        This avoids creating overlap snippets that begin in the middle of a token.
        """
        cleaned = text.lstrip()
        if not cleaned:
            return ""
        if cleaned[0].isalnum():
            first_space = cleaned.find(" ")
            if first_space != -1:
                return cleaned[first_space + 1 :].lstrip()
        return cleaned
