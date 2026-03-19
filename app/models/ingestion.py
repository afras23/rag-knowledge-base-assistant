"""
Ingestion ORM models for batch ingestion jobs and per-document ingestion events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class IngestionJobStatus(StrEnum):
    """Status values for an ingestion job."""

    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class IngestionEventStatus(StrEnum):
    """Outcome values for ingestion events."""

    success = "success"
    failed = "failed"
    skipped = "skipped"


class IngestionJob(Base):
    """A batch ingestion job."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[IngestionJobStatus] = mapped_column(
        SAEnum(IngestionJobStatus, name="ingestionjobstatus"), nullable=False
    )

    total_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(length=200), nullable=True)

    events: Mapped[list[IngestionEvent]] = relationship(
        "IngestionEvent",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class IngestionEvent(Base):
    """A per-document ingestion event within a job."""

    __tablename__ = "ingestion_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("ingestion_jobs.id"), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)

    stage: Mapped[str] = mapped_column(String(length=200), nullable=False)
    status: Mapped[IngestionEventStatus] = mapped_column(
        SAEnum(IngestionEventStatus, name="ingestioneventstatus"),
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(String(length=2000), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    job: Mapped[IngestionJob] = relationship("IngestionJob", back_populates="events")
