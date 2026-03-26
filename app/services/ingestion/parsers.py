"""
Document parsers for ingestion (Phase 3 — Component 1).

This module provides format-specific parsers (PDF, DOCX, Markdown) that return a
structured :class:`ParsedDocument` suitable for downstream header-aware chunking.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

import anyio
from pydantic import BaseModel, Field

from app.core.exceptions import ExtractionError

logger = logging.getLogger(__name__)


SupportedFormat = Literal["pdf", "docx", "markdown"]


class DocumentMetadata(BaseModel):
    """Minimal metadata returned by parsers."""

    filename: str = Field(..., description="Source filename (no directory path)")
    format: SupportedFormat = Field(..., description="Source file format")
    page_count: int = Field(..., ge=0, description="Page count for PDFs; 0 for non-paginated formats")


class DocumentSection(BaseModel):
    """A logical section of a document used for header-aware splitting."""

    title: str = Field(..., description="Section title (heading text or page label)")
    content: str = Field(..., description="Section content text")
    page_or_section: str = Field(..., description="Anchor reference (e.g., 'Page 3', 'H2: Security')")


class ParsedDocument(BaseModel):
    """Parsed document containing full text, metadata, and structured sections."""

    full_text: str = Field(..., description="Full extracted text for the document")
    metadata: DocumentMetadata = Field(..., description="Parser-extracted metadata")
    sections: list[DocumentSection] = Field(..., description="Structured sections for header-aware splitting")


class DocumentParser(ABC):
    """Abstract parser interface for supported document formats."""

    @abstractmethod
    async def parse_path(self, source_path: Path) -> ParsedDocument:
        """
        Parse a document from a filesystem path.

        Args:
            source_path: Path to the source file.

        Returns:
            ParsedDocument containing text and sections.

        Raises:
            ExtractionError: If parsing fails or content is empty/unusable.
        """


_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_PDF_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\n(\w)")


def _normalize_text(raw_text: str) -> str:
    """
    Normalize extracted text for downstream processing.

    - Collapse repeated spaces/tabs
    - Normalize newlines
    - Strip leading/trailing whitespace
    """
    if not raw_text.strip():
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", raw_text)
    normalized_newlines = collapsed.replace("\r\n", "\n").replace("\r", "\n")
    compact = _MULTI_NEWLINE_RE.sub("\n\n", normalized_newlines)
    return compact.strip()


def _normalize_pdf_text(raw_text: str) -> str:
    """Normalize PDF-extracted text, including dehyphenation at line breaks."""
    if not raw_text.strip():
        return ""
    dehyphenated = _PDF_HYPHEN_LINEBREAK_RE.sub(r"\1\2", raw_text)
    return _normalize_text(dehyphenated)


def _safe_filename(source_path: Path) -> str:
    """Return a safe filename for logging/metadata."""
    return source_path.name


class MarkdownParser(DocumentParser):
    """Parser for Markdown files, splitting by `#` headings."""

    async def parse_path(self, source_path: Path) -> ParsedDocument:
        start_time = time.perf_counter()
        filename = _safe_filename(source_path)

        def _read_text() -> str:
            return source_path.read_text(encoding="utf-8")

        try:
            raw_text = await anyio.to_thread.run_sync(_read_text)
        except OSError as exc:
            raise ExtractionError(
                "Failed to read markdown file",
                context={"filename": filename, "format": "markdown", "error": str(exc)},
            ) from exc

        normalized = _normalize_text(raw_text)
        if not normalized:
            raise ExtractionError(
                "Markdown file is empty after normalization",
                context={"filename": filename, "format": "markdown"},
            )

        sections = _split_markdown_into_sections(normalized)
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "Parsed markdown document",
            extra={
                "filename": filename,
                "format": "markdown",
                "page_count": 0,
                "section_count": len(sections),
                "duration_ms": duration_ms,
            },
        )
        return ParsedDocument(
            full_text=normalized,
            metadata=DocumentMetadata(filename=filename, format="markdown", page_count=0),
            sections=sections,
        )


def _split_markdown_into_sections(markdown_text: str) -> list[DocumentSection]:
    """
    Split Markdown into sections using ATX headings (#, ##, ###...).

    If no headings exist, returns a single section.
    """
    lines = markdown_text.split("\n")
    sections: list[DocumentSection] = []
    current_title = "Document"
    current_anchor = "Document"
    current_buffer: list[str] = []

    def _flush() -> None:
        content = _normalize_text("\n".join(current_buffer))
        if content:
            sections.append(
                DocumentSection(
                    title=current_title,
                    content=content,
                    page_or_section=current_anchor,
                )
            )

    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    for line in lines:
        match = heading_re.match(line)
        if match:
            _flush()
            hashes, heading_text = match.group(1), match.group(2).strip()
            level = len(hashes)
            current_title = heading_text or f"Heading L{level}"
            current_anchor = f"H{level}: {current_title}"
            current_buffer = []
            continue
        current_buffer.append(line)

    _flush()
    if not sections:
        content = _normalize_text(markdown_text)
        if not content:
            return []
        return [
            DocumentSection(
                title="Document",
                content=content,
                page_or_section="Document",
            )
        ]
    return sections


class PdfParser(DocumentParser):
    """Parser for PDF files using pdfplumber, extracting text per page with anchors."""

    async def parse_path(self, source_path: Path) -> ParsedDocument:
        start_time = time.perf_counter()
        filename = _safe_filename(source_path)

        try:
            pdfplumber = __import__("pdfplumber")
        except ModuleNotFoundError as exc:
            raise ExtractionError(
                "pdfplumber is not installed",
                context={"filename": filename, "format": "pdf"},
            ) from exc

        def _parse_sync() -> tuple[list[DocumentSection], int, str]:
            sections: list[DocumentSection] = []
            page_count = 0
            full_text_parts: list[str] = []

            with pdfplumber.open(str(source_path)) as pdf:
                page_count = len(pdf.pages)
                for idx, page in enumerate(pdf.pages, start=1):
                    try:
                        page_text_raw = page.extract_text() or ""
                    except Exception as page_exc:  # noqa: BLE001
                        logger.warning(
                            "PDF page extraction failed; skipping page",
                            extra={"filename": filename, "format": "pdf", "page_number": idx, "error": str(page_exc)},
                        )
                        continue

                    page_text = _normalize_pdf_text(page_text_raw)
                    if not page_text:
                        continue

                    anchor = f"Page {idx}"
                    sections.append(
                        DocumentSection(
                            title=anchor,
                            content=page_text,
                            page_or_section=anchor,
                        )
                    )
                    full_text_parts.append(page_text)

            full_text = _normalize_text("\n\n".join(full_text_parts))
            return sections, page_count, full_text

        try:
            sections, page_count, full_text = await anyio.to_thread.run_sync(_parse_sync)
        except ExtractionError:
            raise
        except OSError as exc:
            raise ExtractionError(
                "Failed to read PDF file",
                context={"filename": filename, "format": "pdf", "error": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(
                "Failed to parse PDF",
                context={"filename": filename, "format": "pdf", "error": str(exc)},
            ) from exc

        if not full_text:
            raise ExtractionError(
                "PDF contains no extractable text",
                context={"filename": filename, "format": "pdf", "page_count": page_count},
            )

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "Parsed PDF document",
            extra={
                "filename": filename,
                "format": "pdf",
                "page_count": page_count,
                "section_count": len(sections),
                "duration_ms": duration_ms,
            },
        )

        return ParsedDocument(
            full_text=full_text,
            metadata=DocumentMetadata(filename=filename, format="pdf", page_count=page_count),
            sections=sections,
        )


class DocxParser(DocumentParser):
    """Parser for DOCX files using python-docx, extracting headings as section boundaries."""

    async def parse_path(self, source_path: Path) -> ParsedDocument:
        start_time = time.perf_counter()
        filename = _safe_filename(source_path)

        try:
            docx_module = __import__("docx")
        except ModuleNotFoundError as exc:
            raise ExtractionError(
                "python-docx is not installed",
                context={"filename": filename, "format": "docx"},
            ) from exc

        def _parse_sync() -> tuple[list[DocumentSection], str]:
            doc = docx_module.Document(str(source_path))
            paragraphs = list(doc.paragraphs)

            sections: list[DocumentSection] = []
            current_title = "Document"
            current_anchor = "Document"
            current_buffer: list[str] = []

            def _is_heading(paragraph: Any) -> tuple[bool, int]:
                style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
                match = re.match(r"^Heading\s+(\d+)$", style_name.strip(), re.IGNORECASE)
                if not match:
                    return False, 0
                return True, int(match.group(1))

            def _flush() -> None:
                content = _normalize_text("\n".join(current_buffer))
                if content:
                    sections.append(
                        DocumentSection(
                            title=current_title,
                            content=content,
                            page_or_section=current_anchor,
                        )
                    )

            for paragraph in paragraphs:
                text = (getattr(paragraph, "text", "") or "").strip()
                is_heading, heading_level = _is_heading(paragraph)
                if is_heading:
                    _flush()
                    current_title = text or f"Heading L{heading_level}"
                    current_anchor = f"H{heading_level}: {current_title}"
                    current_buffer = []
                    continue
                if text:
                    current_buffer.append(text)

            _flush()
            full_text = _normalize_text("\n\n".join(section.content for section in sections))

            if not sections:
                fallback_full = _normalize_text("\n".join((getattr(p, "text", "") or "") for p in paragraphs))
                if fallback_full:
                    return (
                        [
                            DocumentSection(
                                title="Document",
                                content=fallback_full,
                                page_or_section="Document",
                            )
                        ],
                        fallback_full,
                    )
                return [], ""

            return sections, full_text

        try:
            sections, full_text = await anyio.to_thread.run_sync(_parse_sync)
        except OSError as exc:
            raise ExtractionError(
                "Failed to read DOCX file",
                context={"filename": filename, "format": "docx", "error": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(
                "Failed to parse DOCX",
                context={"filename": filename, "format": "docx", "error": str(exc)},
            ) from exc

        if not full_text:
            raise ExtractionError(
                "DOCX contains no extractable text",
                context={"filename": filename, "format": "docx"},
            )

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "Parsed DOCX document",
            extra={
                "filename": filename,
                "format": "docx",
                "page_count": 0,
                "section_count": len(sections),
                "duration_ms": duration_ms,
            },
        )

        return ParsedDocument(
            full_text=full_text,
            metadata=DocumentMetadata(filename=filename, format="docx", page_count=0),
            sections=sections,
        )


def get_parser(file_format: str) -> DocumentParser:
    """
    Factory for document parsers.

    Args:
        file_format: Expected values: pdf, docx, markdown.

    Returns:
        A parser instance for the requested format.

    Raises:
        ExtractionError: If the format is unsupported.
    """
    normalized = file_format.strip().lower()
    if normalized in {"md", "markdown"}:
        return MarkdownParser()
    if normalized == "pdf":
        return PdfParser()
    if normalized == "docx":
        return DocxParser()
    raise ExtractionError(
        "Unsupported document format",
        context={"format": file_format},
    )
