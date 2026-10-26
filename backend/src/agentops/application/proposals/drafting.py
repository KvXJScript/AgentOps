"""Draft a `create_issue` proposal from a succeeded report.

This service only READS the tracker (`IssueReader`): it has no way to create an issue. The model writes
title/body/labels into a strict schema; the tool, the target and the summary are set here by server code.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agentops.application.proposals.arguments import DraftIssueOutput, to_draft
from agentops.application.proposals.prompts import DRAFT_ISSUE_SYSTEM_PROMPT, draft_issue_request
from agentops.application.reports.nodes import StageContext, ask_json
from agentops.domain.errors import LLMNotConfigured
from agentops.domain.ports import ChatModel
from agentops.domain.proposals import (
    PROPOSAL_TARGET,
    PROPOSAL_TOOL,
    SOURCE_REPORT,
    ActionProposal,
    ProposalNotFound,
    ProposalRepository,
    ProposalStatus,
    ReportNotReady,
    payload_hash,
)
from agentops.domain.reports import InvalidModelOutput, ReportNotFound, ReportRunRepository, ReportStatus
from agentops.domain.tracker import QUERY_MAX_CHARS, InvalidToolArguments, IssueDraft, IssueReader

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 15 * 60
RELATED_ISSUES_LIMIT = 5
LIST_LIMIT = 100


def _utcnow() -> datetime:
    return datetime.now(UTC)


def proposal_summary(draft: IssueDraft) -> str:
    """Server-written, from validated arguments only — the model cannot shape it."""
    labels = ", ".join(draft.labels) if draft.labels else "no labels"
    return f'Create issue "{draft.title}" in {PROPOSAL_TARGET} (local demo MCP server) with {labels}.'


class ProposalService:
    def __init__(
        self,
        repository: ProposalRepository,
        reports: ReportRunRepository,
        reader: IssueReader,
        chat_model: ChatModel | None,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._reports = reports
        self._reader = reader
        self._model = chat_model
        self._ttl = timedelta(seconds=ttl_s)
        self._clock = clock

    async def create_from_report(self, report_id: UUID) -> tuple[ActionProposal, bool]:
        """Returns (proposal, created). An existing live pending proposal for the report is returned as is."""
        run = await self._reports.get(report_id)
        if run is None:
            raise ReportNotFound()
        if run.status is not ReportStatus.SUCCEEDED or run.report is None:
            raise ReportNotReady()
        existing = await self._repo.find_live_pending(report_id, now=self._clock())
        if existing is not None:
            return existing, False
        if self._model is None:
            raise LLMNotConfigured()

        started = time.monotonic()
        related = (
            await self._reader.search_issues(run.report.title[:QUERY_MAX_CHARS], limit=RELATED_ISSUES_LIMIT)
        ).items
        ctx = StageContext()
        output = await ask_json(
            self._model,
            DraftIssueOutput,
            system_prompt=DRAFT_ISSUE_SYSTEM_PROMPT,
            request=draft_issue_request(run.question, run.report, related),
            ctx=ctx,
        )
        try:
            draft = to_draft(output)
        except InvalidToolArguments as exc:  # e.g. the title got too short once markers were stripped
            raise InvalidModelOutput() from exc

        now = self._clock()
        candidate = ActionProposal(
            id=uuid.uuid4(),
            tool=PROPOSAL_TOOL,
            target=PROPOSAL_TARGET,
            arguments=draft,
            summary=proposal_summary(draft),
            payload_hash=payload_hash(PROPOSAL_TOOL, PROPOSAL_TARGET, draft),
            status=ProposalStatus.PENDING,
            source_type=SOURCE_REPORT,
            source_id=report_id,
            related_issues=tuple(related),
            created_at=now,
            expires_at=now + self._ttl,
        )
        proposal, created = await self._repo.create_pending(candidate, now=now)
        logger.info(
            "proposal.drafted",
            extra={
                "proposal_id": str(proposal.id),
                "report_id": str(report_id),
                "new_proposal": created,
                "related_issues": len(related),
                "attempts": ctx.attempts,
                "labels": list(draft.labels),
                "body_chars": len(draft.body),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "model": self._model.model,
            },
        )
        return proposal, created

    async def get(self, proposal_id: UUID) -> ActionProposal:
        proposal = await self._repo.get(proposal_id)
        if proposal is None:
            raise ProposalNotFound()
        return proposal.as_of(self._clock())

    async def list(self, status: ProposalStatus | None = None) -> list[ActionProposal]:
        now = self._clock()
        return [p.as_of(now) for p in await self._repo.list_recent(status=status, now=now, limit=LIST_LIMIT)]
