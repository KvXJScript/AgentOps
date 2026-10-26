"""Persistence for action proposals.

State changes that matter for safety are single conditional UPDATEs, so concurrent requests cannot both
win: `pending → executing` (approve), `pending → rejected`, `executing → executed/failed`. At most one
`pending` row per source is enforced by a partial unique index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, CursorResult, DateTime, Index, String, Text, and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agentops.domain.proposals import (
    ActionProposal,
    GroundingIssue,
    GroundingReason,
    ProposalGrounding,
    ProposalStatus,
)
from agentops.domain.tracker import Issue, IssueDraft
from agentops.infrastructure.db.models import Base

_STATUSES = ", ".join(f"'{s.value}'" for s in ProposalStatus)
_PENDING = ProposalStatus.PENDING.value
PENDING_PER_SOURCE_INDEX = "uq_action_proposals_pending_source"


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tool: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(16))
    # A snapshot, not a foreign key: proposals outlive (and never block cleanup of) report runs.
    source_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    related_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_issue_id: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    # Advisory grounding-check result; NULL for proposals drafted before the check existed. Not hashed.
    grounding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("status <> 'executed' OR result_issue_id IS NOT NULL", name="executed_has_result"),
        Index("ix_action_proposals_created_at", "created_at"),
        Index("ix_action_proposals_status", "status"),
        Index(PENDING_PER_SOURCE_INDEX, "source_id", unique=True, postgresql_where=text("status = 'pending'")),
    )


class SqlProposalRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_pending(self, proposal: ActionProposal, *, now: datetime) -> tuple[ActionProposal, bool]:
        try:
            async with self._sessions.begin() as session:
                # Expired-but-still-pending rows must not hold the "one pending per source" slot.
                await session.execute(
                    update(ActionProposalRow)
                    .where(
                        ActionProposalRow.source_id == proposal.source_id,
                        ActionProposalRow.status == _PENDING,
                        ActionProposalRow.expires_at <= now,
                    )
                    .values(status=ProposalStatus.EXPIRED.value)
                )
                session.add(_row(proposal))
        except IntegrityError as exc:
            if PENDING_PER_SOURCE_INDEX not in str(exc.orig):
                raise
            existing = await self.find_live_pending(proposal.source_id, now=now)
            if existing is None:  # pragma: no cover - the winner expired within the same instant
                raise
            return existing, False
        return proposal, True

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        async with self._sessions() as session:
            row = await session.get(ActionProposalRow, proposal_id)
            return _proposal(row) if row is not None else None

    async def find_live_pending(self, source_id: UUID, *, now: datetime) -> ActionProposal | None:
        stmt = select(ActionProposalRow).where(
            ActionProposalRow.source_id == source_id,
            ActionProposalRow.status == _PENDING,
            ActionProposalRow.expires_at > now,
        )
        async with self._sessions() as session:
            row = (await session.scalars(stmt)).first()
            return _proposal(row) if row is not None else None

    async def list_recent(self, *, status: ProposalStatus | None, now: datetime, limit: int) -> list[ActionProposal]:
        stmt = select(ActionProposalRow)
        if status is ProposalStatus.PENDING:
            stmt = stmt.where(ActionProposalRow.status == _PENDING, ActionProposalRow.expires_at > now)
        elif status is ProposalStatus.EXPIRED:
            stmt = stmt.where(
                or_(
                    ActionProposalRow.status == ProposalStatus.EXPIRED.value,
                    and_(ActionProposalRow.status == _PENDING, ActionProposalRow.expires_at <= now),
                )
            )
        elif status is not None:
            stmt = stmt.where(ActionProposalRow.status == status.value)
        stmt = stmt.order_by(ActionProposalRow.created_at.desc(), ActionProposalRow.id).limit(limit)
        async with self._sessions() as session:
            return [_proposal(row) for row in await session.scalars(stmt)]

    async def claim_for_execution(self, proposal_id: UUID, *, payload_hash: str, now: datetime) -> bool:
        stmt = (
            update(ActionProposalRow)
            .where(
                ActionProposalRow.id == proposal_id,
                ActionProposalRow.status == _PENDING,
                ActionProposalRow.payload_hash == payload_hash,
                ActionProposalRow.expires_at > now,
            )
            .values(status=ProposalStatus.EXECUTING.value, decided_at=now)
        )
        return await self._changed(stmt)

    async def finish_execution(
        self,
        proposal_id: UUID,
        *,
        status: ProposalStatus,
        at: datetime,
        result_issue_id: str | None,
        error_code: str | None,
    ) -> ActionProposal | None:
        if status not in (ProposalStatus.EXECUTED, ProposalStatus.FAILED):
            raise ValueError("an execution finishes as executed or failed")
        stmt = (
            update(ActionProposalRow)
            .where(ActionProposalRow.id == proposal_id, ActionProposalRow.status == ProposalStatus.EXECUTING.value)
            .values(status=status.value, decided_at=at, result_issue_id=result_issue_id, error_code=error_code)
        )
        if not await self._changed(stmt):
            return None
        return await self.get(proposal_id)

    async def reject(self, proposal_id: UUID, *, now: datetime) -> bool:
        stmt = (
            update(ActionProposalRow)
            .where(
                ActionProposalRow.id == proposal_id,
                ActionProposalRow.status == _PENDING,
                ActionProposalRow.expires_at > now,
            )
            .values(status=ProposalStatus.REJECTED.value, decided_at=now)
        )
        return await self._changed(stmt)

    async def mark_expired(self, proposal_id: UUID, *, now: datetime) -> None:
        stmt = (
            update(ActionProposalRow)
            .where(
                ActionProposalRow.id == proposal_id,
                ActionProposalRow.status == _PENDING,
                ActionProposalRow.expires_at <= now,
            )
            .values(status=ProposalStatus.EXPIRED.value)
        )
        await self._changed(stmt)

    async def list_executing(self) -> list[ActionProposal]:
        stmt = select(ActionProposalRow).where(ActionProposalRow.status == ProposalStatus.EXECUTING.value)
        async with self._sessions() as session:
            return [_proposal(row) for row in await session.scalars(stmt.order_by(ActionProposalRow.created_at))]

    async def _changed(self, stmt: Any) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(stmt)
            assert isinstance(result, CursorResult)
            return bool(result.rowcount)


def _row(p: ActionProposal) -> ActionProposalRow:
    return ActionProposalRow(
        id=p.id,
        tool=p.tool,
        target=p.target,
        arguments=_arguments_json(p.arguments),
        summary=p.summary,
        payload_hash=p.payload_hash,
        status=p.status.value,
        source_type=p.source_type,
        source_id=p.source_id,
        related_issues=[_issue_json(i) for i in p.related_issues],
        created_at=p.created_at,
        expires_at=p.expires_at,
        decided_at=p.decided_at,
        result_issue_id=p.result_issue_id,
        error_code=p.error_code,
        grounding=_grounding_json(p.grounding) if p.grounding is not None else None,
    )


def _grounding_json(g: ProposalGrounding) -> dict[str, Any]:
    return {
        "checked_claims": g.checked_claims,
        "supported": g.supported,
        "issues": [
            {"claim": i.claim, "reason": i.reason.value, "markers": list(i.markers), "numbers": list(i.numbers)}
            for i in g.issues
        ],
    }


def _grounding(data: dict[str, Any]) -> ProposalGrounding:
    return ProposalGrounding(
        checked_claims=int(data["checked_claims"]),
        supported=int(data["supported"]),
        issues=tuple(
            GroundingIssue(
                claim=i["claim"],
                reason=GroundingReason(i["reason"]),
                markers=tuple(i["markers"]),
                numbers=tuple(i.get("numbers", ())),
            )
            for i in data["issues"]
        ),
    )


def _arguments_json(draft: IssueDraft) -> dict[str, Any]:
    return {"title": draft.title, "body": draft.body, "labels": list(draft.labels)}


def _issue_json(issue: Issue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "title": issue.title,
        "body": issue.body,
        "labels": list(issue.labels),
        "status": issue.status,
        "created_at": issue.created_at.isoformat(),
    }


def _issue(data: dict[str, Any]) -> Issue:
    return Issue(
        id=data["id"],
        title=data["title"],
        body=data["body"],
        labels=tuple(data["labels"]),
        status="closed" if data["status"] == "closed" else "open",
        created_at=datetime.fromisoformat(data["created_at"]),
    )


def _proposal(row: ActionProposalRow) -> ActionProposal:
    args = row.arguments
    return ActionProposal(
        id=row.id,
        tool=row.tool,
        target=row.target,
        arguments=IssueDraft(title=args["title"], body=args["body"], labels=tuple(args["labels"])),
        summary=row.summary,
        payload_hash=row.payload_hash,
        status=ProposalStatus(row.status),
        source_type=row.source_type,
        source_id=row.source_id,
        related_issues=tuple(_issue(i) for i in row.related_issues),
        created_at=row.created_at,
        expires_at=row.expires_at,
        decided_at=row.decided_at,
        result_issue_id=row.result_issue_id,
        error_code=row.error_code,
        grounding=_grounding(row.grounding) if row.grounding is not None else None,
    )
