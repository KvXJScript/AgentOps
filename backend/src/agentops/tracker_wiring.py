"""Wiring for Phase 4: the MCP tracker connection, its two ports, proposals and approvals.

The `IssueWriter` is constructed here and passed to `ApprovalService` only — that is what makes the
approval endpoint the single code path able to call `create_issue`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.application.tracker import TrackerService
from agentops.config import Settings
from agentops.domain.ports import ChatModel
from agentops.domain.tracker import TOOL_POLICIES
from agentops.infrastructure.db.proposals import SqlProposalRepository
from agentops.infrastructure.db.reports import SqlReportRunRepository
from agentops.infrastructure.mcp.tracker import McpIssueReader, McpIssueWriter, McpTrackerConfig, McpTrackerConnection

logger = logging.getLogger(__name__)

SERVER_MODULE = "local_tracker_mcp"


async def _noop() -> None:
    return None


@dataclass(frozen=True, slots=True)
class TrackerComponents:
    tracker_service: TrackerService
    proposal_service: ProposalService | None
    approval_service: ApprovalService | None
    startup: Callable[[], Awaitable[None]]
    aclose: Callable[[], Awaitable[None]]


def tracker_config(settings: Settings) -> McpTrackerConfig:
    return McpTrackerConfig(
        command=sys.executable,  # the backend's own interpreter, so the bundled server is importable
        args=("-m", SERVER_MODULE),
        data_dir=Path(settings.mcp_tracker_data_dir).expanduser().resolve(),
        allowlist=frozenset(settings.mcp_tracker_allowed_tools),
        timeout_s=settings.mcp_timeout_s,
        max_output_bytes=settings.mcp_max_output_bytes,
    )


def build_tracker_components(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    chat_model: ChatModel | None,
) -> TrackerComponents:
    allowlisted = tuple(p for name, p in TOOL_POLICIES.items() if name in settings.mcp_tracker_allowed_tools)
    if not settings.mcp_tracker_enabled:
        return TrackerComponents(TrackerService(None, allowlisted=allowlisted), None, None, _noop, _noop)

    connection = McpTrackerConnection(tracker_config(settings))
    repository = SqlProposalRepository(session_factory)
    proposals = ProposalService(
        repository,
        SqlReportRunRepository(session_factory),
        McpIssueReader(connection),
        chat_model,
        ttl_s=settings.proposal_ttl_s,
    )
    approvals = ApprovalService(repository, McpIssueWriter(connection))

    async def startup() -> None:
        await connection.start()  # never raises; status reports `unavailable` instead
        try:
            await approvals.recover_executing()
        except Exception:
            logger.exception("proposal.recovery_failed")

    return TrackerComponents(
        tracker_service=TrackerService(McpIssueReader(connection), allowlisted=allowlisted),
        proposal_service=proposals,
        approval_service=approvals,
        startup=startup,
        aclose=connection.aclose,
    )
