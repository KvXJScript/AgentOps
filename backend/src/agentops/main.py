from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentops.api.dependencies import AppDependencies
from agentops.api.errors import install_error_handlers
from agentops.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from agentops.api.routes import conversations, health
from agentops.application.chat_service import ChatService
from agentops.config import Settings
from agentops.infrastructure.db.repository import SqlConversationRepository
from agentops.infrastructure.db.session import create_engine, create_session_factory, ping
from agentops.infrastructure.llm.factory import build_chat_model
from agentops.infrastructure.logging import configure_logging

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def build_dependencies(settings: Settings) -> AppDependencies:
    engine = create_engine(settings.database_url)
    chat_model = build_chat_model(settings)
    service = ChatService(
        SqlConversationRepository(create_session_factory(engine)),
        chat_model,
        history_limit=settings.chat_history_messages,
    )

    async def check_database() -> bool:
        return await ping(engine)

    async def aclose() -> None:
        await service.wait_for_pending_writes()
        await engine.dispose()

    return AppDependencies(chat_service=service, chat_model=chat_model, check_database=check_database, aclose=aclose)


def create_app(settings: Settings | None = None, *, dependencies: AppDependencies | None = None) -> FastAPI:
    """Build the app. Tests pass `dependencies` to run without a database or provider."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if dependencies is None:
            configure_logging(settings.log_level)
            app.state.dependencies = build_dependencies(settings)
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

    app.add_middleware(RequestContextMiddleware)
    # Added last so it is outermost: error responses get CORS headers too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory agentops.main:app_factory`."""
    return create_app()
