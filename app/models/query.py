"""
ORM models for query analytics and LLM call auditing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class LlmCallType(StrEnum):
    """Types of LLM calls made by the system."""

    generation = "generation"
    rewrite = "rewrite"
    evaluation = "evaluation"
    embedding = "embedding"


class QueryEvent(Base):
    """Persisted analytics for a user question handled by the system."""

    __tablename__ = "query_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    question_hash: Mapped[str] = mapped_column(String(length=64), nullable=False, index=True)

    user_group: Mapped[str | None] = mapped_column(String(length=100), nullable=True)

    collection_ids_searched: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    retrieval_strategy: Mapped[str] = mapped_column(String(length=100), nullable=False)
    chunks_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    refused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refusal_reason: Mapped[str | None] = mapped_column(String(length=2000), nullable=True)

    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    prompt_version: Mapped[str] = mapped_column(String(length=100), nullable=False)
    model: Mapped[str] = mapped_column(String(length=200), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    llm_calls: Mapped[list[LlmCallAudit]] = relationship(
        "LlmCallAudit",
        back_populates="query_event",
        cascade="all, delete-orphan",
    )


class LlmCallAudit(Base):
    """Audit record for each upstream LLM call the system makes."""

    __tablename__ = "llm_call_audits"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    query_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("query_events.id"),
        nullable=True,
    )

    call_type: Mapped[LlmCallType] = mapped_column(
        SAEnum(LlmCallType, name="llmcalltype"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(length=200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(length=100), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    query_event: Mapped[QueryEvent | None] = relationship("QueryEvent", back_populates="llm_calls")
