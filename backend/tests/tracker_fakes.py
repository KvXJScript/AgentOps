"""Test doubles for the tracker ports and the proposal repository, plus builders."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from agentops.domain.proposals import ActionProposal, ProposalStatus
from agentops.domain.reports import Report, ReportRun, ReportSection, ReportStatus, initial_stages
from agentops.domain.retrieval import ChunkLocation, Citation
from agentops.domain.tracker import (
    TOOL_POLICIES,
    Issue,
    IssueDraft,
    IssuePage,
    McpUnavailable,
    TrackerStatus,
)
from agentops.infrastructure.mcp.tracker import McpTrackerConfig
from tests.report_fakes import T0

TESTS_DIR = Path(__file__).resolve().parent
FAULT_SERVER = TESTS_DIR / "mcp_fault_server.py"
ALLOWLIST = frozenset({"list_issues", "search_issues", "create_issue"})


def tracker_config(data_dir: Path, *, fault: str | None = None, **overrides: Any) -> McpTrackerConfig:
    """The real server (`python -m local_tracker_mcp`) by default, or the fault server with `fault`."""
    args = (str(FAULT_SERVER), fault) if fault is not None else ("-m", "local_tracker_mcp")
    values: dict[str, Any] = {
        "command": sys.executable,
        "args": args,
        "data_dir": data_dir,
        "allowlist": ALLOWLIST,
        "timeout_s": 5.0,
        "reconnect_interval_s": 0.0,
    }
    values.update(overrides)
    return McpTrackerConfig(**values)


def issue(n: int, title: str = "Some issue", body: str = "Body", labels: tuple[str, ...] = ("bug",)) -> Issue:
    return Issue(id=f"LT-{n}", title=title, body=body, labels=labels, status="open", created_at=T0)


def citation(marker: str, excerpt: str, *, title: str = "remote-work-policy.md", heading: str = "") -> Citation:
    return Citation(
        marker=marker,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=title,
        excerpt=excerpt,
        location=ChunkLocation(char_start=0, char_end=len(excerpt), heading_path=(heading,) if heading else ()),
    )


def succeeded_run(
    *,
    title: str = "Remote work policy",
    status: ReportStatus = ReportStatus.SUCCEEDED,
    run_id: UUID | None = None,
    references: tuple[Citation, ...] = (),
) -> ReportRun:
    report = Report(
        title=title,
        summary="Staff may work remotely two days a week [S1].",
        sections=(ReportSection(heading="Eligibility", body="All staff after probation [S1]."),),
        references=references,
    )
    return ReportRun(
        id=run_id or uuid.uuid4(),
        question="What does the handbook say about remote work?",
        document_ids=None,
        status=status,
        stages=initial_stages(),
        created_at=T0,
        updated_at=T0,
        report=report if status is ReportStatus.SUCCEEDED else None,
        model="scripted-1",
    )


def draft_reply(
    title: str = "Follow up: remote work equipment budget",
    body: str = "The report found staff may work remotely two days a week. Next: confirm the equipment budget.",
    labels: list[str] | None = None,
    **extra: Any,
) -> str:
    return json.dumps({"title": title, "body": body, "labels": ["follow-up"] if labels is None else labels, **extra})


class FakeIssueReader:
    def __init__(self, issues: list[Issue] | None = None, *, error: BaseException | None = None) -> None:
        self.issues = issues if issues is not None else [issue(1, "Remote work follow-up")]
        self.error = error
        self.connected = True
        self.searches: list[tuple[str, int]] = []

    async def status(self) -> TrackerStatus:
        return TrackerStatus(connected=self.connected, tools=tuple(TOOL_POLICIES.values()))

    async def list_issues(self, *, limit: int) -> IssuePage:
        if self.error is not None:
            raise self.error
        return IssuePage(items=tuple(self.issues[:limit]), truncated=False)

    async def search_issues(self, query: str, *, limit: int) -> IssuePage:
        self.searches.append((query, limit))
        if self.error is not None:
            raise self.error
        return IssuePage(items=tuple(self.issues[:limit]), truncated=False)


class CountingIssueWriter:
    """Records every create_issue call; idempotent by key like the real server."""

    def __init__(self, *, error: BaseException | None = None, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[IssueDraft, str]] = []
        self.error = error
        self.delay_s = delay_s
        self._by_key: dict[str, Issue] = {}

    async def create_issue(self, draft: IssueDraft, *, idempotency_key: str) -> Issue:
        self.calls.append((draft, idempotency_key))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        if idempotency_key not in self._by_key:
            self._by_key[idempotency_key] = Issue(
                id=f"LT-{100 + len(self._by_key)}",
                title=draft.title,
                body=draft.body,
                labels=draft.labels,
                status="open",
                created_at=T0,
            )
        return self._by_key[idempotency_key]


class InMemoryProposalRepository:
    """Same semantics as the SQL repository; each method is atomic (no await between check and set)."""

    def __init__(self) -> None:
        self.items: dict[UUID, ActionProposal] = {}

    async def create_pending(self, proposal: ActionProposal, *, now: datetime) -> tuple[ActionProposal, bool]:
        for pid, p in list(self.items.items()):
            if p.source_id == proposal.source_id and p.status is ProposalStatus.PENDING and p.expires_at <= now:
                self.items[pid] = replace(p, status=ProposalStatus.EXPIRED)
        existing = self._live_pending(proposal.source_id, now)
        if existing is not None:
            return existing, False
        self.items[proposal.id] = proposal
        return proposal, True

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        return self.items.get(proposal_id)

    async def find_live_pending(self, source_id: UUID, *, now: datetime) -> ActionProposal | None:
        return self._live_pending(source_id, now)

    async def list_recent(self, *, status: ProposalStatus | None, now: datetime, limit: int) -> list[ActionProposal]:
        items = sorted(self.items.values(), key=lambda p: p.created_at, reverse=True)
        if status is not None:
            items = [p for p in items if p.as_of(now).status is status]
        return items[:limit]

    async def claim_for_execution(self, proposal_id: UUID, *, payload_hash: str, now: datetime) -> bool:
        p = self.items.get(proposal_id)
        if p is None or p.status is not ProposalStatus.PENDING or p.payload_hash != payload_hash:
            return False
        if p.expires_at <= now:
            return False
        self.items[proposal_id] = replace(p, status=ProposalStatus.EXECUTING, decided_at=now)
        return True

    async def finish_execution(
        self,
        proposal_id: UUID,
        *,
        status: ProposalStatus,
        at: datetime,
        result_issue_id: str | None,
        error_code: str | None,
    ) -> ActionProposal | None:
        p = self.items.get(proposal_id)
        if p is None or p.status is not ProposalStatus.EXECUTING:
            return None
        self.items[proposal_id] = replace(
            p, status=status, decided_at=at, result_issue_id=result_issue_id, error_code=error_code
        )
        return self.items[proposal_id]

    async def reject(self, proposal_id: UUID, *, now: datetime) -> bool:
        p = self.items.get(proposal_id)
        if p is None or p.status is not ProposalStatus.PENDING or p.expires_at <= now:
            return False
        self.items[proposal_id] = replace(p, status=ProposalStatus.REJECTED, decided_at=now)
        return True

    async def mark_expired(self, proposal_id: UUID, *, now: datetime) -> None:
        p = self.items.get(proposal_id)
        if p is not None and p.status is ProposalStatus.PENDING and p.expires_at <= now:
            self.items[proposal_id] = replace(p, status=ProposalStatus.EXPIRED)

    async def list_executing(self) -> list[ActionProposal]:
        return [p for p in self.items.values() if p.status is ProposalStatus.EXECUTING]

    def _live_pending(self, source_id: UUID, now: datetime) -> ActionProposal | None:
        return next(
            (
                p
                for p in self.items.values()
                if p.source_id == source_id and p.status is ProposalStatus.PENDING and p.expires_at > now
            ),
            None,
        )


class SettableClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


UNAVAILABLE = McpUnavailable()
