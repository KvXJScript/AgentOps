from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from agentops.application.prompts import SYSTEM_PROMPT
from agentops.application.rag import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    EvidenceContext,
    RagService,
    finalize_answer,
    grounded_system_prompt,
    strip_markers,
)
from agentops.domain.documents import RetrievalUnavailable
from agentops.domain.errors import ConversationNotFound, DomainError, LLMError, LLMNotConfigured
from agentops.domain.models import (
    DEFAULT_CONVERSATION_TITLE,
    ChatTurn,
    Conversation,
    Grounding,
    Message,
    MessageStatus,
    Role,
    StreamEnd,
    TextDelta,
    Usage,
)
from agentops.domain.ports import ChatModel, ConversationRepository
from agentops.domain.retrieval import Citation, RetrievalTrace, RetrievedChunk

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 60
LIST_LIMIT = 100
CANCELLED = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def title_from_content(content: str) -> str:
    collapsed = " ".join(content.split())
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class RetrievalEvent:
    chunks: tuple[RetrievedChunk, ...]
    sufficient: bool


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


type ReplyEvent = RetrievalEvent | TokenEvent | CompletedEvent | FailedEvent


@dataclass(frozen=True, slots=True)
class ReplyStream:
    user_message: Message
    assistant_message_id: UUID
    events: AsyncGenerator[ReplyEvent]


class InternalFailure(DomainError):
    """An unexpected error; its details stay in the server logs."""


class RetrievalTraceNotFound(DomainError):
    code = "not_found"
    message = "No document retrieval was recorded for this message."


@dataclass(frozen=True, slots=True)
class _DocumentScope:
    document_ids: tuple[UUID, ...] | None


@dataclass(slots=True)
class _ReplyState:
    """What one assistant reply has produced so far (mutable while streaming)."""

    conversation_id: UUID
    message_id: UUID
    model: ChatModel
    scope: _DocumentScope | None
    started: float
    parts: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    context: EvidenceContext | None = None
    model_called: bool = False

    def log_context(self) -> dict[str, object]:
        return {
            "conversation_id": str(self.conversation_id),
            "message_id": str(self.message_id),
            "model": self.model.model,
            "use_documents": self.scope is not None,
        }


class ChatService:
    def __init__(
        self,
        repository: ConversationRepository,
        chat_model: ChatModel | None,
        *,
        history_limit: int,
        rag: RagService | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._model = chat_model
        self._rag = rag
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

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
        *,
        use_documents: bool = False,
        document_ids: Sequence[UUID] | None = None,
    ) -> ReplyStream:
        """Store the user message and return the assistant reply as a lazy event stream.

        With `use_documents`, the reply is grounded in retrieved chunks (restricted to
        `document_ids` when given; an empty list means no documents at all).

        Raises before anything is stored if the conversation is missing, no model is
        configured, or document search is unavailable, so the caller can answer with
        a plain error.
        """
        conversation = await self._require(conversation_id)
        if self._model is None:
            raise LLMNotConfigured()
        if use_documents and self._rag is None:
            raise RetrievalUnavailable()
        model = self._model
        scope = _DocumentScope(tuple(document_ids) if document_ids is not None else None) if use_documents else None

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
            events=self._reply(model, conversation_id, assistant_id, content, scope),
        )

    async def get_retrieval(self, message_id: UUID) -> RetrievalTrace:
        trace = await self._repo.get_retrieval(message_id)
        if trace is None:
            raise RetrievalTraceNotFound()
        return trace

    async def wait_for_pending_writes(self) -> None:
        """Wait for assistant messages still being persisted (used on shutdown and in tests)."""
        while self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _reply(
        self,
        model: ChatModel,
        conversation_id: UUID,
        message_id: UUID,
        question: str,
        scope: _DocumentScope | None,
    ) -> AsyncGenerator[ReplyEvent]:
        state = _ReplyState(
            conversation_id=conversation_id,
            message_id=message_id,
            model=model,
            scope=scope,
            started=time.monotonic(),
        )
        log_ctx = state.log_context()
        failure: DomainError | None = None
        result: tuple[Message, RetrievalTrace | None] | None = None
        try:
            async with aclosing(self._generate(state, question)) as events:
                async for event in events:
                    yield event
            result = self._build(state, MessageStatus.COMPLETE, None)
        except LLMError as exc:
            failure = exc
            result = self._build(state, MessageStatus.FAILED, exc.code)
        except DomainError as exc:  # e.g. the embedder failed while embedding the question
            failure = exc
            result = self._build(state, MessageStatus.FAILED, exc.code)
        except Exception:
            logger.exception("chat.reply_crashed", extra=log_ctx)
            failure = InternalFailure()
            result = self._build(state, MessageStatus.FAILED, failure.code)
        finally:
            if result is None:
                # Cancelled or closed mid-stream (the client went away). Keep what was generated.
                logger.info("chat.reply_abandoned", extra=log_ctx)
                self._store_in_background(*self._build(state, MessageStatus.FAILED, CANCELLED))

        message, trace = result
        try:
            # The write runs in its own task and is shielded, so a client
            # disconnect cannot interrupt it halfway.
            await asyncio.shield(self._store_in_background(message, trace))
        except Exception:
            failure = failure or InternalFailure()

        if failure is None:
            yield CompletedEvent(message=message, usage=state.usage)
        else:
            yield FailedEvent(error=failure, message_id=message_id)

    async def _generate(self, state: _ReplyState, question: str) -> AsyncGenerator[ReplyEvent]:
        """Retrieval (when grounded) and the model stream. Raises on failure."""
        history = [
            ChatTurn(role=m.role, content=strip_markers(m.content) if state.scope else m.content)
            for m in await self._repo.recent_complete_messages(state.conversation_id, self._history_limit)
        ]
        system_prompt = SYSTEM_PROMPT
        if state.scope is not None:
            assert self._rag is not None
            context = await self._rag.gather(question, document_ids=state.scope.document_ids)
            state.context = context
            yield RetrievalEvent(chunks=context.retrieved, sufficient=context.sufficient)
            if not context.sufficient:
                # Nothing relevant enough: answer with a fixed text; the model is not called.
                state.parts.append(INSUFFICIENT_EVIDENCE_ANSWER)
                yield TokenEvent(INSUFFICIENT_EVIDENCE_ANSWER)
                return
            system_prompt = grounded_system_prompt(SYSTEM_PROMPT, context)

        state.model_called = True
        async for chunk in state.model.stream(history, system_prompt=system_prompt):
            if isinstance(chunk, TextDelta):
                if chunk.text:
                    state.parts.append(chunk.text)
                    yield TokenEvent(chunk.text)
            elif isinstance(chunk, StreamEnd):
                state.usage = chunk.usage

    def _build(
        self, state: _ReplyState, status: MessageStatus, error_code: str | None
    ) -> tuple[Message, RetrievalTrace | None]:
        if status is not MessageStatus.COMPLETE and state.parts:
            status = MessageStatus.INCOMPLETE
        content = "" if status is MessageStatus.FAILED else "".join(state.parts)
        grounding, citations, trace = Grounding.NONE, tuple[Citation, ...](), None
        context = state.context
        if state.scope is not None:
            grounding = Grounding.INSUFFICIENT_EVIDENCE
            if context is not None and context.sufficient:
                answer = finalize_answer(content, context.evidence)
                content, citations, grounding = answer.content, answer.citations, answer.grounding
                trace = context.trace(answer.cited)
            elif context is not None:
                trace = context.trace()
        usage = state.usage
        message = Message(
            id=state.message_id,
            conversation_id=state.conversation_id,
            role=Role.ASSISTANT,
            content=content,
            status=status,
            error_code=error_code,
            model=state.model.model if state.model_called else None,
            created_at=self._clock(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            grounding=grounding,
            citations=citations,
        )
        logger.info(
            "chat.reply_finished",
            extra={
                **state.log_context(),
                "status": status.value,
                "error_code": error_code,
                "duration_ms": round((time.monotonic() - state.started) * 1000),
                "output_chars": len(message.content),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "grounding": grounding.value,
                "citations": len(citations),
            },
        )
        return message, trace

    def _store_in_background(self, message: Message, retrieval: RetrievalTrace | None = None) -> asyncio.Task[None]:
        async def store() -> None:
            try:
                await self._repo.add_message(message, retrieval=retrieval)
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
