from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from agentops.application.documents.pipeline import IngestionPipeline
from agentops.application.documents.validation import TITLE_MAX_CHARS, default_title, detect_media_type, safe_filename
from agentops.domain.documents import (
    Chunk,
    ChunkNotFound,
    Document,
    DocumentNotFound,
    DocumentNotReady,
    DocumentRepository,
    DocumentStatus,
    FileTooLarge,
)

logger = logging.getLogger(__name__)

LIST_LIMIT = 200


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    created: bool  # a new document was stored
    duplicate: bool  # identical content was already ingested; nothing changed


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        pipeline: IngestionPipeline,
        *,
        max_upload_bytes: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._pipeline = pipeline
        self._max_upload_bytes = max_upload_bytes
        self._clock = clock

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    async def upload(self, filename: str, content: bytes, *, title: str | None = None) -> UploadResult:
        """Validate, store and ingest an upload.

        Validation failures (type, size, empty, unreadable) raise before anything is
        stored. Failures after that leave a `failed` document that can be retried.
        """
        if len(content) > self._max_upload_bytes:
            raise FileTooLarge()
        media_type = detect_media_type(filename, content)
        checksum = hashlib.sha256(content).hexdigest()

        existing = await self._repo.get_by_checksum(checksum)
        if existing is not None:
            return await self._reupload(existing)

        prepared = await asyncio.to_thread(self._pipeline.prepare, content, media_type)
        now = self._clock()
        document = Document(
            id=uuid.uuid4(),
            title=(title or default_title(filename))[:TITLE_MAX_CHARS],
            filename=safe_filename(filename),
            media_type=media_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            status=DocumentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        if not await self._repo.add(document, content):
            # A concurrent upload of the same bytes won the insert.
            existing = await self._repo.get_by_checksum(checksum)
            if existing is None:
                raise DocumentNotFound()
            return await self._reupload(existing)
        logger.info(
            "documents.stored",
            extra={"document_id": str(document.id), "media_type": media_type.value, "size_bytes": len(content)},
        )
        ingested = await self._pipeline.ingest_prepared(document, prepared)
        return UploadResult(document=ingested, created=True, duplicate=False)

    async def _reupload(self, existing: Document) -> UploadResult:
        if existing.status is DocumentStatus.FAILED:
            logger.info("documents.reupload_retry", extra={"document_id": str(existing.id)})
            return UploadResult(document=await self._pipeline.run(existing.id), created=False, duplicate=False)
        logger.info("documents.duplicate", extra={"document_id": str(existing.id), "status": existing.status.value})
        return UploadResult(document=existing, created=False, duplicate=True)

    async def list_documents(self) -> list[Document]:
        return await self._repo.list_recent(LIST_LIMIT)

    async def get_document(self, document_id: UUID) -> Document:
        document = await self._repo.get(document_id)
        if document is None:
            raise DocumentNotFound()
        return document

    async def get_chunk(self, document_id: UUID, chunk_id: UUID) -> Chunk:
        chunk = await self._repo.get_chunk(document_id, chunk_id)
        if chunk is None:
            raise ChunkNotFound()
        return chunk

    async def retry(self, document_id: UUID) -> Document:
        document = await self.get_document(document_id)
        if document.status is not DocumentStatus.FAILED:
            raise DocumentNotReady("Only failed documents can be retried.")
        return await self._pipeline.run(document_id)

    async def delete(self, document_id: UUID) -> None:
        if not await self._repo.delete(document_id):
            raise DocumentNotFound()
        logger.info("documents.deleted", extra={"document_id": str(document_id)})
