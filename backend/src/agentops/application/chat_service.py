from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from agentops.application.prompts import SYSTEM_PROMPT
from agentops.domain.errors import ConversationNotFound, DomainError, LLMError, LLMNotConfigured
from agentops.domain.models import (
    DEFAULT_CONVERSATION_TITLE,
    ChatTurn,
    Conversation,
    Message,
    MessageStatus,
    Role,
    StreamEnd,
    TextDelta,
    Usage,
)
from agentops.domain.ports import ChatModel, ConversationRepository

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 60
LIST_LIMIT = 100


def _utcnow() -> datetime:
    return datetime.now(UTC)


def title_from_content(content: str) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class TokenEvent:
    delta: str


@dataclass(frozen=True, slots=True)
class CompletedEvent:
    message: Message
    usage: Usage


@dataclass(frozen=True, slots=True)
class FailedEvent:
    error: DomainError
    message_id: UUID


type ReplyEvent = TokenEvent | CompletedEvent | FailedEvent


@dataclass(frozen=True, slots=True)
class ReplyStream:
    user_message: Message
    assistant_message_id: UUID
    events: AsyncGenerator[ReplyEvent]


class _InternalFailure(DomainError):
    pass


class ChatService:
    def __init__(
        self,
        repository: ConversationRepository,
        chat_model: ChatModel | None,
        *,
        history_limit: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._model = chat_model
        self._history_limit = history_limit
        self._clock = clock
        self._pending: set[asyncio.Task[None]] = set()

    async def create_conversation(self, title: str | None = None) -> Conversation:
        now = self._clock()
        conversation = Conversation(
            id=uuid.uuid4(), title=title or DEFAULT_CONVERSATION_TITLE, created_at=now, updated_at=now
        )
        await self._repo.create(conversation)
        return conversation

    async def list_conversations(self) -> list[Conversation]:
        return await self._repo.list_recent(LIST_LIMIT)

    async def get_conversation(self, conversation_id: UUID) -> tuple[Conversation, list[Message]]:
        conversation = await self._require(conversation_id)
        return conversation, await self._repo.list_messages(conversation_id)

    async def delete_conversation(self, conversation_id: UUID) -> None:
        if not await self._repo.delete(conversation_id):
            raise ConversationNotFound()

    async def send_message(self, conversation_id: UUID, content: str) -> ReplyStream:
        """Store the user message and return the assistant reply as a lazy event stream.

        Raises before anything is stored if the conversation is missing or no
        model is configured, so the caller can answer with a plain error.
        """
        conversation = await self._require(conversation_id)
        if self._model is None:
            raise LLMNotConfigured()
        model = self._model

        is_first = conversation.title == DEFAULT_CONVERSATION_TITLE and not await self._repo.list_messages(
            conversation_id
        )
        user_message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role=Role.USER,
            content=content,
            status=MessageStatus.COMPLETE,
            created_at=self._clock(),
        )
        await self._repo.add_message(user_message, new_title=title_from_content(content) if is_first else None)

        assistant_id = uuid.uuid4()
        return ReplyStream(
            user_message=user_message,
            assistant_message_id=assistant_id,
            events=self._reply(model, conversation_id, assistant_id),
        )

    async def wait_for_pending_writes(self) -> None:
        """Wait for assistant messages still being persisted (used on shutdown and in tests)."""
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _reply(self, model: ChatModel, conversation_id: UUID, message_id: UUID) -> AsyncGenerator[ReplyEvent]:
        history = [
            ChatTurn(role=m.role, content=m.content)
            for m in await self._repo.recent_complete_messages(conversation_id, self._history_limit)
        ]
        parts: list[str] = []
        usage = Usage()
        started = time.monotonic()
        log_ctx = {"conversation_id": str(conversation_id), "message_id": str(message_id), "model": model.model}

        def build(status: MessageStatus, error_code: str | None) -> Message:
            if status is not MessageStatus.COMPLETE and parts:
                status = MessageStatus.INCOMPLETE
            message = Message(
                id=message_id,
                conversation_id=conversation_id,
                role=Role.ASSISTANT,
                content="" if status is MessageStatus.FAILED else "".join(parts),
                status=status,
                error_code=error_code,
                model=model.model,
                created_at=self._clock(),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            logger.info(
                "chat.reply_finished",
                extra={
                    **log_ctx,
                    "status": status.value,
                    "error_code": error_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "output_chars": len(message.content),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                },
            )
            return message

        failure: DomainError | None = None
        message: Message | None = None
        try:
            async for chunk in model.stream(history, system_prompt=SYSTEM_PROMPT):
                if isinstance(chunk, TextDelta):
                    if chunk.text:
                        parts.append(chunk.text)
                        yield TokenEvent(chunk.text)
                elif isinstance(chunk, StreamEnd):
                    usage = chunk.usage
            message = build(MessageStatus.COMPLETE, None)
        except LLMError as exc:
            failure = exc
            message = build(MessageStatus.FAILED, exc.code)
        except Exception:
            logger.exception("chat.reply_crashed", extra=log_ctx)
            failure = _InternalFailure()
            message = build(MessageStatus.FAILED, failure.code)
        finally:
            if message is None:
                # Cancelled or closed mid-stream (the client went away). Keep what
                # was generated; there is no provider error code to record.
                logger.info("chat.reply_abandoned", extra=log_ctx)
                self._store_in_background(build(MessageStatus.FAILED, None))

        try:
            # The write runs in its own task and is shielded, so a client
            # disconnect cannot interrupt it halfway.
            await asyncio.shield(self._store_in_background(message))
        except Exception:
            failure = failure or _InternalFailure()

        if failure is None:
            yield CompletedEvent(message=message, usage=usage)
        else:
            yield FailedEvent(error=failure, message_id=message_id)

    def _store_in_background(self, message: Message) -> asyncio.Task[None]:
        async def store() -> None:
            try:
                await self._repo.add_message(message)
            except ConversationNotFound:
                logger.info("chat.reply_dropped_conversation_deleted", extra={"message_id": str(message.id)})
            except Exception:
                logger.exception("chat.reply_persist_failed", extra={"message_id": str(message.id)})
                raise

        task = asyncio.create_task(store())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def _require(self, conversation_id: UUID) -> Conversation:
        conversation = await self._repo.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound()
        return conversation
