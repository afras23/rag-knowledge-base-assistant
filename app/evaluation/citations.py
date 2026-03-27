"""
Citation verification against retrieved chunks (evidence grounding checks).
"""

from __future__ import annotations

from app.api.schemas.chat import CitationSchema
from app.services.vectorstore.chroma_client import RetrievedChunk


def citation_accuracy_ratio(
    citations: list[CitationSchema],
    chunks: list[RetrievedChunk],
) -> tuple[float, list[str]]:
    """
    Verify that each citation references an existing chunk and preview text is grounded.

    A citation passes when a chunk with the same ``doc_id`` exists and
    ``chunk_preview`` appears as a substring of the chunk ``text`` (case-insensitive).

    Args:
        citations: Model citations emitted by the generator.
        chunks: Retrieved chunks available as evidence for this turn.

    Returns:
        Tuple of (accuracy ratio in [0, 1], human-readable issue strings).
    """
    if not citations:
        return (1.0, [])

    by_doc: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.doc_id, []).append(chunk)

    issues: list[str] = []
    passed = 0
    for cite in citations:
        key = str(cite.doc_id)
        if key not in by_doc:
            issues.append(f"doc_id_not_in_chunks:{key}")
            continue
        preview = cite.chunk_preview.strip().lower()
        texts = [c.text.lower() for c in by_doc[key]]
        if preview and any(preview in full for full in texts):
            passed += 1
            continue
        issues.append(f"preview_not_substring:{key}")
    ratio = passed / float(len(citations))
    return (ratio, issues)
