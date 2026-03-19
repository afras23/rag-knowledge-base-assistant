"""
Document ORM model for RAG corpus metadata and versioning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Document(Base):
    """A document registered for ingestion and retrieval."""

    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(length=500), nullable=False)
    file_format: Mapped[str] = mapped_column(String(length=20), nullable=False)
    collection_id: Mapped[str] = mapped_column(String(length=100), ForeignKey("collections.id"), nullable=False)
    restriction_level: Mapped[str] = mapped_column(String(length=30), nullable=False)

    content_hash: Mapped[str] = mapped_column(String(length=128), nullable=False, unique=True)

    version_label: Mapped[str | None] = mapped_column(String(length=100), nullable=True)

    supersedes_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=True,
    )

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    supersedes: Mapped[Document | None] = relationship(
        "Document",
        remote_side="Document.id",
        foreign_keys=[supersedes_id],
        uselist=False,
    )
