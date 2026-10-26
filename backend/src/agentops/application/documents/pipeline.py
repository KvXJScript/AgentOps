"""The ingestion pipeline: extract → normalise → chunk → embed → persist → ready.

`IngestionPipeline.run(document_id)` is the single seam a background worker would
call later; today it runs synchronously inside the upload/retry request.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from agentops.application.documents.chunking import ChunkingConfig, chunk_document
from agentops.application.documents.front_matter import DocumentMetadata, split_front_matter
from agentops.application.documents.normalize import NormalizedText, normalize_segments
from agentops.domain.documents import (
    Chunk,
    ChunkDraft,
    Document,
    DocumentNotFound,
    DocumentRepository,
    DocumentStatus,
    EmbeddedChunk,
    Embedder,
    EmbeddingFailed,
    EmptyDocument,
    FileTooLarge,
    IngestionError,
    MediaType,
    TextExtractor,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def embedding_input(draft: ChunkDraft) -> str:
    """What the embedder sees: the chunk, prefixed with its nearest heading for context.

    Stored chunk text (and so citations and offsets) never includes the prefix.
    """
    path = draft.location.heading_path
    if not path or draft.text.lstrip("# ").startswith(path[-1]):
        return draft.text
    return f"{path[-1]}\n{draft.text}"


class IngestionPipeline:
    def __init__(
        self,
        repository: DocumentRepository,
        extractor: TextExtractor,
        embedder: Embedder,
        *,
        chunking: ChunkingConfig,
        max_document_chars: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._extractor = extractor
        self._embedder = embedder
        self._chunking = chunking
        self._max_chars = max_document_chars
        self._clock = clock

    def prepare(self, content: bytes, media_type: MediaType) -> NormalizedText:
        """Extract and normalise (CPU-bound; run off the event loop). Raises `IngestionError`."""
        normalized = normalize_segments(self._extractor.extract(content, media_type))
        if not normalized.text:
            raise EmptyDocument()
        if len(normalized.text) > self._max_chars:
            raise FileTooLarge(f"The document has more than {self._max_chars:,} characters of text.")
        return normalized

    async def run(self, document_id: UUID) -> Document:
        """(Re-)ingest a stored document from its original bytes. Never raises for content problems:
        the document ends up `ready` or `failed` with an error code."""
        document = await self._repo.get(document_id)
        content = await self._repo.get_content(document_id)
        if document is None or content is None:
            raise DocumentNotFound()
        await self._mark(document, DocumentStatus.PROCESSING, None)
        try:
            prepared = await asyncio.to_thread(self.prepare, content, document.media_type)
        except IngestionError as exc:
            return await self._fail(document, exc.code, step="extract")
        except Exception:
            logger.exception("ingestion.crashed", extra={"document_id": str(document.id), "step": "extract"})
            return await self._fail(document, "internal_error", step="extract")
        return await self._index(document, prepared)

    async def ingest_prepared(self, document: Document, prepared: NormalizedText) -> Document:
        """Continue a fresh upload whose text was already extracted during validation."""
        await self._mark(document, DocumentStatus.PROCESSING, None)
        return await self._index(document, prepared)

    async def _index(self, document: Document, prepared: NormalizedText) -> Document:
        log_ctx = {"document_id": str(document.id), "media_type": document.media_type.value}
        started = time.monotonic()
        step = "chunk"
        try:
            markdown = document.media_type is MediaType.MARKDOWN
            metadata, prepared = split_front_matter(prepared) if markdown else (DocumentMetadata(), prepared)
            drafts = chunk_document(prepared, markdown=markdown, config=self._chunking)
            if not drafts:
                raise EmptyDocument()
            step = "embed"
            vectors = await self._embedder.embed_documents([embedding_input(d) for d in drafts])
            if len(vectors) != len(drafts) or any(len(v) != self._embedder.dimensions for v in vectors):
                raise EmbeddingFailed()
            step = "persist"
            embedded = [
                EmbeddedChunk(
                    chunk=Chunk(
                        id=uuid.uuid4(),
                        document_id=document.id,
                        ordinal=d.ordinal,
                        text=d.text,
                        location=d.location,
                    ),
                    embedding=vector,
                )
                for d, vector in zip(drafts, vectors, strict=True)
            ]
            ready = await self._repo.save_chunks(
                document.id,
                embedded,
                embedding_model=self._embedder.model,
                at=self._clock(),
                deprecated=metadata.deprecated,
            )
        except DocumentNotFound:
            raise  # deleted while being ingested
        except IngestionError as exc:
            return await self._fail(document, exc.code, step=step)
        except Exception:
            logger.exception("ingestion.crashed", extra={**log_ctx, "step": step})
            return await self._fail(document, "internal_error", step=step)
        logger.info(
            "ingestion.ready",
            extra={
                **log_ctx,
                "chunk_count": ready.chunk_count,
                "deprecated": ready.deprecated,
                "text_chars": len(prepared.text),
                "embedding_model": self._embedder.model,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return ready

    async def _fail(self, document: Document, error_code: str, *, step: str) -> Document:
        logger.warning(
            "ingestion.failed", extra={"document_id": str(document.id), "step": step, "error_code": error_code}
        )
        return await self._mark(document, DocumentStatus.FAILED, error_code)

    async def _mark(self, document: Document, status: DocumentStatus, error_code: str | None) -> Document:
        await self._repo.mark(document.id, status, error_code=error_code, at=self._clock())
        updated = await self._repo.get(document.id)
        if updated is None:
            raise DocumentNotFound()
        return updated
