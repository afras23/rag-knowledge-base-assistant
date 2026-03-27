"""
Parse and resolve LLM [Source: ...] citations against retrieved chunks.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from app.api.schemas.chat import CitationSchema
from app.services.vectorstore.chroma_client import RetrievedChunk

logger = logging.getLogger(__name__)

SOURCE_REF_RE = re.compile(r"\[Source:\s*([^,\]]+),\s*([^\]]+)\]")

CHUNK_PREVIEW_MAX_LEN = 200


def extract_source_references(answer: str) -> list[tuple[str, str]]:
    """
    Extract (title, section) pairs from bracketed source references.

    Args:
        answer: Model answer text.

    Returns:
        Parsed title and section strings per reference.
    """
    matches: list[tuple[str, str]] = []
    for match in SOURCE_REF_RE.finditer(answer):
        title = match.group(1).strip()
        section = match.group(2).strip()
        matches.append((title, section))
    return matches


def _normalize_title(value: str) -> str:
    return " ".join(value.lower().split())


def _find_chunk_for_title(title: str, chunks: list[RetrievedChunk]) -> RetrievedChunk | None:
    target = _normalize_title(title)
    for chunk in chunks:
        if _normalize_title(chunk.document_title) == target:
            return chunk
    for chunk in chunks:
        if target in _normalize_title(chunk.document_title):
            return chunk
    return None


def build_citations_from_answer(
    answer: str,
    chunks: list[RetrievedChunk],
) -> tuple[list[CitationSchema], list[str]]:
    """
    Match parsed references to chunks and build citation records.

    Args:
        answer: Model answer containing optional [Source: title, section] tags.
        chunks: Retrieved chunks available for matching.

    Returns:
        Structured citations and a list of unmatched raw title strings (for logging).
    """
    refs = extract_source_references(answer)
    citations: list[CitationSchema] = []
    unmatched: list[str] = []
    for title, section in refs:
        chunk = _find_chunk_for_title(title, chunks)
        if chunk is None:
            unmatched.append(title)
            logger.warning(
                "Citation reference could not be matched to a chunk",
                extra={"document_title_ref": title, "section_ref": section},
            )
            continue
        preview = chunk.text[:CHUNK_PREVIEW_MAX_LEN]
        try:
            doc_uuid = UUID(chunk.doc_id)
        except ValueError:
            unmatched.append(title)
            logger.warning(
                "Chunk doc_id is not a valid UUID for citation",
                extra={"doc_id": chunk.doc_id},
            )
            continue
        citations.append(
            CitationSchema(
                document_title=chunk.document_title,
                doc_id=doc_uuid,
                page_or_section=section or chunk.page_or_section,
                relevance_score=chunk.relevance_score,
                chunk_preview=preview,
            )
        )
    return citations, unmatched
