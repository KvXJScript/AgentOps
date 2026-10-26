from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

from agentops.application.rag import excerpt
from agentops.domain.documents import Chunk, Document
from agentops.domain.models import Conversation, Message, Usage
from agentops.domain.retrieval import ChunkLocation, Citation, RetrievalTrace, RetrievedChunk

MAX_DOCUMENT_IDS = 100


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[datetime, PlainSerializer(_iso_utc, return_type=str)]


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    use_documents: bool = False
    # Restricts retrieval to these documents (an empty list means none). Ignored without use_documents.
    document_ids: list[UUID] | None = Field(default=None, max_length=MAX_DOCUMENT_IDS)


class ChunkLocationOut(BaseModel):
    char_start: int
    char_end: int
    page: int | None
    heading_path: list[str]

    @classmethod
    def of(cls, location: ChunkLocation) -> ChunkLocationOut:
        return cls(
            char_start=location.char_start,
            char_end=location.char_end,
            page=location.page,
            heading_path=list(location.heading_path),
        )


class CitationOut(BaseModel):
    marker: str
    chunk_id: UUID
    document_id: UUID
    document_title: str
    excerpt: str
    location: ChunkLocationOut
    document_deleted: bool

    @classmethod
    def of(cls, c: Citation) -> CitationOut:
        return cls(
            marker=c.marker,
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            excerpt=c.excerpt,
            location=ChunkLocationOut.of(c.location),
            document_deleted=c.document_deleted,
        )


class RetrievedChunkOut(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    rank: int
    score: float
    excerpt: str
    location: ChunkLocationOut

    @classmethod
    def of(cls, c: RetrievedChunk) -> RetrievedChunkOut:
        return cls(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            rank=c.rank,
            score=round(c.score, 4),
            excerpt=excerpt(c.text),
            location=ChunkLocationOut.of(c.location),
        )


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
    grounding: Literal["none", "grounded", "insufficient_evidence"]
    citations: list[CitationOut]

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
            grounding=m.grounding.value,
            citations=[CitationOut.of(c) for c in m.citations],
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


class DocumentOut(BaseModel):
    id: UUID
    title: str
    filename: str
    media_type: Literal["text/markdown", "text/plain", "application/pdf"]
    size_bytes: int
    checksum_sha256: str
    status: Literal["pending", "processing", "ready", "failed"]
    error_code: str | None
    chunk_count: int
    embedding_model: str | None
    deprecated: bool
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def of(cls, d: Document) -> DocumentOut:
        return cls(
            id=d.id,
            title=d.title,
            filename=d.filename,
            media_type=d.media_type.value,
            size_bytes=d.size_bytes,
            checksum_sha256=d.checksum_sha256,
            status=d.status.value,
            error_code=d.error_code,
            chunk_count=d.chunk_count,
            embedding_model=d.embedding_model,
            deprecated=d.deprecated,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class DocumentListOut(BaseModel):
    items: list[DocumentOut]


class ChunkOut(BaseModel):
    id: UUID
    document_id: UUID
    ordinal: int
    text: str
    location: ChunkLocationOut

    @classmethod
    def of(cls, c: Chunk) -> ChunkOut:
        return cls(
            id=c.id, document_id=c.document_id, ordinal=c.ordinal, text=c.text, location=ChunkLocationOut.of(c.location)
        )


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    top_k: int | None = Field(default=None, ge=1, le=20)
    document_ids: list[UUID] | None = Field(default=None, max_length=MAX_DOCUMENT_IDS)


class SearchOut(BaseModel):
    items: list[RetrievedChunkOut]
    embedding_model: str


class MessageRetrievalOut(BaseModel):
    chunks: list[RetrievedChunkOut]
    cited_chunk_ids: list[UUID]
    top_k: int
    min_score: float
    embedding_model: str

    @classmethod
    def of(cls, t: RetrievalTrace) -> MessageRetrievalOut:
        return cls(
            chunks=[RetrievedChunkOut.of(c) for c in t.chunks],
            cited_chunk_ids=list(t.cited_chunk_ids),
            top_k=t.top_k,
            min_score=t.min_score,
            embedding_model=t.embedding_model,
        )


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
