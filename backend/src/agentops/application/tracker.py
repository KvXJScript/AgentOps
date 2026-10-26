"""Read-only access to the tracker for the integrations API."""

from __future__ import annotations

from agentops.domain.tracker import IssuePage, IssueReader, McpUnavailable, ToolPolicy, TrackerStatus


class TrackerService:
    def __init__(self, reader: IssueReader | None, *, allowlisted: tuple[ToolPolicy, ...] = ()) -> None:
        self._reader = reader
        self._allowlisted = allowlisted

    async def status(self) -> TrackerStatus:
        if self._reader is None:
            return TrackerStatus(connected=False, tools=self._allowlisted)
        return await self._reader.status()

    async def issues(self, query: str | None, *, limit: int) -> IssuePage:
        if self._reader is None:
            raise McpUnavailable()
        if query is not None and query.strip():
            return await self._reader.search_issues(query, limit=limit)
        return await self._reader.list_issues(limit=limit)
