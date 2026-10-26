from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio

from agentops.api.errors import INTERNAL_ERROR_MESSAGE
from agentops.api.schemas import MessageOut, RetrievedChunkOut, UsageOut
from agentops.application.chat_service import (
    CompletedEvent,
    FailedEvent,
    InternalFailure,
    ReplyStream,
    RetrievalEvent,
    TokenEvent,
)
from agentops.domain.errors import LLMError, LLMRateLimited

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def format_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def reply_events(reply: ReplyStream, request_id: str) -> AsyncIterator[str]:
    yield format_event(
        "message.created",
        {
            "user_message": MessageOut.of(reply.user_message).model_dump(mode="json"),
            "assistant_message_id": str(reply.assistant_message_id),
        },
    )
    try:
        async for event in reply.events:
            match event:
                case RetrievalEvent(chunks=chunks, sufficient=sufficient):
                    yield format_event(
                        "retrieval",
                        {
                            "chunks": [RetrievedChunkOut.of(c).model_dump(mode="json") for c in chunks],
                            "sufficient": sufficient,
                        },
                    )
                case TokenEvent(delta=delta):
                    yield format_event("token", {"delta": delta})
                case CompletedEvent(message=message, usage=usage):
                    yield format_event(
                        "done",
                        {
                            "message": MessageOut.of(message).model_dump(mode="json"),
                            "usage": UsageOut.of(usage).model_dump(mode="json"),
                        },
                    )
                case FailedEvent(error=error, message_id=message_id):
                    data: dict[str, Any] = {
                        "code": error.code,
                        # Domain error messages are written to be user-safe; unexpected ones are not.
                        "message": INTERNAL_ERROR_MESSAGE if isinstance(error, InternalFailure) else error.message,
                        "retryable": isinstance(error, LLMError) and error.retryable,
                        "request_id": request_id,
                        "message_id": str(message_id),
                    }
                    if isinstance(error, LLMRateLimited) and error.retry_after_s is not None:
                        data["retry_after_s"] = error.retry_after_s
                    yield format_event("error", data)
    finally:
        # On client disconnect this generator is cancelled; close the reply
        # stream under a shield so the service can record the partial answer.
        with anyio.CancelScope(shield=True):
            await reply.events.aclose()
