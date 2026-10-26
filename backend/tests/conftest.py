from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from agentops.api.dependencies import AppDependencies
from agentops.application.chat_service import ChatService
from agentops.config import Settings
from agentops.domain.ports import ChatModel
from agentops.main import create_app
from tests.fakes import InMemoryConversationRepository, ScriptedChatModel

_ENV_VARS = (
    "DATABASE_URL",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_TIMEOUT_S",
    "LLM_MAX_RETRIES",
    "LLM_MAX_OUTPUT_TOKENS",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CHAT_HISTORY_MESSAGES",
    "LOG_LEVEL",
    "CORS_ORIGINS",
    # Phase 2 — documents and RAG
    "MAX_UPLOAD_BYTES",
    "MAX_PDF_PAGES",
    "MAX_DOCUMENT_CHARS",
    "CHUNK_MAX_CHARS",
    "CHUNK_OVERLAP_CHARS",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_CACHE_DIR",
    "EMBEDDING_BATCH_SIZE",
    "RAG_TOP_K",
    "RETRIEVAL_MIN_SCORE",
    "RAG_CONTEXT_MAX_CHARS",
    # Phase 4 — MCP tracker and approvals
    "MCP_TRACKER_ENABLED",
    "MCP_TRACKER_DATA_DIR",
    "MCP_TRACKER_ALLOWED_TOOLS",
    "MCP_TIMEOUT_S",
    "MCP_MAX_OUTPUT_BYTES",
    "PROPOSAL_TTL_S",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never see the developer's shell env; `.env` is disabled via `_env_file=None`."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


class DatabaseProbe:
    def __init__(self) -> None:
        self.ok = True

    async def __call__(self) -> bool:
        return self.ok


@dataclass
class Harness:
    client: httpx.AsyncClient
    repo: InMemoryConversationRepository
    service: ChatService
    database: DatabaseProbe


@pytest.fixture
def repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


@pytest.fixture
def chat_model() -> ChatModel | None:
    """Override in a test module (or parametrize) to script the model."""
    return ScriptedChatModel()


@pytest.fixture
async def harness(repo: InMemoryConversationRepository, chat_model: ChatModel | None) -> AsyncIterator[Harness]:
    service = ChatService(repo, chat_model, history_limit=4)
    database = DatabaseProbe()
    deps = AppDependencies(chat_service=service, chat_model=chat_model, check_database=database)
    app = create_app(make_settings(), dependencies=deps)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield Harness(client=client, repo=repo, service=service, database=database)
    await service.wait_for_pending_writes()


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events
