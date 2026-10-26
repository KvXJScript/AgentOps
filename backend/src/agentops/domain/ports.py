from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol
from uuid import UUID

from agentops.domain.models import ChatChunk, ChatTurn, Conversation, Message
from agentops.domain.retrieval import RetrievalTrace


class ChatModel(Protocol):
    """A streaming chat model.

    `stream` yields zero or more `TextDelta` chunks followed by exactly one
    `StreamEnd`. Failures are raised as `LLMError` subclasses, possibly after
    some deltas have already been yielded.
    """

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]: ...


class ConversationRepository(Protocol):
    async def create(self, conversation: Conversation) -> None: ...

    async def get(self, conversation_id: UUID) -> Conversation | None: ...

    async def list_recent(self, limit: int) -> list[Conversation]: ...

    async def delete(self, conversation_id: UUID) -> bool: ...

    async def list_messages(self, conversation_id: UUID) -> list[Message]: ...

    async def recent_complete_messages(self, conversation_id: UUID, limit: int) -> list[Message]:
        """Last `limit` messages with status complete, oldest first."""
        ...

    async def add_message(
        self, message: Message, *, new_title: str | None = None, retrieval: RetrievalTrace | None = None
    ) -> None:
        """Insert a message (with its retrieval trace, if any), bump the conversation's `updated_at`
        (and optionally its title).

        Raises `ConversationNotFound` if the conversation no longer exists.
        """
        ...

    async def get_retrieval(self, message_id: UUID) -> RetrievalTrace | None: ...
