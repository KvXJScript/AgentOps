from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from agentops.domain.retrieval import Citation

DEFAULT_CONVERSATION_TITLE = "New conversation"


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class Grounding(StrEnum):
    NONE = "none"  # plain chat
    GROUNDED = "grounded"  # at least one valid citation
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: UUID
    conversation_id: UUID
    role: Role
    content: str
    status: MessageStatus
    created_at: datetime
    error_code: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    grounding: Grounding = Grounding.NONE
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One entry of the history sent to a chat model."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class StreamEnd:
    usage: Usage


type ChatChunk = TextDelta | StreamEnd
