"""API schemas for requests and responses."""

from app.api.schemas.admin import (
    CollectionSchema,
    DocumentCreateRequest,
    DocumentResponse,
    IngestionEventResponse,
    IngestionJobResponse,
)
from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse, CitationSchema
from app.api.schemas.common import ErrorDetail, ErrorResponse, MetricsResponse, PaginatedResponse

__all__ = [
    "ChatQueryRequest",
    "ChatQueryResponse",
    "CitationSchema",
    "DocumentCreateRequest",
    "DocumentResponse",
    "CollectionSchema",
    "IngestionJobResponse",
    "IngestionEventResponse",
    "ErrorDetail",
    "ErrorResponse",
    "PaginatedResponse",
    "MetricsResponse",
]
