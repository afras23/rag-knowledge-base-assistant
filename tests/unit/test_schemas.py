"""
Unit tests for Pydantic boundary schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas.admin import (
    CollectionSchema,
    DocumentCreateRequest,
    DocumentResponse,
    IngestionEventResponse,
    IngestionJobResponse,
)
from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse, CitationSchema
from app.api.schemas.common import ErrorDetail, ErrorResponse, MetricsResponse, PaginatedResponse


def test_chat_query_request_happy_path() -> None:
    """Valid ChatQueryRequest should parse successfully."""
    payload = ChatQueryRequest(
        question="What is our travel policy?",
        conversation_id=None,
        collection_ids=["compliance"],
        max_chunks=5,
    )
    assert payload.question
    assert payload.max_chunks == 5


def test_chat_query_request_rejects_empty_question() -> None:
    """Empty questions should be rejected by min_length constraint."""
    with pytest.raises(ValidationError):
        ChatQueryRequest(
            question="",
            conversation_id=None,
            collection_ids=None,
            max_chunks=5,
        )


@pytest.mark.parametrize("max_chunks", [0, 21])
def test_chat_query_request_rejects_out_of_range_max_chunks(max_chunks: int) -> None:
    """max_chunks must be between 1 and 20 inclusive."""
    with pytest.raises(ValidationError):
        ChatQueryRequest(
            question="How do we do risk assessment?",
            conversation_id=None,
            collection_ids=None,
            max_chunks=max_chunks,
        )


def test_citation_schema_happy_path() -> None:
    """A valid citation should validate."""
    citation = CitationSchema(
        document_title="ISO 9001 Quality Framework",
        doc_id=uuid4(),
        page_or_section="Section 7.5.3",
        relevance_score=0.9,
        chunk_preview="The organization shall ensure documented information...",
    )
    assert citation.relevance_score == 0.9


def test_chat_query_response_refused_requires_reason_formatting() -> None:
    """ChatQueryResponse should validate with refused=True and a refusal_reason."""
    conversation_id = uuid4()
    response = ChatQueryResponse(
        answer="",
        citations=[],
        confidence=0.1,
        conversation_id=conversation_id,
        refused=True,
        refusal_reason="I couldn't find relevant evidence in your allowed documents.",
        tokens_used=0,
        cost_usd=0.0,
        latency_ms=0.0,
    )
    assert response.refused is True
    assert response.refusal_reason is not None


def test_document_create_request_happy_path() -> None:
    """DocumentCreateRequest should validate for allowed formats and restriction levels."""
    payload = DocumentCreateRequest(
        title="Travel Policy",
        file_format="pdf",
        collection_id="operations",
        restriction_level="public",
        version_label="2026-03",
        supersedes_doc_id=None,
    )
    assert payload.file_format == "pdf"


def test_document_create_request_rejects_invalid_file_format() -> None:
    """Unsupported file formats should be rejected."""
    with pytest.raises(ValidationError):
        DocumentCreateRequest(
            title="Travel Policy",
            file_format="exe",
            collection_id="operations",
            restriction_level="public",
            version_label=None,
            supersedes_doc_id=None,
        )


def test_paginated_response_generic_happy_path() -> None:
    """PaginatedResponse[int] should validate with item types."""
    response = PaginatedResponse[int](items=[1, 2], total=2, page=1, page_size=2)
    assert response.items == [1, 2]
    assert response.total == 2


def test_error_response_rejects_wrong_status() -> None:
    """ErrorResponse.status must be exactly 'error'."""
    with pytest.raises(ValidationError):
        ErrorResponse(
            status="ok",
            error=ErrorDetail(code="DOCUMENT_NOT_FOUND", message="Document not found"),
            metadata={},
        )


def test_metrics_response_rejects_negative_cost() -> None:
    """MetricsResponse enforces non-negative cost fields."""
    with pytest.raises(ValidationError):
        MetricsResponse(
            queries_today=1,
            refusals_today=0,
            avg_latency_ms=10.0,
            cost_today_usd=-1.0,
            cost_limit_usd=50.0,
            documents_indexed=0,
            active_collections=0,
        )


def test_admin_schemas_happy_path_shapes() -> None:
    """Smoke-test additional admin schemas with valid sample values."""
    doc_id = uuid4()
    now = datetime.now(timezone.utc)

    document = DocumentResponse(
        id=doc_id,
        title="Travel Policy",
        file_format="pdf",
        collection_id="operations",
        restriction_level="public",
        version_label="2026-03",
        superseded_by=None,
        chunk_count=10,
        ingestion_status="completed",
        created_at=now,
        updated_at=now,
    )
    assert document.ingestion_status == "completed"

    collection = CollectionSchema(
        id="operations",
        name="Operations",
        description="Operations playbooks and policies",
        allowed_roles=["consultant", "lead"],
    )
    assert collection.allowed_roles

    job = IngestionJobResponse(
        job_id=uuid4(),
        status="processing",
        total_documents=5,
        processed=2,
        succeeded=2,
        failed=0,
        started_at=now,
        completed_at=None,
    )
    assert job.status == "processing"

    event = IngestionEventResponse(
        document_id=doc_id,
        stage="parsing",
        status="success",
        error_message=None,
        duration_ms=12.3,
        timestamp=now,
    )
    assert event.stage == "parsing"
