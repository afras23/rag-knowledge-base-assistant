"""
ORM models for evaluation runs and quality metrics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Float, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvaluationRun(Base):
    """Evaluation run results for a specific prompt/chunking configuration."""

    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    overall_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    precision_at_k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recall_at_k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    citation_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
