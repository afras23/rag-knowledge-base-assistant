"""
Tests for correlation ID middleware and context binding (Phase 11).
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from app.core.middleware import CORRELATION_HEADER, CorrelationIdMiddleware, correlation_id_ctx


def test_propagates_incoming_correlation_header() -> None:
    """Client-provided ``X-Correlation-ID`` is echoed on the response."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/t")
    def read_root() -> PlainTextResponse:
        return PlainTextResponse("ok")

    client = TestClient(app)
    cid = str(uuid.uuid4())
    response = client.get("/t", headers={CORRELATION_HEADER: cid})
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == cid


def test_generates_uuid_when_header_missing() -> None:
    """Missing header yields a new UUID in the response header."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/t")
    def read_root() -> PlainTextResponse:
        return PlainTextResponse("ok")

    client = TestClient(app)
    response = client.get("/t")
    assert response.status_code == 200
    out = response.headers[CORRELATION_HEADER]
    parsed = uuid.UUID(out)
    assert str(parsed) == out


def test_contextvar_set_during_request() -> None:
    """``correlation_id_ctx`` matches the active request id inside handlers."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    seen: dict[str, str | None] = {}

    @app.get("/ctx")
    def read_ctx() -> PlainTextResponse:
        seen["ctx"] = correlation_id_ctx.get()
        return PlainTextResponse("ok")

    client = TestClient(app)
    cid = str(uuid.uuid4())
    client.get("/ctx", headers={CORRELATION_HEADER: cid})
    assert seen.get("ctx") == cid
