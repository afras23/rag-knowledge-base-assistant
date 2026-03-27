"""
Offline retrieval simulation over eval/sample_docs for metrics without live Chroma.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, cast

from app.services.vectorstore.chroma_client import RetrievedChunk

logger = logging.getLogger(__name__)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "what",
        "when",
        "where",
        "which",
        "there",
        "their",
        "have",
        "been",
        "will",
        "your",
        "into",
        "about",
        "after",
        "before",
        "would",
        "could",
        "other",
        "than",
        "some",
        "such",
        "these",
        "those",
        "them",
        "they",
    },
)


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """
    Load eval/sample_docs/manifest.json.

    Args:
        manifest_path: Path to JSON manifest.

    Returns:
        Parsed JSON object.

    Raises:
        FileNotFoundError: When the file is missing.
        ValueError: When JSON is invalid.
    """
    raw = manifest_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest.json is not valid JSON") from exc
    if not isinstance(data, dict) or "docs" not in data:
        raise ValueError("manifest.json must contain a top-level 'docs' object")
    return cast(dict[str, Any], data)


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    raw = {m.group(0) for m in re.finditer(r"[a-z0-9]{4,}", lowered)}
    return raw - _STOPWORDS


def score_overlap(question: str, chunk_text: str) -> float:
    """
    Jaccard overlap of token sets between question and chunk (cheap proxy for relevance).

    Args:
        question: User question.
        chunk_text: Candidate chunk body.

    Returns:
        Score in [0, 1].
    """
    q = _tokens(question)
    t = _tokens(chunk_text)
    if not q or not t:
        return 0.0
    inter = len(q & t)
    union = len(q | t)
    return float(inter) / float(union) if union else 0.0


def build_chunks_from_sample_docs(
    *,
    sample_dir: Path,
    manifest: dict[str, Any],
) -> list[RetrievedChunk]:
    """
    Read markdown files and synthesize RetrievedChunk rows using manifest metadata.

    Args:
        sample_dir: Directory containing markdown files listed in the manifest.
        manifest: Parsed manifest (``docs`` map filename -> meta).

    Returns:
        Flat list of chunks with synthetic chunk_index and section anchors.
    """
    chunks: list[RetrievedChunk] = []
    docs_meta: dict[str, Any] = manifest["docs"]
    for filename, meta in docs_meta.items():
        path = sample_dir / filename
        body = path.read_text(encoding="utf-8")
        parts = _split_into_parts(body)
        doc_id = str(meta["doc_id"])
        title = str(meta["document_title"])
        collection_id = str(meta.get("collection_id", "default"))
        restriction = str(meta.get("restriction_level", "public"))
        for idx, (section, text) in enumerate(parts):
            chunk = RetrievedChunk(
                text=text.strip(),
                doc_id=doc_id,
                document_title=title,
                page_or_section=section,
                relevance_score=0.5,
                collection_id=collection_id,
                restriction_level=restriction,
                chunk_index=idx,
            )
            chunks.append(chunk)
    logger.info(
        "Built offline chunks from sample docs",
        extra={"chunk_count": len(chunks), "doc_files": len(docs_meta)},
    )
    return chunks


def _split_into_parts(body: str) -> list[tuple[str, str]]:
    """Split markdown body into (section_title, text) segments."""
    lines = body.splitlines()
    parts: list[tuple[str, str]] = []
    current_title = "Introduction"
    buf: list[str] = []
    heading_re = re.compile(r"^#+\s+(.+)$")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if buf:
                parts.append((current_title, "\n".join(buf)))
            current_title = m.group(1).strip()
            buf = []
            continue
        buf.append(line)
    if buf:
        parts.append((current_title, "\n".join(buf)))
    if not parts:
        return [("Full document", body)]
    return parts


def rank_chunks_for_question(
    *,
    question: str,
    chunks: list[RetrievedChunk],
    user_group: str | None,
    max_chunks: int,
) -> list[RetrievedChunk]:
    """
    Filter by access rules, score by token overlap, return top ``max_chunks``.

    Args:
        question: Query text.
        chunks: Candidate corpus chunks.
        user_group: When None, confidential chunks are excluded.
        max_chunks: Result list size cap.

    Returns:
        Sorted chunks with ``relevance_score`` set from overlap.
    """
    filtered: list[RetrievedChunk] = []
    for ch in chunks:
        if user_group is None and ch.restriction_level.lower() == "confidential":
            continue
        filtered.append(ch)

    scored: list[tuple[float, RetrievedChunk]] = []
    for ch in filtered:
        score = score_overlap(question, ch.text)
        updated = ch.model_copy(update={"relevance_score": min(1.0, max(0.0, score))})
        scored.append((score, updated))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [pair[1] for pair in scored[:max_chunks]]
