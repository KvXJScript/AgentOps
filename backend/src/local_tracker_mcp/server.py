"""The MCP tool surface of `local-tracker`.

Tools: `list_issues`, `search_issues` (read), `create_issue` (write, idempotent by `idempotency_key`) and
`delete_all_issues` — a DECOY that is deliberately advertised but is not in the AgentOps allowlist, so
tests can prove the backend never exposes or calls a tool just because a server offers it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field, StringConstraints

from local_tracker_mcp import SERVER_NAME
from local_tracker_mcp.store import IssueStore, StoredIssue

LabelName = Literal["bug", "docs", "follow-up", "question"]
MAX_LIMIT = 50

Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Maximum number of issues to return (1-50).")]
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=120)]
Body = Annotated[str, StringConstraints(max_length=4000)]
Labels = Annotated[list[LabelName], Field(max_length=3)]
IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,128}$")]


class Issue(BaseModel):
    id: str
    title: str
    body: str
    labels: list[str]
    status: Literal["open", "closed"]
    created_at: str

    @classmethod
    def of(cls, issue: StoredIssue) -> Issue:
        return cls(
            id=issue.id,
            title=issue.title,
            body=issue.body,
            labels=list(issue.labels),
            status="closed" if issue.status == "closed" else "open",
            created_at=issue.created_at,
        )


class IssueList(BaseModel):
    items: list[Issue]
    total: int


class CreatedIssue(BaseModel):
    issue: Issue
    created: bool  # False when the idempotency key had already been used


class DeleteResult(BaseModel):
    deleted: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_server(store: IssueStore, *, clock: Callable[[], datetime] = _utcnow) -> MCPServer:
    server = MCPServer(
        SERVER_NAME,
        title="Local demo MCP server",
        instructions="Local demo issue tracker for AgentOps. Not a real external service.",
        version="1.0.0",
        log_level="WARNING",
    )

    @server.tool(description="List issues, newest first.")
    def list_issues(limit: Limit = 20) -> IssueList:
        page, total = store.list_issues(limit)
        return IssueList(items=[Issue.of(i) for i in page], total=total)

    @server.tool(description="Search issue titles and bodies by words (case-insensitive).")
    def search_issues(query: Query, limit: Limit = 20) -> IssueList:
        page, total = store.search_issues(query, limit)
        return IssueList(items=[Issue.of(i) for i in page], total=total)

    @server.tool(
        description="Create an issue. Reusing an idempotency_key returns the issue created with it, not a duplicate."
    )
    def create_issue(title: Title, body: Body, labels: Labels, idempotency_key: IdempotencyKey) -> CreatedIssue:
        unique_labels: list[str] = list(dict.fromkeys(labels))
        issue, created = store.create_issue(
            title=title, body=body, labels=unique_labels, idempotency_key=idempotency_key, now=clock()
        )
        return CreatedIssue(issue=Issue.of(issue), created=created)

    # DECOY: advertised on purpose, never allowlisted by AgentOps. It really deletes, so a test that
    # observes an unchanged issue count proves it was not called.
    @server.tool(description="Delete every issue in the tracker.")
    def delete_all_issues() -> DeleteResult:
        return DeleteResult(deleted=store.delete_all())

    return server
