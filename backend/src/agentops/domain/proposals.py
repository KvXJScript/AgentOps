"""Action proposals: a drafted tracker write that waits for an explicit human decision."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agentops.domain.errors import DomainError
from agentops.domain.tracker import CREATE_ISSUE_TOOL, TRACKER_NAME, Issue, IssueDraft

# Fixed by server code, never chosen by a model.
PROPOSAL_TOOL = CREATE_ISSUE_TOOL
PROPOSAL_TARGET = TRACKER_NAME
SOURCE_REPORT = "report"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ActionProposal:
    id: UUID
    tool: str
    target: str
    arguments: IssueDraft
    summary: str
    payload_hash: str
    status: ProposalStatus
    source_type: str
    source_id: UUID
    related_issues: tuple[Issue, ...]  # untrusted, display only
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    result_issue_id: str | None = None
    error_code: str | None = None

    def as_of(self, now: datetime) -> ActionProposal:
        """A pending proposal past `expires_at` reads back as expired."""
        if self.status is ProposalStatus.PENDING and now >= self.expires_at:
            return replace(self, status=ProposalStatus.EXPIRED)
        return self


def canonical_payload(tool: str, target: str, arguments: IssueDraft) -> bytes:
    """Canonical JSON of {tool, target, arguments}: sorted keys, no whitespace, UTF-8."""
    payload = {
        "tool": tool,
        "target": target,
        "arguments": {"title": arguments.title, "body": arguments.body, "labels": list(arguments.labels)},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_hash(tool: str, target: str, arguments: IssueDraft) -> str:
    return hashlib.sha256(canonical_payload(tool, target, arguments)).hexdigest()


class ProposalNotFound(DomainError):
    code = "not_found"
    message = "Proposal not found"


class ReportNotReady(DomainError):
    code = "report_not_ready"
    message = "Only a succeeded report can be turned into a proposal."


class ProposalNotPending(DomainError):
    code = "proposal_not_pending"
    message = "The proposal is no longer pending."


class PayloadMismatch(DomainError):
    code = "payload_mismatch"
    message = "The proposal changed since it was reviewed; review it again."


class ProposalExpired(DomainError):
    code = "proposal_expired"
    message = "The proposal has expired; create a new one."


class ProposalRepository(Protocol):
    async def create_pending(self, proposal: ActionProposal, *, now: datetime) -> tuple[ActionProposal, bool]:
        """Store a pending proposal unless the source already has a live pending one.

        Returns (proposal, created). Pending rows of the same source past `expires_at` are first marked
        expired, so they never block a new proposal.
        """
        ...

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        """Raw stored state (callers apply `as_of`)."""
        ...

    async def find_live_pending(self, source_id: UUID, *, now: datetime) -> ActionProposal | None: ...

    async def list_recent(self, *, status: ProposalStatus | None, now: datetime, limit: int) -> list[ActionProposal]:
        """Newest first. `pending`/`expired` filters use `now` (expiry is evaluated, not only stored)."""
        ...

    async def claim_for_execution(self, proposal_id: UUID, *, payload_hash: str, now: datetime) -> bool:
        """Atomically pending → executing iff still pending, same hash and not expired."""
        ...

    async def finish_execution(
        self,
        proposal_id: UUID,
        *,
        status: ProposalStatus,
        at: datetime,
        result_issue_id: str | None,
        error_code: str | None,
    ) -> ActionProposal | None:
        """executing → executed/failed. Returns the stored proposal, or None if it was not executing."""
        ...

    async def reject(self, proposal_id: UUID, *, now: datetime) -> bool:
        """Atomically pending (not expired) → rejected."""
        ...

    async def mark_expired(self, proposal_id: UUID, *, now: datetime) -> None:
        """Persist the expiry of a pending proposal past `expires_at` (no-op otherwise)."""
        ...

    async def list_executing(self) -> list[ActionProposal]: ...
