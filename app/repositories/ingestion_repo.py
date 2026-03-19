"""
Repository for ingestion job lifecycle and event tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion import (
    IngestionEvent,
    IngestionEventStatus,
    IngestionJob,
    IngestionJobStatus,
)
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class IngestionRepository(BaseRepository):
    """Data access layer for ingestion jobs and ingestion events."""

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize ingestion repository."""
        super().__init__(db_session)

    async def create_job(
        self,
        *,
        total_documents: int,
        created_by: str | None,
    ) -> IngestionJob:
        """Create an ingestion job record."""
        db_job = IngestionJob(
            status=IngestionJobStatus.pending,
            total_documents=total_documents,
            processed=0,
            succeeded=0,
            failed=0,
            created_by=created_by,
        )
        self.db_session.add(db_job)
        await self.db_session.commit()
        await self.db_session.refresh(db_job)
        logger.info(
            "Created ingestion job",
            extra={"job_id": str(db_job.id), "total_documents": total_documents},
        )
        return db_job

    async def update_job_progress(
        self,
        *,
        job_id: UUID,
        status: IngestionJobStatus,
        processed: int,
        succeeded: int,
        failed: int,
        completed_at: datetime | None = None,
    ) -> None:
        """
        Update job progress counters.

        Args:
            job_id: Ingestion job UUID.
            status: New job status.
            processed: Processed document count.
            succeeded: Succeeded count.
            failed: Failed count.
            completed_at: Optional completion datetime.
        """
        await self.db_session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(
                status=status,
                processed=processed,
                succeeded=succeeded,
                failed=failed,
                completed_at=completed_at,
            )
        )
        await self.db_session.commit()
        logger.info(
            "Updated ingestion job progress",
            extra={"job_id": str(job_id), "status": status.value},
        )

    async def log_ingestion_event(
        self,
        *,
        job_id: UUID,
        document_id: UUID,
        stage: str,
        status: IngestionEventStatus,
        error_message: str | None,
        duration_ms: float,
    ) -> IngestionEvent:
        """Log a single ingestion event."""
        db_event = IngestionEvent(
            job_id=job_id,
            document_id=document_id,
            stage=stage,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self.db_session.add(db_event)
        await self.db_session.commit()
        await self.db_session.refresh(db_event)
        logger.info(
            "Logged ingestion event",
            extra={"job_id": str(job_id), "document_id": str(document_id), "stage": stage, "status": status.value},
        )
        return db_event

    async def get_job_status(self, *, job_id: UUID) -> IngestionJob:
        """Fetch a job by ID or raise if missing."""
        query = select(IngestionJob).where(IngestionJob.id == job_id)
        result = await self.db_session.execute(query)
        db_job = result.scalar_one_or_none()
        if db_job is None:
            raise LookupError(f"Ingestion job not found: {job_id}")
        return db_job
