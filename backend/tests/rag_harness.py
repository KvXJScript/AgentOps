"""An API harness with the Phase 2 services wired to in-memory fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from agentops.api.dependencies import AppDependencies
from agentops.application.chat_service import ChatService
from agentops.application.documents.chunking import ChunkingConfig
from agentops.application.documents.pipeline import IngestionPipeline
from agentops.application.documents.service import DocumentService
from agentops.application.rag import RagConfig, RagService
from agentops.domain.documents import Embedder
from agentops.domain.ports import ChatModel
from agentops.domain.retrieval import Retriever
from agentops.infrastructure.documents.extraction import DefaultTextExtractor
from agentops.infrastructure.embeddings.fake import FakeEmbedder
from agentops.main import create_app
from tests.conftest import DatabaseProbe, make_settings
from tests.document_fakes import InMemoryDocumentRepository, InMemoryRetriever
from tests.fakes import InMemoryConversationRepository, ScriptedChatModel


@dataclass
class RagHarness:
    client: httpx.AsyncClient
    conversations: InMemoryConversationRepository
    documents: InMemoryDocumentRepository
    chat: ChatService
    document_service: DocumentService
    retriever: Retriever
    embedder: Embedder
    model: ChatModel | None


@asynccontextmanager
async def rag_harness(
    *,
    chat_model: ChatModel | None = None,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    config: RagConfig | None = None,
    max_upload_bytes: int = 64 * 1024,
    chunking: ChunkingConfig | None = None,
) -> AsyncIterator[RagHarness]:
    conversations = InMemoryConversationRepository()
    documents = InMemoryDocumentRepository()
    conversations.alive_chunk_ids = documents.alive_chunk_ids
    embedder = embedder or FakeEmbedder()
    retriever = retriever or InMemoryRetriever(documents, embedder)
    model = chat_model if chat_model is not None else ScriptedChatModel()
    rag = RagService(retriever, embedding_model=embedder.model, config=config or RagConfig(min_score=0.2))
    pipeline = IngestionPipeline(
        documents,
        DefaultTextExtractor(max_pdf_pages=5),
        embedder,
        chunking=chunking or ChunkingConfig(max_chars=300, overlap_chars=40),
        max_document_chars=20_000,
    )
    document_service = DocumentService(documents, pipeline, max_upload_bytes=max_upload_bytes)
    chat = ChatService(conversations, model, history_limit=10, rag=rag)
    deps = AppDependencies(
        chat_service=chat,
        chat_model=model,
        check_database=DatabaseProbe(),
        retriever=retriever,
        embedder=embedder,
        document_service=document_service,
        rag_service=rag,
    )
    app = create_app(make_settings(), dependencies=deps)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield RagHarness(
            client=client,
            conversations=conversations,
            documents=documents,
            chat=chat,
            document_service=document_service,
            retriever=retriever,
            embedder=embedder,
            model=model,
        )
    await chat.wait_for_pending_writes()
