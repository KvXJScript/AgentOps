"""Draft a `create_issue` proposal from a succeeded report.

This service only READS the tracker (`IssueReader`): it has no way to create an issue. The model writes
title/body/labels into a strict schema; the tool, the target and the summary are set here by server code.

The model also sees the report's stored reference excerpts (`S1..`) and must cite them. A deterministic
check (`grounding.py`) then strips invalid markers, appends a "Sources" footer to the body and records,
per claim, what it couldn't tie to the excerpts. That result is advisory: it is stored on the proposal for
the reviewer and is not part of the payload hash.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agentops.application.proposals.arguments import DraftIssueReply, to_draft
from agentops.application.proposals.grounding import check_proposal_grounding
from agentops.application.proposals.prompts import (
    DRAFT_ISSUE_SYSTEM_PROMPT,
    EVIDENCE_MAX_CHARS,
    draft_issue_request,
    format_source_excerpts,
)
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
from agentops.domain.retrieval import Citation
from agentops.domain.tracker import ISSUE_BODY_MAX, QUERY_MAX_CHARS, InvalidToolArguments, IssueDraft, IssueReader

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 15 * 60
RELATED_ISSUES_LIMIT = 5
LIST_LIMIT = 100
SOURCE_LINE_MAX_CHARS = 140
SOURCES_HEADING = "Sources (cited excerpts from the report's documents):"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def proposal_summary(draft: IssueDraft) -> str:
    """Server-written, from validated arguments only — the model cannot shape it."""
    labels = ", ".join(draft.labels) if draft.labels else "no labels"
    return f'Create issue "{draft.title}" in {PROPOSAL_TARGET} (local demo MCP server) with {labels}.'


def sources_footer(cited: Sequence[Citation], *, room: int) -> str:
    """Server-written list of what the body's `[Sn]` markers point to, within `room` characters."""
    if not cited:
        return ""
    lines = [f"\n\n---\n{SOURCES_HEADING}"]
    used = len(lines[0])
    for n, ref in enumerate(cited):
        line = f"\n- [{ref.marker}] {_source_name(ref)}"
        more = f"\n- … and {len(cited) - n} more"
        if used + len(line) + (len(more) if n < len(cited) - 1 else 0) > room:
            lines.append(more)
            break
        lines.append(line)
        used += len(line)
    return "".join(lines)


def _source_name(ref: Citation) -> str:
    # Brackets would read as markers (and `[I1]` would be stripped on re-validation): use parentheses.
    title = ref.document_title.replace("[", "(").replace("]", ")")
    heading = ref.location.heading_path[-1] if ref.location.heading_path else ""
    name = f"{title} — {heading}" if heading else title
    name = " ".join(name.split())
    return name if len(name) <= SOURCE_LINE_MAX_CHARS else name[: SOURCE_LINE_MAX_CHARS - 1].rstrip() + "…"


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
        sources, evidence = format_source_excerpts(run.report.references, max_chars=EVIDENCE_MAX_CHARS)
        ctx = StageContext()
        output = await ask_json(
            self._model,
            DraftIssueReply,
            system_prompt=DRAFT_ISSUE_SYSTEM_PROMPT,
            request=draft_issue_request(run.question, run.report, related, sources=sources),
            ctx=ctx,
        )
        checked = check_proposal_grounding(output.body, evidence)
        by_label = {ref.marker: ref for ref in evidence}
        body = checked.body.strip()
        body += sources_footer([by_label[m] for m in checked.cited], room=ISSUE_BODY_MAX - len(body))
        try:
            draft = to_draft(output, body=body)
        except InvalidToolArguments as exc:  # e.g. the title or body got empty once markers were stripped
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
            grounding=checked.grounding,
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
                "evidence": len(evidence),
                "grounding_claims": checked.grounding.checked_claims,
                "grounding_supported": checked.grounding.supported,
                "grounding_issues": len(checked.grounding.issues),
                "invalid_markers": checked.invalid_markers,
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
