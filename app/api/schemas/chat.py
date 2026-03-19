"""
Chat-related Pydantic schemas (query request/response and citations).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class CitationSchema(BaseModel):
    """A single citation pointing to a source document location."""

    document_title: str = Field(..., description="Human-friendly document title")
    doc_id: UUID = Field(..., description="Stable document UUID")
    page_or_section: str = Field(..., description="Page number or section/heading reference")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Relevance score for this citation")
    chunk_preview: str = Field(..., max_length=200, description="Preview snippet from the cited chunk (max 200 chars)")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "document_title": "ISO 9001 Quality Framework",
                    "doc_id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                    "page_or_section": "Section 7.5.3",
                    "relevance_score": 0.91,
                    "chunk_preview": "The organization shall ensure documented information...",
                }
            ]
        }
    )


class ChatQueryRequest(BaseModel):
    """Request payload for querying the knowledge base."""

    question: str = Field(..., min_length=1, description="Natural language question to answer")
    conversation_id: UUID | None = Field(
        default=None,
        description="Optional conversation UUID to enable multi-turn context",
    )
    collection_ids: list[str] | None = Field(
        default=None,
        description="Optional list of collection IDs to restrict retrieval scope",
    )
    max_chunks: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of retrieved chunks to use as evidence",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "question": "What's our approach to supply chain risk assessment?",
                    "conversation_id": "6c4a8f31-8d1e-4d8c-93c6-8d7df5f3f01a",
                    "collection_ids": ["compliance", "operations"],
                    "max_chunks": 8,
                }
            ]
        }
    )


class ChatQueryResponse(BaseModel):
    """Response payload for a knowledge base question."""

    answer: str = Field(..., description="Grounded answer text derived from retrieved documents")
    citations: list[CitationSchema] = Field(..., description="Citations supporting the answer")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score for answer reliability")
    conversation_id: UUID = Field(..., description="Conversation UUID for subsequent multi-turn requests")
    refused: bool = Field(..., description="Whether the system refused instead of answering")
    refusal_reason: str | None = Field(default=None, description="Reason for refusal when refused=True")
    tokens_used: int = Field(..., ge=0, description="Total tokens used for all LLM calls in this request")
    cost_usd: float = Field(..., ge=0.0, description="Total USD cost for this request")
    latency_ms: float = Field(..., ge=0.0, description="End-to-end latency in milliseconds")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "answer": "We assess supply chain risk using a tiered vendor evaluation... ",
                    "citations": [
                        {
                            "document_title": "Supply Chain Risk Assessment Methodology",
                            "doc_id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                            "page_or_section": "Section 4.2",
                            "relevance_score": 0.88,
                            "chunk_preview": "Risk is evaluated across impact, likelihood, and controls...",
                        }
                    ],
                    "confidence": 0.86,
                    "conversation_id": "6c4a8f31-8d1e-4d8c-93c6-8d7df5f3f01a",
                    "refused": False,
                    "refusal_reason": None,
                    "tokens_used": 1430,
                    "cost_usd": 0.031,
                    "latency_ms": 2450.7,
                }
            ]
        }
    )
