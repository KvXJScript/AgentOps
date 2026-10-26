from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from agentops.api.dependencies import ChatServiceDep
from agentops.api.errors import request_id_of
from agentops.api.schemas import (
    ConversationDetailOut,
    ConversationListOut,
    ConversationOut,
    CreateConversationRequest,
    ErrorEnvelope,
    MessageOut,
    SendMessageRequest,
)
from agentops.api.sse import SSE_HEADERS, reply_events

router = APIRouter(prefix="/conversations", tags=["conversations"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorEnvelope}}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConversationOut)
async def create_conversation(
    service: ChatServiceDep, body: CreateConversationRequest | None = None
) -> ConversationOut:
    conversation = await service.create_conversation(body.title if body else None)
    return ConversationOut.of(conversation)


@router.get("", response_model=ConversationListOut)
async def list_conversations(service: ChatServiceDep) -> ConversationListOut:
    return ConversationListOut(items=[ConversationOut.of(c) for c in await service.list_conversations()])


@router.get("/{conversation_id}", response_model=ConversationDetailOut, responses=_NOT_FOUND)
async def get_conversation(conversation_id: UUID, service: ChatServiceDep) -> ConversationDetailOut:
    conversation, messages = await service.get_conversation(conversation_id)
    return ConversationDetailOut(
        **ConversationOut.of(conversation).model_dump(), messages=[MessageOut.of(m) for m in messages]
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_conversation(conversation_id: UUID, service: ChatServiceDep) -> Response:
    await service.delete_conversation(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Server-sent events, see docs/api-contract.md"},
        404: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
async def send_message(
    conversation_id: UUID, body: SendMessageRequest, request: Request, service: ChatServiceDep
) -> StreamingResponse:
    reply = await service.send_message(conversation_id, body.content)
    return StreamingResponse(
        reply_events(reply, request_id_of(request)), media_type="text/event-stream", headers=SSE_HEADERS
    )
