"""Persistence for documents, chunks (pgvector) and the JSONB snapshots chat stores about them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    case,
    delete,
    false,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agentops.domain.documents import (
    Chunk,
    Document,
    DocumentNotFound,
    DocumentStatus,
    EmbeddedChunk,
    Embedder,
    MediaType,
)
from agentops.domain.ranking import RankingPolicy, contains_identifier, identifier_pattern, query_identifiers
from agentops.domain.retrieval import ChunkLocation, Citation, RetrievalTrace, RetrievedChunk
from agentops.infrastructure.db.models import Base

# The column type is fixed by migration 0002; changing models requires a new migration.
EMBEDDING_DIMENSIONS = 384


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    deprecated: Mapped[bool] = mapped_column(Boolean, server_default=false())  # migration 0006
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')", name="status"),
        CheckConstraint("media_type IN ('text/markdown', 'text/plain', 'application/pdf')", name="media_type"),
        Index("ix_documents_created_at", "created_at"),
    )


class DocumentSourceRow(Base):
    __tablename__ = "document_sources"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary)


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    heading_path: Mapped[list[str]] = mapped_column(JSONB)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(128))

    __table_args__ = (
        CheckConstraint("char_end > char_start", name="offsets"),
        UniqueConstraint("document_id", "ordinal"),
        # HNSW (vector_cosine_ops) index ix_document_chunks_embedding is created in migration 0002.
    )


class MessageRetrievalRow(Base):
    __tablename__ = "message_retrievals"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    top_k: Mapped[int] = mapped_column(Integer)
    min_score: Mapped[float] = mapped_column(Float)
    embedding_model: Mapped[str] = mapped_column(String(128))
    chunks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    cited_chunk_ids: Mapped[list[str]] = mapped_column(JSONB)


class SqlDocumentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(self, document: Document, content: bytes) -> bool:
        stmt = (
            pg_insert(DocumentRow)
            .values(
                id=document.id,
                title=document.title,
                filename=document.filename,
                media_type=document.media_type.value,
                size_bytes=document.size_bytes,
                checksum_sha256=document.checksum_sha256,
                status=document.status.value,
                error_code=document.error_code,
                chunk_count=document.chunk_count,
                embedding_model=document.embedding_model,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
            .on_conflict_do_nothing(index_elements=["checksum_sha256"])
            .returning(DocumentRow.id)
        )
        async with self._sessions.begin() as session:
            if (await session.execute(stmt)).scalar_one_or_none() is None:
                return False
            session.add(DocumentSourceRow(document_id=document.id, content=content))
        return True

    async def get(self, document_id: UUID) -> Document | None:
        async with self._sessions() as session:
            row = await session.get(DocumentRow, document_id)
            return _document(row) if row else None

    async def get_by_checksum(self, checksum_sha256: str) -> Document | None:
        stmt = select(DocumentRow).where(DocumentRow.checksum_sha256 == checksum_sha256)
        async with self._sessions() as session:
            row = (await session.scalars(stmt)).one_or_none()
            return _document(row) if row else None

    async def list_recent(self, limit: int) -> list[Document]:
        stmt = select(DocumentRow).order_by(DocumentRow.created_at.desc(), DocumentRow.id).limit(limit)
        async with self._sessions() as session:
            return [_document(row) for row in await session.scalars(stmt)]

    async def get_content(self, document_id: UUID) -> bytes | None:
        stmt = select(DocumentSourceRow.content).where(DocumentSourceRow.document_id == document_id)
        async with self._sessions() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def mark(self, document_id: UUID, status: DocumentStatus, *, error_code: str | None, at: datetime) -> None:
        values: dict[str, object] = {"status": status.value, "error_code": error_code, "updated_at": at}
        if status is not DocumentStatus.READY:
            values |= {"chunk_count": 0, "embedding_model": None}
        async with self._sessions.begin() as session:
            await session.execute(update(DocumentRow).where(DocumentRow.id == document_id).values(**values))
            if status is not DocumentStatus.READY:
                await session.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document_id))

    async def save_chunks(
        self,
        document_id: UUID,
        chunks: Sequence[EmbeddedChunk],
        *,
        embedding_model: str,
        at: datetime,
        deprecated: bool = False,
    ) -> Document:
        async with self._sessions.begin() as session:
            # Locks the document row, so a concurrent delete or re-run waits for this transaction.
            row = await session.get(DocumentRow, document_id, with_for_update=True)
            if row is None:
                raise DocumentNotFound()
            await session.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document_id))
            if chunks:
                await session.execute(
                    insert(DocumentChunkRow),
                    [
                        {
                            "id": item.chunk.id,
                            "document_id": document_id,
                            "ordinal": item.chunk.ordinal,
                            "text": item.chunk.text,
                            "char_start": item.chunk.location.char_start,
                            "char_end": item.chunk.location.char_end,
                            "page": item.chunk.location.page,
                            "heading_path": list(item.chunk.location.heading_path),
                            "embedding": list(item.embedding),
                            "embedding_model": embedding_model,
                        }
                        for item in chunks
                    ],
                )
            row.status = DocumentStatus.READY.value
            row.error_code = None
            row.chunk_count = len(chunks)
            row.embedding_model = embedding_model
            row.deprecated = deprecated
            row.updated_at = at
            await session.flush()
            return _document(row)

    async def get_chunk(self, document_id: UUID, chunk_id: UUID) -> Chunk | None:
        stmt = select(DocumentChunkRow).where(
            DocumentChunkRow.id == chunk_id, DocumentChunkRow.document_id == document_id
        )
        async with self._sessions() as session:
            row = (await session.scalars(stmt)).one_or_none()
            return _chunk(row) if row else None

    async def delete(self, document_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(DocumentRow).where(DocumentRow.id == document_id))
            return bool(result.rowcount)  # type: ignore[attr-defined]


class PgVectorRetriever:
    """Cosine-similarity search over chunks, with the data-scope rules in SQL.

    Only chunks of `ready` documents embedded with the current model are candidates
    (deleted documents have no chunks left), narrowed to `document_ids` when given.

    `ranking` may reorder candidates (identifier boost, deprecated penalty; see `agentops.domain.ranking`).
    Every candidate query carries the same scope filters, and `score` stays the cosine similarity. The
    candidate set is exact for the adjusted order, not a heuristic re-rank of the vector top-k:

    1. the vector top-k (HNSW, as without adjustments);
    2. only if (1) holds a deprecated chunk: the vector top-k of non-deprecated chunks, which may move up;
    3. only if the query has identifier-like tokens: the top-k (adjusted order) of chunks containing one.

    A query without identifiers whose top-k holds no deprecated chunk runs (1) alone and returns it unchanged.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        *,
        ranking: RankingPolicy | None = None,
    ) -> None:
        self._sessions = session_factory
        self._embedder = embedder
        self._ranking = ranking or RankingPolicy()

    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        policy = self._ranking
        vector = await self._embedder.embed_query(query)
        identifiers = query_identifiers(query) if policy.identifier_boost > 0 else ()
        pattern = identifier_pattern(identifiers) if identifiers else None
        distance = DocumentChunkRow.embedding.cosine_distance(vector)
        candidates = (
            select(DocumentChunkRow, DocumentRow.title, DocumentRow.deprecated, distance.label("distance"))
            .join(DocumentRow, DocumentRow.id == DocumentChunkRow.document_id)
            .where(
                DocumentRow.status == DocumentStatus.READY.value,
                DocumentChunkRow.embedding_model == self._embedder.model,
            )
            .limit(top_k)
        )
        if document_ids is not None:
            candidates = candidates.where(DocumentChunkRow.document_id.in_(list(document_ids)))
        async with self._sessions.begin() as session:
            # pgvector >= 0.8: keep scanning the HNSW index until enough rows pass the filters.
            await session.execute(_ITERATIVE_SCAN)
            rows = list((await session.execute(candidates.order_by(distance, DocumentChunkRow.id))).all())
            adjusted = pattern is not None
            if policy.deprecated_penalty > 0 and any(row.deprecated for row in rows):
                current = candidates.where(DocumentRow.deprecated.is_(False))
                rows += (await session.execute(current.order_by(distance, DocumentChunkRow.id))).all()
                adjusted = True
            if pattern is not None:
                penalty = case((DocumentRow.deprecated, policy.deprecated_penalty), else_=0.0)
                matching = candidates.where(DocumentChunkRow.text.regexp_match(pattern, flags="i"))
                rows += (await session.execute(matching.order_by(distance + penalty, DocumentChunkRow.id))).all()
        if adjusted:
            unique = {row[0].id: row for row in rows}.values()
            rows = sorted(
                unique,
                key=lambda row: (
                    -policy.rank_score(
                        1.0 - float(row.distance),
                        deprecated=row.deprecated,
                        identifier_match=pattern is not None and contains_identifier(row[0].text, pattern),
                    ),
                    row[0].id,
                ),
            )[:top_k]
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                rank=rank,
                score=1.0 - float(dist),
                text=chunk.text,
                location=_location(chunk),
            )
            for rank, (chunk, title, _, dist) in enumerate(rows, start=1)
        ]


_ITERATIVE_SCAN = text("SET LOCAL hnsw.iterative_scan = strict_order")


def _document(row: DocumentRow) -> Document:
    return Document(
        id=row.id,
        title=row.title,
        filename=row.filename,
        media_type=MediaType(row.media_type),
        size_bytes=row.size_bytes,
        checksum_sha256=row.checksum_sha256,
        status=DocumentStatus(row.status),
        error_code=row.error_code,
        chunk_count=row.chunk_count,
        embedding_model=row.embedding_model,
        deprecated=row.deprecated,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _location(row: DocumentChunkRow) -> ChunkLocation:
    return ChunkLocation(
        char_start=row.char_start, char_end=row.char_end, page=row.page, heading_path=tuple(row.heading_path)
    )


def _chunk(row: DocumentChunkRow) -> Chunk:
    return Chunk(id=row.id, document_id=row.document_id, ordinal=row.ordinal, text=row.text, location=_location(row))


# --- JSONB snapshots stored on messages --------------------------------------------------------------------------


def location_to_json(location: ChunkLocation) -> dict[str, Any]:
    return {
        "char_start": location.char_start,
        "char_end": location.char_end,
        "page": location.page,
        "heading_path": list(location.heading_path),
    }


def location_from_json(data: dict[str, Any]) -> ChunkLocation:
    return ChunkLocation(
        char_start=int(data["char_start"]),
        char_end=int(data["char_end"]),
        page=data.get("page"),
        heading_path=tuple(data.get("heading_path") or ()),
    )


def citation_to_json(citation: Citation) -> dict[str, Any]:
    return {
        "marker": citation.marker,
        "chunk_id": str(citation.chunk_id),
        "document_id": str(citation.document_id),
        "document_title": citation.document_title,
        "excerpt": citation.excerpt,
        "location": location_to_json(citation.location),
    }


def citation_from_json(data: dict[str, Any], *, document_deleted: bool) -> Citation:
    return Citation(
        marker=str(data["marker"]),
        chunk_id=UUID(data["chunk_id"]),
        document_id=UUID(data["document_id"]),
        document_title=str(data["document_title"]),
        excerpt=str(data["excerpt"]),
        location=location_from_json(data["location"]),
        document_deleted=document_deleted,
    )


def trace_to_row(message_id: UUID, trace: RetrievalTrace) -> MessageRetrievalRow:
    return MessageRetrievalRow(
        message_id=message_id,
        top_k=trace.top_k,
        min_score=trace.min_score,
        embedding_model=trace.embedding_model,
        chunks=[
            {
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "document_title": c.document_title,
                "rank": c.rank,
                "score": c.score,
                "excerpt": c.text,
                "location": location_to_json(c.location),
            }
            for c in trace.chunks
        ],
        cited_chunk_ids=[str(i) for i in trace.cited_chunk_ids],
    )


def trace_from_row(row: MessageRetrievalRow) -> RetrievalTrace:
    return RetrievalTrace(
        chunks=tuple(
            RetrievedChunk(
                chunk_id=UUID(c["chunk_id"]),
                document_id=UUID(c["document_id"]),
                document_title=str(c["document_title"]),
                rank=int(c["rank"]),
                score=float(c["score"]),
                text=str(c["excerpt"]),
                location=location_from_json(c["location"]),
            )
            for c in row.chunks
        ),
        cited_chunk_ids=tuple(UUID(i) for i in row.cited_chunk_ids),
        top_k=row.top_k,
        min_score=row.min_score,
        embedding_model=row.embedding_model,
    )


async def existing_chunk_ids(session: AsyncSession, chunk_ids: set[UUID]) -> set[UUID]:
    if not chunk_ids:
        return set()
    stmt = select(DocumentChunkRow.id).where(DocumentChunkRow.id.in_(list(chunk_ids)))
    return set(await session.scalars(stmt))
