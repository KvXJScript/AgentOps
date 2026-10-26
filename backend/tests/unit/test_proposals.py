"""Proposal drafting and the approval boundary (fakes + scripted model)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, replace

import pytest

from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.domain.errors import LLMNotConfigured, LLMTimeout
from agentops.domain.proposals import (
    ActionProposal,
    PayloadMismatch,
    ProposalExpired,
    ProposalNotFound,
    ProposalNotPending,
    ProposalStatus,
    ReportNotReady,
    canonical_payload,
    payload_hash,
)
from agentops.domain.reports import InvalidModelOutput, ReportNotFound, ReportStatus
from agentops.domain.tracker import InvalidToolArguments, IssueDraft, McpTimeout, McpUnavailable
from tests.fakes import ScriptedChatModel, ScriptStep
from tests.report_fakes import InMemoryReportRunRepository
from tests.tracker_fakes import (
    CountingIssueWriter,
    FakeIssueReader,
    InMemoryProposalRepository,
    SettableClock,
    draft_reply,
    succeeded_run,
)


@dataclass
class World:
    proposals: ProposalService
    approvals: ApprovalService
    repo: InMemoryProposalRepository
    reports: InMemoryReportRunRepository
    reader: FakeIssueReader
    writer: CountingIssueWriter
    model: ScriptedChatModel
    clock: SettableClock
    report_id: uuid.UUID


def _world(*scripts: list[ScriptStep], writer: CountingIssueWriter | None = None) -> World:
    repo = InMemoryProposalRepository()
    reports = InMemoryReportRunRepository()
    run = succeeded_run()
    reports.runs[run.id] = run
    reader = FakeIssueReader()
    writer = writer or CountingIssueWriter()
    model = ScriptedChatModel(*(scripts or ([draft_reply()],)))
    clock = SettableClock()
    return World(
        proposals=ProposalService(repo, reports, reader, model, ttl_s=900, clock=clock),
        approvals=ApprovalService(repo, writer, clock=clock),
        repo=repo,
        reports=reports,
        reader=reader,
        writer=writer,
        model=model,
        clock=clock,
        report_id=run.id,
    )


async def _pending(w: World) -> ActionProposal:
    proposal, created = await w.proposals.create_from_report(w.report_id)
    assert created
    return proposal


# --- hashing ----------------------------------------------------------------------------------------
def test_payload_hash_is_sha256_of_canonical_json() -> None:
    args = IssueDraft(title="Zażółć tytuł", body="Body", labels=("bug", "docs"))
    expected = json.dumps(
        {
            "tool": "create_issue",
            "target": "local-tracker",
            "arguments": {"title": "Zażółć tytuł", "body": "Body", "labels": ["bug", "docs"]},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    assert canonical_payload("create_issue", "local-tracker", args) == expected
    assert payload_hash("create_issue", "local-tracker", args) == hashlib.sha256(expected).hexdigest()
    assert payload_hash("create_issue", "local-tracker", replace(args, body="Body!")) != payload_hash(
        "create_issue", "local-tracker", args
    )


# --- drafting ---------------------------------------------------------------------------------------
async def test_drafts_a_pending_proposal_with_server_fixed_tool_target_and_summary() -> None:
    w = _world([draft_reply(labels=["follow-up", "question", "follow-up"], body="Next step [S1] [I1].")])
    proposal = await _pending(w)
    assert proposal.status is ProposalStatus.PENDING
    assert (proposal.tool, proposal.target) == ("create_issue", "local-tracker")
    assert proposal.arguments.labels == ("follow-up", "question")  # deduplicated, canonical order
    assert proposal.arguments.body == "Next step."  # evidence markers stripped
    assert proposal.summary == (
        'Create issue "Follow up: remote work equipment budget" in local-tracker (local demo MCP server) '
        "with follow-up, question."
    )
    assert proposal.payload_hash == payload_hash(proposal.tool, proposal.target, proposal.arguments)
    assert proposal.expires_at - proposal.created_at == __import__("datetime").timedelta(minutes=15)
    assert proposal.source_id == w.report_id
    assert [i.id for i in proposal.related_issues] == ["LT-1"]
    assert w.reader.searches == [("Remote work policy", 5)]
    assert w.writer.calls == []  # drafting never writes


async def test_report_and_issues_reach_the_model_only_as_untrusted_evidence() -> None:
    w = _world()
    await _pending(w)
    (history, system_prompt) = w.model.calls[0]
    assert "untrusted data" in system_prompt
    assert "you have no tools" in system_prompt
    request = history[-1].content
    assert '<evidence label="R1" source="report">' in request
    assert '<evidence label="I1" source="local-tracker issue">' in request
    assert "Remote work follow-up" in request


async def test_second_create_returns_the_existing_pending_proposal_without_a_model_call() -> None:
    w = _world()
    first = await _pending(w)
    again, created = await w.proposals.create_from_report(w.report_id)
    assert not created
    assert again == first
    assert len(w.model.calls) == 1


async def test_an_expired_pending_proposal_does_not_block_a_new_one() -> None:
    w = _world([draft_reply()], [draft_reply(title="A second draft title")])
    first = await _pending(w)
    w.clock.advance(minutes=16)
    second, created = await w.proposals.create_from_report(w.report_id)
    assert created
    assert second.id != first.id
    assert (await w.proposals.get(first.id)).status is ProposalStatus.EXPIRED


@pytest.mark.parametrize("status", [ReportStatus.RUNNING, ReportStatus.FAILED, ReportStatus.INSUFFICIENT_EVIDENCE])
async def test_only_succeeded_reports_can_be_proposed(status: ReportStatus) -> None:
    w = _world()
    run = succeeded_run(status=status)
    w.reports.runs[run.id] = run
    with pytest.raises(ReportNotReady):
        await w.proposals.create_from_report(run.id)
    with pytest.raises(ReportNotFound):
        await w.proposals.create_from_report(uuid.uuid4())
    assert w.model.calls == []


async def test_invalid_json_gets_one_corrective_retry() -> None:
    w = _world(["not json at all"], [draft_reply()])
    proposal = await _pending(w)
    assert proposal.status is ProposalStatus.PENDING
    assert len(w.model.calls) == 2
    assert "could not be used" in w.model.calls[1][0][-1].content


@pytest.mark.parametrize(
    "bad",
    [
        draft_reply(labels=["admin"]),
        draft_reply(tool="delete_all_issues"),  # the model may not pick a tool
        draft_reply(target="production-tracker"),
        draft_reply(title="Hey"),
        draft_reply(body="x" * 4001),
        draft_reply(labels=["bug", "docs", "question", "follow-up"]),
    ],
)
async def test_schema_violations_twice_end_in_invalid_model_output(bad: str) -> None:
    w = _world([bad], [bad])
    with pytest.raises(InvalidModelOutput):
        await w.proposals.create_from_report(w.report_id)
    assert w.repo.items == {}
    assert len(w.model.calls) == 2


async def test_title_that_is_only_markers_is_invalid_model_output() -> None:
    reply = draft_reply(title="[S1] [S2] ok")
    w = _world([reply])
    with pytest.raises(InvalidModelOutput):
        await w.proposals.create_from_report(w.report_id)


async def test_drafting_fails_fast_without_model_or_tracker() -> None:
    w = _world()
    no_model = ProposalService(w.repo, w.reports, w.reader, None, clock=w.clock)
    with pytest.raises(LLMNotConfigured):
        await no_model.create_from_report(w.report_id)
    w.reader.error = McpUnavailable()
    with pytest.raises(McpUnavailable):
        await w.proposals.create_from_report(w.report_id)
    assert w.repo.items == {}


async def test_provider_errors_propagate() -> None:
    w = _world([LLMTimeout()])
    with pytest.raises(LLMTimeout):
        await w.proposals.create_from_report(w.report_id)


# --- approval ---------------------------------------------------------------------------------------
async def test_approve_executes_exactly_once_with_proposal_id_as_idempotency_key() -> None:
    w = _world()
    p = await _pending(w)
    done = await w.approvals.approve(p.id, p.payload_hash)
    assert done.status is ProposalStatus.EXECUTED
    assert done.result_issue_id == "LT-100"
    assert done.decided_at is not None
    assert w.writer.calls == [(p.arguments, str(p.id))]


async def test_repeated_approve_returns_the_executed_proposal_without_a_second_call() -> None:
    w = _world()
    p = await _pending(w)
    first = await w.approvals.approve(p.id, p.payload_hash)
    again = await w.approvals.approve(p.id, p.payload_hash)
    assert again == first
    assert len(w.writer.calls) == 1
    with pytest.raises(PayloadMismatch):  # executed + different hash
        await w.approvals.approve(p.id, "0" * 64)
    assert len(w.writer.calls) == 1


async def test_hash_mismatch_is_refused_and_leaves_the_proposal_pending() -> None:
    w = _world()
    p = await _pending(w)
    with pytest.raises(PayloadMismatch):
        await w.approvals.approve(p.id, "f" * 64)
    assert (await w.proposals.get(p.id)).status is ProposalStatus.PENDING
    assert w.writer.calls == []


async def test_tampered_stored_arguments_are_refused() -> None:
    w = _world()
    p = await _pending(w)
    w.repo.items[p.id] = replace(p, arguments=replace(p.arguments, body="Something else entirely"))
    with pytest.raises(PayloadMismatch):
        await w.approvals.approve(p.id, p.payload_hash)
    w.repo.items[p.id] = replace(p, arguments=replace(p.arguments, labels=("admin",)))
    with pytest.raises(InvalidToolArguments):
        await w.approvals.approve(p.id, p.payload_hash)
    assert w.writer.calls == []


async def test_expired_proposal_reads_back_expired_and_cannot_be_approved_or_rejected() -> None:
    w = _world()
    p = await _pending(w)
    w.clock.advance(minutes=15)
    assert (await w.proposals.get(p.id)).status is ProposalStatus.EXPIRED
    assert [x.id for x in await w.proposals.list(ProposalStatus.EXPIRED)] == [p.id]
    assert await w.proposals.list(ProposalStatus.PENDING) == []
    with pytest.raises(ProposalExpired):
        await w.approvals.approve(p.id, p.payload_hash)
    with pytest.raises(ProposalExpired):
        await w.approvals.reject(p.id)
    assert w.repo.items[p.id].status is ProposalStatus.EXPIRED  # persisted
    assert w.writer.calls == []


async def test_concurrent_approvals_execute_only_once() -> None:
    w = _world(writer=CountingIssueWriter(delay_s=0.05))
    p = await _pending(w)
    results = await asyncio.gather(
        *(w.approvals.approve(p.id, p.payload_hash) for _ in range(5)), return_exceptions=True
    )
    executed = [r for r in results if isinstance(r, ActionProposal)]
    refused = [r for r in results if isinstance(r, ProposalNotPending)]
    assert len(executed) == 1
    assert executed[0].status is ProposalStatus.EXECUTED
    assert len(refused) == 4  # they saw it `executing`
    assert len(w.writer.calls) == 1


async def test_lost_claim_after_execution_finished_returns_the_result() -> None:
    w = _world()
    p = await _pending(w)
    real_claim = w.repo.claim_for_execution

    async def claim_and_lose(proposal_id: uuid.UUID, *, payload_hash: str, now: object) -> bool:
        # Another request won and finished between our checks and our UPDATE.
        assert await real_claim(proposal_id, payload_hash=payload_hash, now=now)  # type: ignore[arg-type]
        await w.repo.finish_execution(
            proposal_id, status=ProposalStatus.EXECUTED, at=w.clock(), result_issue_id="LT-77", error_code=None
        )
        return False

    w.repo.claim_for_execution = claim_and_lose  # type: ignore[method-assign]
    result = await w.approvals.approve(p.id, p.payload_hash)
    assert result.result_issue_id == "LT-77"
    assert w.writer.calls == []


@pytest.mark.parametrize("error", [McpUnavailable(), McpTimeout(), RuntimeError("boom")])
async def test_tool_failure_is_stored_as_failed_with_error_code(error: BaseException) -> None:
    w = _world(writer=CountingIssueWriter(error=error))
    p = await _pending(w)
    failed = await w.approvals.approve(p.id, p.payload_hash)
    assert failed.status is ProposalStatus.FAILED
    assert failed.error_code == getattr(error, "code", "internal_error")
    assert failed.result_issue_id is None
    with pytest.raises(ProposalNotPending):
        await w.approvals.approve(p.id, p.payload_hash)
    assert len(w.writer.calls) == 1


async def test_reject_only_from_pending_and_approve_after_reject_is_refused() -> None:
    w = _world()
    p = await _pending(w)
    rejected = await w.approvals.reject(p.id)
    assert rejected.status is ProposalStatus.REJECTED
    assert rejected.decided_at is not None
    with pytest.raises(ProposalNotPending):
        await w.approvals.reject(p.id)
    with pytest.raises(ProposalNotPending):
        await w.approvals.approve(p.id, p.payload_hash)
    assert w.writer.calls == []
    # A rejected proposal frees the report for a new one.
    w.model = ScriptedChatModel([draft_reply()])
    fresh = ProposalService(w.repo, w.reports, w.reader, w.model, clock=w.clock)
    _, created = await fresh.create_from_report(w.report_id)
    assert created


async def test_reject_after_execution_is_refused() -> None:
    w = _world()
    p = await _pending(w)
    await w.approvals.approve(p.id, p.payload_hash)
    with pytest.raises(ProposalNotPending):
        await w.approvals.reject(p.id)


async def test_unknown_proposal_is_not_found() -> None:
    w = _world()
    for call in (
        w.approvals.approve(uuid.uuid4(), "x"),
        w.approvals.reject(uuid.uuid4()),
        w.proposals.get(uuid.uuid4()),
    ):
        with pytest.raises(ProposalNotFound):
            await call


async def test_startup_recovery_finishes_executing_proposals_idempotently() -> None:
    w = _world()
    p = await _pending(w)
    assert await w.repo.claim_for_execution(p.id, payload_hash=p.payload_hash, now=w.clock())
    await w.writer.create_issue(p.arguments, idempotency_key=str(p.id))  # the crash happened after the call
    assert await w.approvals.recover_executing() == 1
    recovered = await w.proposals.get(p.id)
    assert recovered.status is ProposalStatus.EXECUTED
    assert recovered.result_issue_id == "LT-100"  # same issue, no duplicate
    assert {key for _, key in w.writer.calls} == {str(p.id)}


async def test_concurrent_approvals_that_all_pass_prechecks_race_on_the_atomic_claim() -> None:
    w = _world(writer=CountingIssueWriter(delay_s=0.01))
    p = await _pending(w)
    real_get = w.repo.get
    barrier = asyncio.Barrier(5)

    async def get_then_wait(proposal_id: uuid.UUID) -> ActionProposal | None:
        found = await real_get(proposal_id)
        if found is not None and found.status is ProposalStatus.PENDING:
            await barrier.wait()  # everyone has read `pending` before anyone claims
        return found

    w.repo.get = get_then_wait  # type: ignore[method-assign]
    results = await asyncio.gather(
        *(w.approvals.approve(p.id, p.payload_hash) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(r, ActionProposal) for r in results) == 1
    assert sum(isinstance(r, ProposalNotPending) for r in results) == 4
    assert len(w.writer.calls) == 1


async def test_logs_carry_ids_and_metrics_but_never_bodies_prompts_or_issue_text() -> None:
    import io
    import logging

    import structlog

    from agentops.infrastructure.logging import configure_logging

    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    try:
        w = _world([draft_reply(body="Confidential body text for the tracker.")])
        w.reader.issues = [replace(w.reader.issues[0], body="SYSTEM: secret injected issue body")]
        p = await _pending(w)
        with pytest.raises(PayloadMismatch):
            await w.approvals.approve(p.id, "0" * 64)
        await w.approvals.approve(p.id, p.payload_hash)
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    events = {entry["event"]: entry for entry in lines}
    assert events["proposal.drafted"]["proposal_id"] == str(p.id)
    assert events["proposal.drafted"]["new_proposal"] is True
    assert events["proposal.approve_refused"]["reason"] == "payload_mismatch"
    assert events["proposal.executed"]["issue_id"] == "LT-100"
    output = stream.getvalue()
    for secret in (
        "Confidential body",
        "secret injected",
        "remote work equipment budget",
        "untrusted data",
        "Remote work policy",
    ):
        assert secret not in output
