from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.application.documents.chunking import ChunkingConfig
from agentops.application.documents.pipeline import IngestionPipeline
from agentops.application.documents.service import DocumentService
from agentops.domain.documents import Chunk, Document, DocumentStatus, EmbeddedChunk, MediaType
from agentops.domain.models import Conversation, Grounding, Message, MessageStatus, Role
from agentops.domain.retrieval import ChunkLocation, Citation, RetrievalTrace, RetrievedChunk
from agentops.infrastructure.db.documents import PgVectorRetriever, SqlDocumentRepository
from agentops.infrastructure.db.repository import SqlConversationRepository
from agentops.infrastructure.db.session import create_engine
from agentops.infrastructure.documents.extraction import DefaultTextExtractor
from agentops.infrastructure.embeddings.fake import FakeEmbedder
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def docs(session_factory: async_sessionmaker[AsyncSession]) -> SqlDocumentRepository:
    return SqlDocumentRepository(session_factory)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def _document(checksum: str | None = None, *, title: str = "Handbook") -> Document:
    return Document(
        id=uuid.uuid4(),
        title=title,
        filename=f"{title.lower()}.md",
        media_type=MediaType.MARKDOWN,
        size_bytes=10,
        checksum_sha256=checksum or uuid.uuid4().hex * 2,
        status=DocumentStatus.PENDING,
        created_at=T0,
        updated_at=T0,
    )


async def _index(
    docs: SqlDocumentRepository,
    embedder: FakeEmbedder,
    document: Document,
    texts: list[str],
    *,
    model: str | None = None,
) -> list[Chunk]:
    await docs.add(document, b"source bytes")
    chunks = [
        Chunk(
            id=uuid.uuid4(),
            document_id=document.id,
            ordinal=i,
            text=t,
            location=ChunkLocation(char_start=i * 100, char_end=i * 100 + len(t), heading_path=("Guide", f"Part {i}")),
        )
        for i, t in enumerate(texts)
    ]
    vectors = await embedder.embed_documents(texts)
    await docs.save_chunks(
        document.id,
        [EmbeddedChunk(chunk=c, embedding=v) for c, v in zip(chunks, vectors, strict=True)],
        embedding_model=model or embedder.model,
        at=T0 + timedelta(seconds=1),
    )
    return chunks


async def test_schema_has_documents_tables_and_hnsw_index(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        tables = set(await session.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
        index = await session.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding'")
        )
        columns = set(
            await session.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'messages'")
            )
        )
    assert {"documents", "document_sources", "document_chunks", "message_retrievals"} <= tables
    assert index is not None
    assert "hnsw" in index
    assert "vector_cosine_ops" in index
    assert {"grounding", "citations"} <= columns


async def test_add_is_idempotent_on_checksum_and_keeps_source(docs: SqlDocumentRepository) -> None:
    first = _document("a" * 64)

    assert await docs.add(first, b"original bytes") is True
    assert await docs.add(_document("a" * 64), b"other") is False
    assert await docs.get(first.id) == first
    assert await docs.get_by_checksum("a" * 64) == first
    assert await docs.get_content(first.id) == b"original bytes"
    assert await docs.get_by_checksum("b" * 64) is None


async def test_save_chunks_marks_ready_and_round_trips(docs: SqlDocumentRepository, embedder: FakeEmbedder) -> None:
    document = _document()
    chunks = await _index(docs, embedder, document, ["alpha beta", "gamma delta"])

    stored = await docs.get(document.id)
    assert stored is not None
    assert (stored.status, stored.chunk_count, stored.embedding_model) == (DocumentStatus.READY, 2, embedder.model)
    assert await docs.get_chunk(document.id, chunks[1].id) == chunks[1]
    assert await docs.get_chunk(uuid.uuid4(), chunks[1].id) is None  # chunk must belong to the document


async def test_marking_failed_drops_chunks(docs: SqlDocumentRepository, embedder: FakeEmbedder) -> None:
    document = _document()
    chunks = await _index(docs, embedder, document, ["alpha"])

    await docs.mark(document.id, DocumentStatus.FAILED, error_code="embedding_failed", at=T0)

    stored = await docs.get(document.id)
    assert stored is not None
    assert (stored.status, stored.error_code, stored.chunk_count) == (DocumentStatus.FAILED, "embedding_failed", 0)
    assert await docs.get_chunk(document.id, chunks[0].id) is None


async def test_delete_cascades_chunks_and_source(
    docs: SqlDocumentRepository, embedder: FakeEmbedder, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document = _document()
    await _index(docs, embedder, document, ["alpha", "beta"])

    assert await docs.delete(document.id) is True
    assert await docs.delete(document.id) is False
    async with session_factory() as session:
        remaining = await session.scalar(text("SELECT count(*) FROM document_chunks"))
        sources = await session.scalar(text("SELECT count(*) FROM document_sources"))
    assert (remaining, sources) == (0, 0)


async def test_pgvector_search_ranks_and_applies_scope_in_sql(
    docs: SqlDocumentRepository, embedder: FakeEmbedder, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    handbook = _document(title="Handbook")
    other = _document(title="Other")
    pending = _document(title="Pending")
    stale = _document(title="Stale")
    await _index(docs, embedder, handbook, ["vacation days per year policy", "office parking rules"])
    await _index(docs, embedder, other, ["vacation days carry over"])
    await _index(docs, embedder, stale, ["vacation days per year policy"], model="older-model")
    await docs.add(pending, b"x")
    retriever = PgVectorRetriever(session_factory, embedder)

    results = await retriever.search("vacation days per year", top_k=5)

    assert [r.document_title for r in results[:2]] == ["Handbook", "Other"]
    assert [r.rank for r in results] == list(range(1, len(results) + 1))
    assert results[0].score == pytest.approx(1.0 - 0.0, abs=0.3)
    assert results[0].score > results[1].score
    assert results[0].location.heading_path == ("Guide", "Part 0")
    assert "Stale" not in {r.document_title for r in results}  # other embedding model never mixes in

    scoped = await retriever.search("vacation days", top_k=5, document_ids=[other.id])
    assert {r.document_id for r in scoped} == {other.id}
    assert await retriever.search("vacation", top_k=5, document_ids=[]) == []
    assert await retriever.search("vacation", top_k=5, document_ids=[pending.id]) == []

    await docs.mark(handbook.id, DocumentStatus.FAILED, error_code="internal_error", at=T0)
    assert handbook.id not in {r.document_id for r in await retriever.search("vacation days", top_k=5)}


async def test_citations_and_trace_survive_document_deletion(
    docs: SqlDocumentRepository, embedder: FakeEmbedder, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document = _document()
    [chunk] = await _index(docs, embedder, document, ["alpha beta"])
    conversations = SqlConversationRepository(session_factory)
    conv = Conversation(id=uuid.uuid4(), title="c", created_at=T0, updated_at=T0)
    await conversations.create(conv)
    citation = Citation(
        marker="S1",
        chunk_id=chunk.id,
        document_id=document.id,
        document_title=document.title,
        excerpt="alpha beta",
        location=chunk.location,
    )
    message = Message(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        role=Role.ASSISTANT,
        content="Alpha [S1].",
        status=MessageStatus.COMPLETE,
        created_at=T0,
        model="m",
        grounding=Grounding.GROUNDED,
        citations=(citation,),
    )
    trace = RetrievalTrace(
        chunks=(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title,
                rank=1,
                score=0.9,
                text="alpha beta",
                location=chunk.location,
            ),
        ),
        cited_chunk_ids=(chunk.id,),
        top_k=6,
        min_score=0.35,
        embedding_model=embedder.model,
    )
    await conversations.add_message(message, retrieval=trace)

    assert await conversations.list_messages(conv.id) == [message]
    assert await conversations.get_retrieval(message.id) == trace
    assert await conversations.get_retrieval(uuid.uuid4()) is None

    await docs.delete(document.id)

    [after] = await conversations.list_messages(conv.id)
    assert after.citations[0].document_deleted is True
    assert after.citations[0].excerpt == "alpha beta"
    assert await conversations.get_retrieval(message.id) == trace


async def test_ingestion_service_end_to_end_against_postgres(
    docs: SqlDocumentRepository, embedder: FakeEmbedder, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pipeline = IngestionPipeline(
        docs,
        DefaultTextExtractor(max_pdf_pages=10),
        embedder,
        chunking=ChunkingConfig(max_chars=200, overlap_chars=30),
        max_document_chars=100_000,
    )
    service = DocumentService(docs, pipeline, max_upload_bytes=1_000_000)
    body = "# Leave\n\n## Vacation\n\nEmployees get 26 vacation days.\n\n## Sick leave\n\nSick leave needs a note.\n"

    first = await service.upload("policy.md", body.encode())
    again = await service.upload("copy.md", body.encode())
    results = await PgVectorRetriever(session_factory, embedder).search("how many vacation days", top_k=3)

    assert first.created
    assert first.document.status is DocumentStatus.READY
    assert first.document.chunk_count == 2
    assert again.duplicate
    assert again.document.id == first.document.id
    assert results[0].location.heading_path == ("Leave", "Vacation")
    assert "26 vacation days" in results[0].text


def test_migration_0002_downgrades_and_upgrades_cleanly(database_url: str) -> None:
    """Round trip on a throwaway database so it never disturbs the shared test schema."""
    url = make_url(database_url).set(database=f"{make_url(database_url).database}_migrations")
    rendered = url.render_as_string(hide_password=False)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)

    async def recreate() -> None:
        conn = await asyncpg.connect(admin, timeout=2)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{url.database}"')
        finally:
            await conn.close()

    async def tables() -> set[str]:
        engine = create_engine(rendered)
        async with engine.connect() as conn:
            names = set(
                (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))).scalars()
            )
        await engine.dispose()
        return names

    asyncio.run(recreate())
    config = alembic_config(rendered)
    command.upgrade(config, "0002")
    assert "document_chunks" in asyncio.run(tables())
    command.downgrade(config, "0001")
    assert not {"documents", "document_chunks", "document_sources", "message_retrievals"} & asyncio.run(tables())
    command.upgrade(config, "head")
    assert "document_chunks" in asyncio.run(tables())
