from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentops.api.dependencies import AppDependencies
from agentops.api.errors import install_error_handlers
from agentops.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from agentops.api.routes import conversations, documents, health, integrations, proposals, reports, retrieval
from agentops.application.chat_service import ChatService
from agentops.application.reports.service import ReportService, ReportSettings
from agentops.config import Settings
from agentops.documents_wiring import build_document_components
from agentops.domain.retrieval import Retriever
from agentops.infrastructure.db.reports import SqlReportRunRepository
from agentops.infrastructure.db.repository import SqlConversationRepository
from agentops.infrastructure.db.session import create_engine, create_session_factory, ping
from agentops.infrastructure.llm.factory import build_chat_model
from agentops.infrastructure.logging import configure_logging
from agentops.tracker_wiring import build_tracker_components

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def build_dependencies(settings: Settings) -> AppDependencies:
    engine = create_engine(settings.database_url)
    chat_model = build_chat_model(settings)
    docs = build_document_components(settings, create_session_factory(engine))
    service = ChatService(
        SqlConversationRepository(create_session_factory(engine)),
        chat_model,
        history_limit=settings.chat_history_messages,
        rag=docs.rag_service,
    )

    retriever: Retriever | None = docs.retriever
    report_service = ReportService(
        SqlReportRunRepository(create_session_factory(engine)),
        retriever=retriever,
        chat_model=chat_model,
        settings=ReportSettings(
            timeout_s=settings.report_timeout_s,
            top_k=settings.report_top_k,
            min_score=settings.retrieval_min_score,  # same relevance bar as RAG chat
            max_evidence_chars=settings.report_max_evidence_chars,
            max_concurrent_runs=settings.report_max_concurrent_runs,
        ),
    )

    tracker = build_tracker_components(settings, create_session_factory(engine), chat_model)

    async def check_database() -> bool:
        return await ping(engine)

    async def startup() -> None:
        try:
            await report_service.recover_interrupted()
        except Exception:
            # The app must still start with the database down; /ready reports it.
            logger.exception("report.recovery_failed")
        await tracker.startup()  # never fails the app: the tracker reports `unavailable` instead

    async def aclose() -> None:
        await report_service.aclose()
        await tracker.aclose()
        await service.wait_for_pending_writes()
        await engine.dispose()

    return AppDependencies(
        chat_service=service,
        chat_model=chat_model,
        check_database=check_database,
        aclose=aclose,
        retriever=retriever,
        report_service=report_service,
        startup=startup,
        embedder=docs.embedder,
        document_service=docs.document_service,
        rag_service=docs.rag_service,
        tracker_service=tracker.tracker_service,
        proposal_service=tracker.proposal_service,
        approval_service=tracker.approval_service,
    )


def create_app(settings: Settings | None = None, *, dependencies: AppDependencies | None = None) -> FastAPI:
    """Build the app. Tests pass `dependencies` to run without a database or provider."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if dependencies is None:
            configure_logging(settings.log_level)
            app.state.dependencies = build_dependencies(settings)
        await app.state.dependencies.startup()
        logger.info("app.started", extra={"provider": settings.llm.provider, "model": settings.llm.model})
        try:
            yield
        finally:
            await app.state.dependencies.aclose()

    app = FastAPI(title="AgentOps API", version="0.1.0", lifespan=lifespan)
    if dependencies is not None:
        app.state.dependencies = dependencies

    install_error_handlers(app)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(conversations.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(retrieval.router, prefix=API_PREFIX)
    app.include_router(reports.router, prefix=API_PREFIX)
    app.include_router(integrations.router, prefix=API_PREFIX)
    app.include_router(proposals.router, prefix=API_PREFIX)

    app.add_middleware(RequestContextMiddleware)
    # Added last so it is outermost: error responses get CORS headers too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, documents.DUPLICATE_HEADER],
    )
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory agentops.main:app_factory`."""
    return create_app()
