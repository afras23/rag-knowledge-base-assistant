"""
Admin-related Pydantic schemas for document and collection management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class DocumentCreateRequest(BaseModel):
    """Request to register a new document for ingestion."""

    title: str = Field(..., min_length=1, description="Document title")
    file_format: Literal["pdf", "docx", "markdown", "notion", "html"] = Field(
        ...,
        description="Input file format used for ingestion",
    )
    collection_id: str = Field(..., min_length=1, description="Target collection ID (practice area/team)")
    restriction_level: Literal["public", "restricted", "confidential"] = Field(
        ...,
        description="Visibility restriction level for the document",
    )
    version_label: str | None = Field(
        default=None,
        description="Optional version label (e.g., 'v2', '2026-01')",
    )
    supersedes_doc_id: UUID | None = Field(
        default=None,
        description="Optional document ID this document supersedes",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "ISO 9001 Quality Framework",
                    "file_format": "pdf",
                    "collection_id": "compliance",
                    "restriction_level": "confidential",
                    "version_label": "2026-03",
                    "supersedes_doc_id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                }
            ]
        }
    )


class DocumentResponse(BaseModel):
    """Response representing a registered document and its ingestion status."""

    id: UUID = Field(..., description="Document UUID")
    title: str = Field(..., description="Document title")
    file_format: str = Field(..., description="Stored file format")
    collection_id: str = Field(..., description="Collection ID this document belongs to")
    restriction_level: str = Field(..., description="Restriction level of this document")
    version_label: str | None = Field(default=None, description="Version label")
    supersedes_id: UUID | None = Field(
        default=None,
        description="UUID of the older document this one replaces, when applicable",
    )
    superseded_by: UUID | None = Field(default=None, description="UUID of the newer document that supersedes this one")
    chunk_count: int = Field(..., ge=0, description="Number of chunks indexed in the vector store")
    ingestion_status: Literal["pending", "processing", "completed", "failed"] = Field(
        ...,
        description="Current ingestion status",
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                    "title": "ISO 9001 Quality Framework",
                    "file_format": "pdf",
                    "collection_id": "compliance",
                    "restriction_level": "confidential",
                    "version_label": "2026-03",
                    "superseded_by": None,
                    "chunk_count": 214,
                    "ingestion_status": "completed",
                    "created_at": "2026-03-18T20:00:00Z",
                    "updated_at": "2026-03-18T20:05:00Z",
                }
            ]
        }
    )


class CollectionCreateRequest(BaseModel):
    """Create a logical document collection."""

    id: str = Field(..., min_length=1, max_length=100, description="Stable collection key (primary id)")
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    allowed_roles: list[str] = Field(default_factory=list)


class CollectionUpdateRequest(BaseModel):
    """Update collection metadata."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    allowed_roles: list[str] = Field(default_factory=list)


class DocumentSupersedeRequest(BaseModel):
    """Link a new document as the replacement for an older one."""

    new_document_id: UUID = Field(..., description="UUID of the newer document that replaces this row")


class CollectionSchema(BaseModel):
    """Represents a logical collection of documents for access control."""

    id: str = Field(..., description="Collection ID (stable string key)")
    name: str = Field(..., description="Human-friendly collection name")
    description: str = Field(..., description="Collection description")
    allowed_roles: list[str] = Field(..., description="Roles allowed to query this collection")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "compliance",
                    "name": "Compliance",
                    "description": "Compliance policies, frameworks, and templates",
                    "allowed_roles": ["consultant", "lead", "admin"],
                }
            ]
        }
    )


class IngestionJobResponse(BaseModel):
    """Response describing the status of an ingestion batch/job."""

    job_id: UUID = Field(..., description="Ingestion job UUID")
    status: Literal["pending", "processing", "completed", "failed"] = Field(..., description="Job status")
    total_documents: int = Field(..., ge=0, description="Total documents in the job")
    processed: int = Field(..., ge=0, description="Number of documents processed")
    succeeded: int = Field(..., ge=0, description="Number of documents successfully indexed")
    failed: int = Field(..., ge=0, description="Number of documents that failed")
    started_at: datetime = Field(..., description="Job start timestamp (UTC)")
    completed_at: datetime | None = Field(default=None, description="Job completion timestamp (UTC)")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "job_id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                    "status": "processing",
                    "total_documents": 10,
                    "processed": 4,
                    "succeeded": 3,
                    "failed": 1,
                    "started_at": "2026-03-18T20:00:00Z",
                    "completed_at": None,
                }
            ]
        }
    )


class IngestionEventResponse(BaseModel):
    """Response describing a single per-document ingestion event."""

    document_id: UUID = Field(..., description="Document UUID")
    stage: str = Field(..., description="Ingestion stage name (e.g., parsing, chunking, embedding)")
    status: Literal["success", "failed", "skipped"] = Field(..., description="Outcome for this event")
    error_message: str | None = Field(default=None, description="Optional error message if failed")
    duration_ms: float = Field(..., ge=0.0, description="Duration of this stage in milliseconds")
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "document_id": "d5d6f6ea-8a3b-4c6c-b9d6-4caa7e2b1c1a",
                    "stage": "parsing",
                    "status": "success",
                    "error_message": None,
                    "duration_ms": 123.4,
                    "timestamp": "2026-03-18T20:01:00Z",
                }
            ]
        }
    )
