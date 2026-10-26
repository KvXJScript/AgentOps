"""Approve / reject proposals. The ONLY code path in AgentOps that can call `create_issue`.

Approve: re-validate the stored arguments, recompute and compare the payload hash, check expiry, then
atomically move `pending → executing` (a conditional UPDATE: only one concurrent approver wins) and only
then call the tool with `idempotency_key = proposal.id`. The outcome is stored as `executed` or `failed`.
Approving an `executed` proposal with the same hash returns it unchanged, without a second call.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from agentops.application.proposals.arguments import validated
from agentops.domain.errors import DomainError
from agentops.domain.proposals import (
    ActionProposal,
    PayloadMismatch,
    ProposalExpired,
    ProposalNotFound,
    ProposalNotPending,
    ProposalRepository,
    ProposalStatus,
    payload_hash,
)
from agentops.domain.tracker import IssueWriter

logger = logging.getLogger(__name__)

INTERNAL_CODE = "internal_error"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ApprovalService:
    def __init__(
        self,
        repository: ProposalRepository,
        writer: IssueWriter,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._writer = writer
        self._clock = clock

    async def approve(self, proposal_id: UUID, submitted_hash: str) -> ActionProposal:
        proposal = await self._load(proposal_id)
        now = self._clock()
        current = proposal.as_of(now)
        same_hash = hmac.compare_digest(current.payload_hash, submitted_hash)

        if current.status is ProposalStatus.EXECUTED:
            if not same_hash:
                self._refused(current, "payload_mismatch")
                raise PayloadMismatch()
            return current  # idempotent repeat: no second tool call
        if current.status is ProposalStatus.EXPIRED:
            await self._repo.mark_expired(proposal_id, now=now)
            self._refused(current, "expired")
            raise ProposalExpired()
        if current.status is not ProposalStatus.PENDING:
            self._refused(current, "not_pending")
            raise ProposalNotPending()

        # Re-validate what is stored and make sure it is still exactly what was reviewed.
        arguments = validated(current.arguments)
        recomputed = payload_hash(current.tool, current.target, arguments)
        if arguments != current.arguments or not hmac.compare_digest(recomputed, current.payload_hash):
            self._refused(current, "stored_payload_changed")
            raise PayloadMismatch()
        if not same_hash:
            self._refused(current, "payload_mismatch")
            raise PayloadMismatch()

        if not await self._repo.claim_for_execution(proposal_id, payload_hash=current.payload_hash, now=now):
            return await self._explain_lost_claim(proposal_id, submitted_hash)

        # Shielded: a client disconnect must not leave the proposal stuck in `executing`.
        return await asyncio.shield(self._execute(current))

    async def reject(self, proposal_id: UUID) -> ActionProposal:
        proposal = await self._load(proposal_id)
        now = self._clock()
        current = proposal.as_of(now)
        if current.status is ProposalStatus.EXPIRED:
            await self._repo.mark_expired(proposal_id, now=now)
            raise ProposalExpired()
        if current.status is not ProposalStatus.PENDING:
            raise ProposalNotPending()
        if not await self._repo.reject(proposal_id, now=now):
            latest = (await self._load(proposal_id)).as_of(self._clock())
            if latest.status is ProposalStatus.EXPIRED:
                raise ProposalExpired()
            raise ProposalNotPending()
        logger.info("proposal.rejected", extra={"proposal_id": str(proposal_id)})
        return (await self._load(proposal_id)).as_of(self._clock())

    async def recover_executing(self) -> int:
        """Finish proposals a previous process left `executing`.

        The user already approved them and `create_issue` is idempotent by proposal id, so re-sending is
        safe: it returns the issue if it was created before the crash, or creates it now.
        """
        stuck = await self._repo.list_executing()
        for proposal in stuck:
            await self._execute(proposal)
        if stuck:
            logger.warning("proposal.recovered_executing", extra={"count": len(stuck)})
        return len(stuck)

    async def _execute(self, proposal: ActionProposal) -> ActionProposal:
        started = time.monotonic()
        issue_id: str | None = None
        error_code: str | None = None
        try:
            issue = await self._writer.create_issue(proposal.arguments, idempotency_key=str(proposal.id))
            issue_id = issue.id
        except DomainError as exc:
            error_code = exc.code
        except Exception:
            logger.exception("proposal.execute_crashed", extra={"proposal_id": str(proposal.id)})
            error_code = INTERNAL_CODE
        status = ProposalStatus.EXECUTED if issue_id is not None else ProposalStatus.FAILED
        stored = await self._repo.finish_execution(
            proposal.id, status=status, at=self._clock(), result_issue_id=issue_id, error_code=error_code
        )
        logger.info(
            "proposal.executed" if issue_id else "proposal.execution_failed",
            extra={
                "proposal_id": str(proposal.id),
                "tool": proposal.tool,
                "status": status.value,
                "issue_id": issue_id,
                "error_code": error_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        if stored is None:  # someone else finished it meanwhile; report what is stored
            return (await self._load(proposal.id)).as_of(self._clock())
        return stored

    async def _explain_lost_claim(self, proposal_id: UUID, submitted_hash: str) -> ActionProposal:
        """The conditional UPDATE matched nothing: say why, from the state that won."""
        latest = (await self._load(proposal_id)).as_of(self._clock())
        if latest.status is ProposalStatus.EXECUTED and hmac.compare_digest(latest.payload_hash, submitted_hash):
            return latest
        self._refused(latest, "claim_lost")
        if latest.status is ProposalStatus.EXPIRED:
            raise ProposalExpired()
        if latest.status is ProposalStatus.PENDING:
            raise PayloadMismatch()  # still pending, so the hash must have differed
        raise ProposalNotPending()

    async def _load(self, proposal_id: UUID) -> ActionProposal:
        proposal = await self._repo.get(proposal_id)
        if proposal is None:
            raise ProposalNotFound()
        return proposal

    @staticmethod
    def _refused(proposal: ActionProposal, reason: str) -> None:
        logger.info(
            "proposal.approve_refused",
            extra={"proposal_id": str(proposal.id), "status": proposal.status.value, "reason": reason},
        )
