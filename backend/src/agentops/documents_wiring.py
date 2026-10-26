"""Composition root for Phase 2 (documents, ingestion, retrieval, RAG). Used by `main.build_dependencies`."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.application.documents.chunking import ChunkingConfig
from agentops.application.documents.pipeline import IngestionPipeline
from agentops.application.documents.service import DocumentService
from agentops.application.rag import RagConfig, RagService
from agentops.config import Settings
from agentops.domain.documents import Embedder
from agentops.infrastructure.db.documents import EMBEDDING_DIMENSIONS, PgVectorRetriever, SqlDocumentRepository
from agentops.infrastructure.documents.extraction import DefaultTextExtractor
from agentops.infrastructure.embeddings.factory import build_embedder


@dataclass(frozen=True, slots=True)
class DocumentComponents:
    embedder: Embedder
    retriever: PgVectorRetriever
    rag_service: RagService
    document_service: DocumentService


def build_document_components(
    settings: Settings, sessions: async_sessionmaker[AsyncSession], *, embedder: Embedder | None = None
) -> DocumentComponents:
    embedder = embedder or build_embedder(settings, required_dimensions=EMBEDDING_DIMENSIONS)
    repository = SqlDocumentRepository(sessions)
    retriever = PgVectorRetriever(sessions, embedder)
    pipeline = IngestionPipeline(
        repository,
        DefaultTextExtractor(max_pdf_pages=settings.max_pdf_pages),
        embedder,
        chunking=ChunkingConfig(max_chars=settings.chunk_max_chars, overlap_chars=settings.chunk_overlap_chars),
        max_document_chars=settings.max_document_chars,
    )
    return DocumentComponents(
        embedder=embedder,
        retriever=retriever,
        rag_service=RagService(
            retriever,
            embedding_model=embedder.model,
            config=RagConfig(
                top_k=settings.rag_top_k,
                min_score=settings.retrieval_min_score,
                context_max_chars=settings.rag_context_max_chars,
            ),
        ),
        document_service=DocumentService(repository, pipeline, max_upload_bytes=settings.max_upload_bytes),
    )
