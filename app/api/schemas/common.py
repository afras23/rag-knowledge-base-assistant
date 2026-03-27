"""
Shared Pydantic schemas used across API boundaries.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

TItem = TypeVar("TItem")
TData = TypeVar("TData")


class ErrorDetail(BaseModel):
    """Machine-readable and human-readable error information."""

    code: str = Field(..., description="Stable machine-readable error code")
    message: str = Field(..., description="Human-readable error message")

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"code": "DOCUMENT_NOT_FOUND", "message": "Document not found"}]}
    )


class SuccessResponse(BaseModel, Generic[TData]):
    """Standard success envelope for API responses."""

    status: Literal["success"] = Field(default="success", description="Indicates a successful response")
    data: TData = Field(..., description="Response payload")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Request metadata (e.g. correlation_id)",
    )


class ErrorResponse(BaseModel):
    """Standard error envelope for API responses."""

    status: Literal["error"] = Field(..., description="Indicates an error response")
    error: ErrorDetail = Field(..., description="Error payload")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional error metadata")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "error",
                    "error": {"code": "DOCUMENT_NOT_FOUND", "message": "Document not found"},
                    "metadata": {"correlation_id": "abc123"},
                }
            ]
        }
    )


class PaginatedResponse(BaseModel, Generic[TItem]):
    """Generic paginated response envelope."""

    items: list[TItem] = Field(..., description="Items for the current page")
    total: int = Field(..., ge=0, description="Total item count across all pages")
    page: int = Field(..., ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(..., ge=1, le=100, description="Number of items per page")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"items": ["..."], "total": 10, "page": 1, "page_size": 5}],
        }
    )


class MetricsResponse(BaseModel):
    """Operational and business metrics exposed by the service."""

    queries_today: int = Field(..., ge=0, description="Number of chat queries processed today")
    refusals_today: int = Field(..., ge=0, description="Number of refused queries today (UTC day)")
    avg_latency_ms: float = Field(..., ge=0, description="Average query latency in milliseconds")
    cost_today_usd: float = Field(..., ge=0, description="Total AI cost today in USD")
    cost_limit_usd: float = Field(..., gt=0, description="Configured daily cost limit in USD")
    documents_indexed: int = Field(..., ge=0, description="Number of documents fully indexed")
    active_collections: int = Field(..., ge=0, description="Collections with at least one indexed document")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "queries_today": 128,
                    "refusals_today": 12,
                    "avg_latency_ms": 842.3,
                    "cost_today_usd": 4.21,
                    "cost_limit_usd": 50.0,
                    "documents_indexed": 612,
                    "active_collections": 8,
                }
            ]
        }
    )
