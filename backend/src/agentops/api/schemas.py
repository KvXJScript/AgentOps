from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

from agentops.domain.models import Conversation, Message, Usage


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def of(cls, c: Conversation) -> ConversationOut:
        return cls(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["complete", "incomplete", "failed"]
    error_code: str | None
    model: str | None
    created_at: Timestamp

    @classmethod
    def of(cls, m: Message) -> MessageOut:
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role.value,
            content=m.content,
            status=m.status.value,
            error_code=m.error_code,
            model=m.model,
            created_at=m.created_at,
        )


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ConversationListOut(BaseModel):
    items: list[ConversationOut]


class UsageOut(BaseModel):
    input_tokens: int | None
    output_tokens: int | None

    @classmethod
    def of(cls, u: Usage) -> UsageOut:
        return cls(input_tokens=u.input_tokens, output_tokens=u.output_tokens)


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyChecks(BaseModel):
    database: Literal["ok", "error"]
    llm: Literal["configured", "not_configured"]


class LLMInfo(BaseModel):
    provider: str
    model: str


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: ReadyChecks
    llm: LLMInfo | None = Field(default=None)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, object]] | None = None
    retry_after_s: float | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
