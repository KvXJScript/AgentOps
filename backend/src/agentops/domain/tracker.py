"""The issue tracker integration (MCP `local-tracker`): types, policy, errors and ports.

Reads and the one write are deliberately two ports. `IssueReader` is given to anything that needs to
look at issues (the integrations API, proposal drafting). `IssueWriter` — the only way to create an
issue — is constructed once and handed only to the approval use case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from agentops.domain.errors import DomainError

TRACKER_NAME = "local-tracker"
TRACKER_LABEL = "Local demo MCP server"
TRACKER_TRANSPORT = "stdio"

# Fixed vocabulary for issues created by AgentOps (the contract's label enum).
PROPOSAL_LABELS: tuple[str, ...] = ("bug", "docs", "follow-up", "question")
ISSUE_TITLE_MIN = 5
ISSUE_TITLE_MAX = 120
ISSUE_BODY_MAX = 4000
ISSUE_LABELS_MAX = 3
QUERY_MAX_CHARS = 200
LIMIT_MAX = 50


class ToolAccess(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    name: str
    access: ToolAccess
    requires_approval: bool
    description: str  # ours, not the server's: server-advertised text is untrusted


READ_TOOLS = ("list_issues", "search_issues")
CREATE_ISSUE_TOOL = "create_issue"

# Every tool AgentOps knows how to call. Config can narrow this (exact-name allowlist), never widen it.
TOOL_POLICIES: dict[str, ToolPolicy] = {
    "list_issues": ToolPolicy("list_issues", ToolAccess.READ, False, "List issues, newest first."),
    "search_issues": ToolPolicy("search_issues", ToolAccess.READ, False, "Search issue titles and bodies."),
    CREATE_ISSUE_TOOL: ToolPolicy(
        CREATE_ISSUE_TOOL,
        ToolAccess.WRITE,
        True,
        "Create an issue. Only ever executed by an explicit approval of a proposal.",
    ),
}


@dataclass(frozen=True, slots=True)
class Issue:
    id: str
    title: str
    body: str
    labels: tuple[str, ...]
    status: Literal["open", "closed"]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuePage:
    items: tuple[Issue, ...]
    truncated: bool  # the output size cap dropped trailing items


@dataclass(frozen=True, slots=True)
class IssueDraft:
    """Arguments of `create_issue` (without the idempotency key, which the approval supplies)."""

    title: str
    body: str
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrackerStatus:
    connected: bool
    tools: tuple[ToolPolicy, ...]  # the allowlisted tools, in policy order


class McpUnavailable(DomainError):
    code = "mcp_unavailable"
    message = "The local tracker (MCP server) is not available."


class McpTimeout(DomainError):
    code = "mcp_timeout"
    message = "The local tracker (MCP server) took too long to respond."


class InvalidToolOutput(DomainError):
    code = "invalid_tool_output"
    message = "The local tracker returned output that could not be used."


class InvalidToolArguments(DomainError):
    """Rejected before the call: the arguments break the tool's contract."""

    code = "validation_error"
    message = "The tool arguments are invalid."


class IssueReader(Protocol):
    async def status(self) -> TrackerStatus: ...

    async def list_issues(self, *, limit: int) -> IssuePage: ...

    async def search_issues(self, query: str, *, limit: int) -> IssuePage: ...


class IssueWriter(Protocol):
    """The single write capability. Only the approval use case receives one."""

    async def create_issue(self, draft: IssueDraft, *, idempotency_key: str) -> Issue: ...
