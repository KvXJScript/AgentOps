"""Documents, their chunks, and the ports ingestion and retrieval depend on."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agentops.domain.errors import DomainError
from agentops.domain.retrieval import ChunkLocation


class MediaType(StrEnum):
    MARKDOWN = "text/markdown"
    PLAIN = "text/plain"
    PDF = "application/pdf"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Document:
    id: UUID
    title: str
    filename: str
    media_type: MediaType
    size_bytes: int
    checksum_sha256: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    chunk_count: int = 0
    embedding_model: str | None = None
    deprecated: bool = False  # from Markdown front matter `status: deprecated`; demoted at retrieval


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """Raw text of one part of a document (one PDF page, or the whole text file)."""

    text: str
    page: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk produced by the chunker, before it has an id or an embedding.

    `text` is exactly `normalized_text[location.char_start:location.char_end]`.
    """

    ordinal: int
    text: str
    location: ChunkLocation


@dataclass(frozen=True, slots=True)
class Chunk:
    id: UUID
    document_id: UUID
    ordinal: int
    text: str
    location: ChunkLocation


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: Chunk
    embedding: Sequence[float]


# --- Errors -----------------------------------------------------------------------------------------------------


class DocumentNotFound(DomainError):
    code = "not_found"
    message = "Document not found"


class ChunkNotFound(DomainError):
    code = "not_found"
    message = "Chunk not found"


class IngestionError(DomainError):
    """A failure tied to the document's content. `message` is safe to show to end users."""


class UnsupportedFileType(IngestionError):
    code = "unsupported_file_type"
    message = "Only Markdown (.md), plain text (.txt) and PDF (.pdf) files are supported."


class FileTooLarge(IngestionError):
    code = "file_too_large"
    message = "The file is larger than the server allows."


class EmptyDocument(IngestionError):
    code = "empty_document"
    message = "The document contains no extractable text."


class CorruptDocument(IngestionError):
    code = "corrupt_document"
    message = "The document could not be read. It may be damaged or encrypted."


class EmbeddingFailed(IngestionError):
    code = "embedding_failed"
    message = "The embedding model failed to process the document."


class DocumentNotReady(DomainError):
    code = "document_not_ready"
    message = "The document is not in a state that allows this operation."


class RetrievalUnavailable(DomainError):
    """Document search was requested but the server has no retriever configured."""

    code = "retrieval_not_configured"
    message = "Document search is not available on this server."


# --- Ports ------------------------------------------------------------------------------------------------------


class TextExtractor(Protocol):
    """Parses original bytes into text. CPU-bound and synchronous: call it off the event loop.

    Raises `CorruptDocument` (unparseable/encrypted), `FileTooLarge` (over the page cap)
    or `UnsupportedFileType`.
    """

    def extract(self, content: bytes, media_type: MediaType) -> list[ExtractedSegment]: ...


class Embedder(Protocol):
    """Turns text into fixed-size vectors. Failures are raised as `EmbeddingFailed`."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class DocumentRepository(Protocol):
    async def add(self, document: Document, content: bytes) -> bool:
        """Insert a document with its original bytes. False if the checksum already exists."""
        ...

    async def get(self, document_id: UUID) -> Document | None: ...

    async def get_by_checksum(self, checksum_sha256: str) -> Document | None: ...

    async def list_recent(self, limit: int) -> list[Document]: ...

    async def get_content(self, document_id: UUID) -> bytes | None: ...

    async def mark(self, document_id: UUID, status: DocumentStatus, *, error_code: str | None, at: datetime) -> None:
        """Set status/error code (and drop any chunks, so a non-ready document is never searchable)."""
        ...

    async def save_chunks(
        self,
        document_id: UUID,
        chunks: Sequence[EmbeddedChunk],
        *,
        embedding_model: str,
        at: datetime,
        deprecated: bool = False,
    ) -> Document:
        """Atomically replace the document's chunks, set `deprecated` and mark it ready. Raises `DocumentNotFound`."""
        ...

    async def get_chunk(self, document_id: UUID, chunk_id: UUID) -> Chunk | None: ...

    async def delete(self, document_id: UUID) -> bool: ...
