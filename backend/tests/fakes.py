"""Test doubles for the domain ports."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from uuid import UUID

from agentops.domain.errors import ConversationNotFound
from agentops.domain.models import (
    ChatChunk,
    ChatTurn,
    Conversation,
    Message,
    MessageStatus,
    StreamEnd,
    TextDelta,
    Usage,
)
from agentops.domain.retrieval import RetrievalTrace

type ScriptStep = str | BaseException | Usage | float


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self.conversations: dict[UUID, Conversation] = {}
        self.messages: list[Message] = []
        self.retrievals: dict[UUID, RetrievalTrace] = {}
        self.fail_add_message = False
        # Set to the document fake's chunk-id view to compute `document_deleted` on read.
        self.alive_chunk_ids: Callable[[], set[UUID]] | None = None

    async def create(self, conversation: Conversation) -> None:
        self.conversations[conversation.id] = conversation

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return self.conversations.get(conversation_id)

    async def list_recent(self, limit: int) -> list[Conversation]:
        return sorted(self.conversations.values(), key=lambda c: c.updated_at, reverse=True)[:limit]

    async def delete(self, conversation_id: UUID) -> bool:
        if self.conversations.pop(conversation_id, None) is None:
            return False
        self.messages = [m for m in self.messages if m.conversation_id != conversation_id]
        return True

    async def list_messages(self, conversation_id: UUID) -> list[Message]:
        messages = [m for m in self.messages if m.conversation_id == conversation_id]
        if self.alive_chunk_ids is None:
            return messages
        alive = self.alive_chunk_ids()
        return [
            replace(m, citations=tuple(replace(c, document_deleted=c.chunk_id not in alive) for c in m.citations))
            for m in messages
        ]

    async def recent_complete_messages(self, conversation_id: UUID, limit: int) -> list[Message]:
        complete = [m for m in await self.list_messages(conversation_id) if m.status is MessageStatus.COMPLETE]
        return complete[-limit:]

    async def add_message(
        self, message: Message, *, new_title: str | None = None, retrieval: RetrievalTrace | None = None
    ) -> None:
        if self.fail_add_message:
            raise RuntimeError("database is down")
        conversation = self.conversations.get(message.conversation_id)
        if conversation is None:
            raise ConversationNotFound()
        self.conversations[conversation.id] = replace(
            conversation, updated_at=message.created_at, title=new_title or conversation.title
        )
        self.messages.append(message)
        if retrieval is not None:
            self.retrievals[message.id] = retrieval

    async def get_retrieval(self, message_id: UUID) -> RetrievalTrace | None:
        return self.retrievals.get(message_id)


class ScriptedChatModel:
    """Each call to `stream` consumes the next script.

    Script steps: `str` -> TextDelta, `Usage` -> StreamEnd (ends the stream),
    exception -> raised, `float` -> sleep that many seconds.
    A script without a `Usage` step ends with an empty `StreamEnd`.
    """

    provider = "scripted"
    model = "scripted-1"

    def __init__(self, *scripts: list[ScriptStep]) -> None:
        self._scripts = list(scripts)
        self.calls: list[tuple[list[ChatTurn], str]] = []

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        self.calls.append((list(history), system_prompt))
        script = self._scripts.pop(0) if self._scripts else ["ok"]
        for step in script:
            if isinstance(step, BaseException):
                raise step
            if isinstance(step, Usage):
                yield StreamEnd(step)
                return
            if isinstance(step, float):
                await asyncio.sleep(step)
                continue
            yield TextDelta(step)
        yield StreamEnd(Usage())
