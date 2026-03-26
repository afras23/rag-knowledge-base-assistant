"""
Shared pytest fixtures for the test suite.

Note: The ingestion parsers/chunker currently pass `extra={"filename": ...}` to
`logger.info(...)`. Standard Python logging forbids overwriting the built-in
LogRecord `filename` attribute, which would otherwise crash unit/integration
tests.

To keep tests focused on functional behavior (parsing/chunking/pipeline),
these tests monkeypatch the specific module loggers to no-op.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _silence_ingestion_module_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable logger output in ingestion parsers and chunker during tests."""
    from app.services.ingestion import chunker as chunker_module
    from app.services.ingestion import parsers as parsers_module

    def _noop(*_: object, **__: object) -> None:
        """No-op logging sink."""

    monkeypatch.setattr(chunker_module.logger, "info", _noop)
    monkeypatch.setattr(chunker_module.logger, "warning", _noop)
    monkeypatch.setattr(chunker_module.logger, "error", _noop)

    monkeypatch.setattr(parsers_module.logger, "info", _noop)
    monkeypatch.setattr(parsers_module.logger, "warning", _noop)
    monkeypatch.setattr(parsers_module.logger, "error", _noop)
