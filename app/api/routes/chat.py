"""
Chat query and conversation history API (Phase 8).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ConversationDetailSchema,
    ConversationMessageItemSchema,
    ConversationSummarySchema,
)
from app.api.schemas.common import PaginatedResponse, SuccessResponse
from app.core.dependencies import get_db_session, get_query_service
from app.core.exceptions import ConversationNotFoundError
from app.repositories.conversation_repo import ConversationRepository
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _correlation_id(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "") or "")


@router.post("/chat/query")
async def post_chat_query(
    body: ChatQueryRequest,
    request: Request,
    query_service: QueryService = Depends(get_query_service),
) -> SuccessResponse[ChatQueryResponse]:
    """
    Run a grounded chat query with optional multi-turn context.
    """
    cid = _correlation_id(request)
    payload = await query_service.query(body, correlation_id=cid or None)
    logger.info(
        "Chat query completed",
        extra={"correlation_id": cid, "refused": payload.refused},
    )
    return SuccessResponse(
        status="success",
        data=payload,
        metadata={"correlation_id": cid},
    )


@router.get("/chat/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> SuccessResponse[ConversationDetailSchema]:
    """Return a conversation and its messages in chronological order."""
    repo = ConversationRepository(session)
    conv = await repo.get_conversation(conversation_id=conversation_id)
    if conv is None:
        raise ConversationNotFoundError(
            context={"conversation_id": str(conversation_id)},
        )
    messages = [
        ConversationMessageItemSchema(
            id=m.id,
            role=m.role.value,
            content=m.content,
            refused=m.refused,
            created_at=m.created_at,
        )
        for m in conv.messages
    ]
    detail = ConversationDetailSchema(
        id=conv.id,
        user_group=conv.user_group,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )
    cid = _correlation_id(request)
    return SuccessResponse(
        status="success",
        data=detail,
        metadata={"correlation_id": cid},
    )


@router.get("/chat/conversations")
async def list_conversations(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_db_session),
) -> SuccessResponse[PaginatedResponse[ConversationSummarySchema]]:
    """Paginate conversations (most recently updated first)."""
    repo = ConversationRepository(session)
    rows, total = await repo.get_conversations(page=page, page_size=page_size)
    items = [
        ConversationSummarySchema(
            id=c.id,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in rows
    ]
    page_payload = PaginatedResponse[ConversationSummarySchema](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    cid = _correlation_id(request)
    return SuccessResponse(
        status="success",
        data=page_payload,
        metadata={"correlation_id": cid},
    )
