"""
Unit tests for document parsers (Phase 3 — Component 1).

These tests validate:
- Markdown section splitting and heading anchors
- PDF page extraction + Page anchors (mocked pdfplumber)
- DOCX heading extraction (mocked python-docx)
- Malformed/empty inputs raise ExtractionError
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.exceptions import ExtractionError
from app.services.ingestion.parsers import (
    ParsedDocument,
    get_parser,
)


class _FakePdfPage:
    def __init__(self, text: str) -> None:
        """Create a fake PDF page."""
        self._text = text

    def extract_text(self) -> str:
        """Return page text for the parser."""
        return self._text


class _FakePdf:
    def __init__(self, pages: list[_FakePdfPage]) -> None:
        """Create a fake PDF object with a pages list."""
        self.pages = pages


class _FakePdfOpenContext:
    def __init__(self, pdf: _FakePdf) -> None:
        """Create a context manager returning the fake PDF."""
        self._pdf = pdf

    def __enter__(self) -> _FakePdf:
        """Enter the context manager."""
        return self._pdf

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any | None,
    ) -> None:
        """Exit the context manager."""


def _install_fake_pdfplumber(monkeypatch: pytest.MonkeyPatch, *, pages: list[str]) -> None:
    """Install a fake `pdfplumber` module into `sys.modules`."""

    fake_pdfplumber = ModuleType("pdfplumber")

    def _open(_: str) -> _FakePdfOpenContext:
        fake_pages = [_FakePdfPage(text) for text in pages]
        return _FakePdfOpenContext(_FakePdf(pages=fake_pages))

    fake_pdfplumber.open = _open  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)


def _install_fake_pdfplumber_failure(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Install a fake `pdfplumber` module whose `open` always raises."""

    fake_pdfplumber = ModuleType("pdfplumber")

    def _open(_: str) -> None:
        raise exc

    fake_pdfplumber.open = _open  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdfplumber", fake_pdfplumber)


class _FakeDocxStyle:
    def __init__(self, name: str) -> None:
        """Create fake style metadata."""
        self.name = name


class _FakeDocxParagraph:
    def __init__(self, text: str, style_name: str | None) -> None:
        """Create a fake docx paragraph with optional heading style."""
        self.text = text
        self.style = _FakeDocxStyle(style_name) if style_name is not None else _FakeDocxStyle("")


class _FakeDocxDocument:
    def __init__(self, paragraphs: list[_FakeDocxParagraph]) -> None:
        """Create a fake docx document."""
        self.paragraphs = paragraphs


def _install_fake_docx(monkeypatch: pytest.MonkeyPatch, *, paragraphs: list[tuple[str, str | None]]) -> None:
    """Install a fake `docx` module into `sys.modules`."""

    fake_docx = ModuleType("docx")

    def _document(_: str) -> _FakeDocxDocument:
        fake_paragraphs = [_FakeDocxParagraph(text, style_name) for text, style_name in paragraphs]
        return _FakeDocxDocument(fake_paragraphs)

    fake_docx.Document = _document  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docx", fake_docx)


def _install_fake_docx_failure(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Install a fake `docx` module whose constructor always raises."""

    fake_docx = ModuleType("docx")

    def _document(_: str) -> None:
        raise exc

    fake_docx.Document = _document  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docx", fake_docx)


@pytest.mark.anyio
async def test_markdown_parser_creates_sections_and_anchors() -> None:
    """Markdown should be split into heading-based sections with stable anchors."""
    sample_path = Path("tests/fixtures/sample_inputs/sample.md").resolve()
    parser = get_parser("markdown")
    parsed_document = await parser.parse_path(sample_path)

    assert isinstance(parsed_document, ParsedDocument)
    assert parsed_document.metadata.filename == "sample.md"
    assert parsed_document.metadata.format == "markdown"
    assert parsed_document.metadata.page_count == 0

    assert len(parsed_document.sections) == 6

    anchors = [section.page_or_section for section in parsed_document.sections]
    assert anchors == [
        "H1: Travel Risk Assessment Policy",
        "H2: Scope",
        "H3: Inputs",
        "H3: Output",
        "H2: Definitions",
        "H2: Process",
    ]

    for section in parsed_document.sections:
        # Anchors should correspond to heading titles; content should not include raw heading markers.
        assert section.content
        assert "#" not in section.content


@pytest.mark.anyio
async def test_markdown_parser_rejects_empty_file(tmp_path: Path) -> None:
    """An empty Markdown file should fail validation after normalization."""
    empty_file = tmp_path / "empty.md"
    empty_file.write_text("", encoding="utf-8")

    parser = get_parser("markdown")
    with pytest.raises(ExtractionError) as exc_info:
        await parser.parse_path(empty_file)

    extraction_error = exc_info.value
    assert extraction_error.error_code == "EXTRACTION_FAILED"


@pytest.mark.anyio
async def test_pdf_parser_extracts_non_empty_pages_and_page_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    """PDF parsing should skip empty pages but keep page_count metadata."""
    _install_fake_pdfplumber(monkeypatch, pages=["First page text", "", "Third page text"])

    parser = get_parser("pdf")
    parsed_document = await parser.parse_path(Path("tests/fixtures/sample_inputs/sample.pdf").resolve())

    assert parsed_document.metadata.format == "pdf"
    assert parsed_document.metadata.page_count == 3
    assert len(parsed_document.sections) == 2

    assert parsed_document.sections[0].page_or_section == "Page 1"
    assert parsed_document.sections[1].page_or_section == "Page 3"

    full_text = parsed_document.full_text
    assert "First page text" in full_text
    assert "Third page text" in full_text
    assert "  " not in full_text


@pytest.mark.anyio
async def test_pdf_parser_malformed_input_raises_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """pdfplumber.open failures should be wrapped as ExtractionError."""
    _install_fake_pdfplumber_failure(monkeypatch, exc=OSError("boom"))

    parser = get_parser("pdf")
    with pytest.raises(ExtractionError) as exc_info:
        await parser.parse_path(Path("tests/fixtures/sample_inputs/malformed.pdf").resolve())

    extraction_error = exc_info.value
    assert extraction_error.error_code == "EXTRACTION_FAILED"


@pytest.mark.anyio
async def test_docx_parser_extracts_heading_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCX parsing should create sections using Heading N style paragraphs."""
    _install_fake_docx(
        monkeypatch,
        paragraphs=[
            ("Intro", "Heading 1"),
            ("This is the first section.", None),
            ("Second", "Heading 2"),
            ("This is the second section.", None),
        ],
    )

    parser = get_parser("docx")
    parsed_document = await parser.parse_path(Path("tests/fixtures/sample_inputs/sample.docx").resolve())

    assert parsed_document.metadata.format == "docx"
    assert parsed_document.metadata.page_count == 0
    assert len(parsed_document.sections) == 2

    assert parsed_document.sections[0].page_or_section == "H1: Intro"
    assert parsed_document.sections[0].title == "Intro"
    assert "first section" in parsed_document.sections[0].content

    assert parsed_document.sections[1].page_or_section == "H2: Second"
    assert parsed_document.sections[1].title == "Second"
    assert "second section" in parsed_document.sections[1].content

    assert parsed_document.full_text
    assert "Intro" not in parsed_document.sections[0].content


@pytest.mark.anyio
async def test_docx_parser_falls_back_when_no_headings(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no headings exist, DOCX parsing returns a single 'Document' section."""
    _install_fake_docx(
        monkeypatch,
        paragraphs=[
            ("Paragraph A", None),
            ("Paragraph B", None),
        ],
    )

    parser = get_parser("docx")
    parsed_document = await parser.parse_path(Path("tests/fixtures/sample_inputs/sample.docx").resolve())

    assert len(parsed_document.sections) == 1
    section = parsed_document.sections[0]
    assert section.title == "Document"
    assert section.page_or_section == "Document"
    assert "Paragraph A" in section.content
    assert "Paragraph B" in section.content


@pytest.mark.anyio
async def test_docx_parser_malformed_input_raises_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCX parsing should wrap parsing exceptions as ExtractionError."""
    _install_fake_docx_failure(monkeypatch, exc=ValueError("docx broken"))

    parser = get_parser("docx")
    with pytest.raises(ExtractionError) as exc_info:
        await parser.parse_path(Path("tests/fixtures/sample_inputs/malformed.pdf").resolve())

    extraction_error = exc_info.value
    assert extraction_error.error_code == "EXTRACTION_FAILED"
