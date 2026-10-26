from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from agentops.application.chat_service import ChatService
from agentops.application.documents.service import DocumentService
from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.application.rag import RagService
from agentops.application.reports.service import ReportService
from agentops.application.tracker import TrackerService
from agentops.domain.documents import Embedder, RetrievalUnavailable
from agentops.domain.ports import ChatModel
from agentops.domain.retrieval import Retriever


async def _no_cleanup() -> None:
    return None


@dataclass(slots=True)
class AppDependencies:
    """Everything the HTTP layer needs, built once per process in the lifespan."""

    chat_service: ChatService
    chat_model: ChatModel | None
    check_database: Callable[[], Awaitable[bool]]
    aclose: Callable[[], Awaitable[None]] = field(default=_no_cleanup)
    retriever: Retriever | None = None
    report_service: ReportService | None = None
    # Run once in the lifespan before serving (e.g. marking interrupted report runs).
    startup: Callable[[], Awaitable[None]] = field(default=_no_cleanup)
    # Phase 2 — documents and RAG. `retriever` above is the pgvector retriever; `rag_service` wraps it
    # with the RAG settings (top_k, min_score, context budget).
    embedder: Embedder | None = None
    document_service: DocumentService | None = None
    rag_service: RagService | None = None
    # Phase 4 — MCP tracker and approvals. Only `approval_service` holds the tracker's write port.
    tracker_service: TrackerService | None = None
    proposal_service: ProposalService | None = None
    approval_service: ApprovalService | None = None


def get_dependencies(request: Request) -> AppDependencies:
    deps: AppDependencies = request.app.state.dependencies
    return deps


def get_chat_service(deps: Annotated[AppDependencies, Depends(get_dependencies)]) -> ChatService:
    return deps.chat_service


def get_document_service(deps: Annotated[AppDependencies, Depends(get_dependencies)]) -> DocumentService:
    if deps.document_service is None:
        raise RetrievalUnavailable("Document storage is not available on this server.")
    return deps.document_service


def get_rag_service(deps: Annotated[AppDependencies, Depends(get_dependencies)]) -> RagService:
    if deps.rag_service is None:
        raise RetrievalUnavailable()
    return deps.rag_service


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
RagServiceDep = Annotated[RagService, Depends(get_rag_service)]
DependenciesDep = Annotated[AppDependencies, Depends(get_dependencies)]
