"""The whole report graph, run through ReportService with fakes."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any

import pytest

from agentops.application.reports.service import ReportService, ReportSettings
from agentops.domain.errors import LLMNotConfigured, LLMUnavailable
from agentops.domain.ports import ChatModel
from agentops.domain.reports import (
    ReportNotFound,
    ReportRun,
    ReportStatus,
    RetrievalNotConfigured,
    Stage,
    StageName,
    StageStatus,
)
from tests.fakes import ScriptedChatModel, ScriptStep
from tests.report_fakes import (
    FakeRetriever,
    InMemoryReportRunRepository,
    TickingClock,
    chunk,
    draft_json,
    findings_json,
)

QUESTION = "What does the handbook say about remote work?"
EVIDENCE = [chunk("Staff may work remotely two days a week.", score=0.9), chunk("Laptops are provided.", score=0.7)]
HAPPY: tuple[list[ScriptStep], list[ScriptStep]] = (
    [findings_json(("Two remote days per week", ["S1"]), ("Laptops provided", ["S2"]))],
    [draft_json(summary="Two remote days [S1] [S8].")],
)


def _service(
    model: ChatModel | None,
    retriever: FakeRetriever | None = None,
    repo: InMemoryReportRunRepository | None = None,
    **settings: Any,
) -> tuple[ReportService, InMemoryReportRunRepository]:
    repo = repo or InMemoryReportRunRepository()
    service = ReportService(
        repo,
        retriever=retriever if retriever is not None else FakeRetriever(EVIDENCE),
        chat_model=model,
        settings=ReportSettings(min_score=0.5, **settings),
        clock=TickingClock(),
    )
    return service, repo


async def _run(service: ReportService, question: str = QUESTION) -> ReportRun:
    created = await service.create_run(question)
    await service.wait_for_runs()
    return await service.get_run(created.id)


def _statuses(run: ReportRun) -> dict[str, str]:
    return {s.name.value: s.status.value for s in run.stages}


async def test_happy_path_produces_a_grounded_report() -> None:
    model = ScriptedChatModel(*HAPPY)
    service, repo = _service(model)

    created = await service.create_run(QUESTION)
    assert created.status is ReportStatus.QUEUED
    assert [s.status for s in created.stages] == [StageStatus.PENDING] * 5
    await service.wait_for_runs()
    run = await service.get_run(created.id)

    assert run.status is ReportStatus.SUCCEEDED
    assert run.error_code is None
    assert run.model == "scripted-1"
    assert set(_statuses(run).values()) == {"done"}
    assert [s.attempts for s in run.stages] == [1, 1, 1, 1, 1]
    assert all(s.started_at and s.finished_at and s.started_at < s.finished_at for s in run.stages)
    assert run.stage(StageName.RETRIEVE).detail == "2 chunks retrieved, 2 used as evidence"
    assert run.stage(StageName.VERIFY).detail == "2 references verified, 1 invalid reference removed"
    assert run.report is not None
    assert run.report.summary == "Two remote days [S1]."
    assert [r.marker for r in run.report.references] == ["S1", "S2"]
    assert len(model.calls) == 2

    # Every stage transition was persisted, so a poller sees progress.
    timeline = [(r.status.value, _statuses(r)) for r in repo.history]
    assert timeline[1] == ("running", {**_statuses(created), "validate": "running"})
    assert ("running", {**_statuses(created), "validate": "done", "retrieve": "done", "analyze": "running"}) in timeline
    assert len(repo.history) == 1 + 2 * 5 + 1


async def test_insufficient_evidence_never_calls_the_model() -> None:
    model = ScriptedChatModel(*HAPPY)
    service, _ = _service(model, FakeRetriever([chunk("unrelated", score=0.1)]))

    run = await _run(service)

    assert run.status is ReportStatus.INSUFFICIENT_EVIDENCE
    assert run.error_code is None
    assert run.report is None
    assert _statuses(run) == {
        "validate": "done",
        "retrieve": "done",
        "analyze": "skipped",
        "draft": "skipped",
        "verify": "skipped",
    }
    assert model.calls == []


async def test_no_supported_findings_stops_before_drafting() -> None:
    model = ScriptedChatModel([findings_json(("Invented", ["S9"]))])
    service, _ = _service(model)

    run = await _run(service)

    assert run.status is ReportStatus.INSUFFICIENT_EVIDENCE
    assert _statuses(run)["analyze"] == "done"
    assert _statuses(run)["draft"] == "skipped"
    assert len(model.calls) == 1


async def test_report_without_valid_references_is_not_shipped() -> None:
    model = ScriptedChatModel(HAPPY[0], [draft_json(summary="Claim [S7].", sections=[{"heading": "H", "body": "B"}])])
    service, _ = _service(model)

    run = await _run(service)

    assert run.status is ReportStatus.INSUFFICIENT_EVIDENCE
    assert run.report is None
    assert run.stage(StageName.VERIFY).status is StageStatus.DONE
    assert run.stage(StageName.VERIFY).detail == "No valid references; report withheld (1 invalid reference removed)"


async def test_provider_failure_mid_run_fails_the_stage_and_the_run() -> None:
    model = ScriptedChatModel(HAPPY[0], [LLMUnavailable()])
    service, _ = _service(model)

    run = await _run(service)

    assert run.status is ReportStatus.FAILED
    assert run.error_code == "llm_unavailable"
    assert _statuses(run) == {
        "validate": "done",
        "retrieve": "done",
        "analyze": "done",
        "draft": "failed",
        "verify": "skipped",
    }
    draft = run.stage(StageName.DRAFT)
    assert draft.attempts == 1
    assert draft.finished_at is not None
    assert run.report is None


async def test_invalid_json_retry_then_success() -> None:
    model = ScriptedChatModel(["not json"], *HAPPY)
    service, _ = _service(model)

    run = await _run(service)

    assert run.status is ReportStatus.SUCCEEDED
    assert run.stage(StageName.ANALYZE).attempts == 2
    assert len(model.calls) == 3


async def test_invalid_json_twice_is_invalid_model_output() -> None:
    model = ScriptedChatModel(HAPPY[0], ["bad"], ["still bad"])
    service, _ = _service(model)

    run = await _run(service)

    assert run.status is ReportStatus.FAILED
    assert run.error_code == "invalid_model_output"
    assert run.stage(StageName.DRAFT).status is StageStatus.FAILED
    assert run.stage(StageName.DRAFT).attempts == 2


async def test_retriever_crash_is_internal_error() -> None:
    service, _ = _service(ScriptedChatModel(), FakeRetriever(error=RuntimeError("db down")))

    run = await _run(service)

    assert run.status is ReportStatus.FAILED
    assert run.error_code == "internal_error"
    assert run.stage(StageName.RETRIEVE).status is StageStatus.FAILED


async def test_whole_run_timeout_is_enforced() -> None:
    model = ScriptedChatModel([5.0, "never"])
    service, _ = _service(model, timeout_s=0.05)

    run = await _run(service)

    assert run.status is ReportStatus.FAILED
    assert run.error_code == "report_timeout"
    assert run.stage(StageName.ANALYZE).status is StageStatus.FAILED
    assert run.stage(StageName.DRAFT).status is StageStatus.SKIPPED


async def test_graph_step_limit_is_enforced() -> None:
    service, _ = _service(ScriptedChatModel(*HAPPY), max_graph_steps=3)

    run = await _run(service)

    assert run.status is ReportStatus.FAILED
    assert run.error_code == "internal_error"
    assert _statuses(run)["verify"] == "skipped"


async def test_concurrency_limit_keeps_extra_runs_queued() -> None:
    model = ScriptedChatModel([0.2, *HAPPY[0]], HAPPY[1], *HAPPY)
    service, repo = _service(model, max_concurrent_runs=1)

    first = await service.create_run(QUESTION)
    second = await service.create_run(QUESTION)
    await asyncio.sleep(0.05)
    assert repo.runs[first.id].status is ReportStatus.RUNNING
    assert repo.runs[second.id].status is ReportStatus.QUEUED
    await service.wait_for_runs()
    assert {repo.runs[first.id].status, repo.runs[second.id].status} == {ReportStatus.SUCCEEDED}


@pytest.mark.parametrize(
    ("model", "retriever", "error"),
    [(None, FakeRetriever(EVIDENCE), LLMNotConfigured), (ScriptedChatModel(), None, RetrievalNotConfigured)],
)
async def test_create_fails_fast_and_stores_nothing(
    model: ChatModel | None, retriever: FakeRetriever | None, error: type[Exception]
) -> None:
    repo = InMemoryReportRunRepository()
    service = ReportService(repo, retriever=retriever, chat_model=model)
    with pytest.raises(error):
        await service.create_run(QUESTION)
    assert repo.runs == {}


async def test_unknown_run_is_not_found() -> None:
    service, _ = _service(ScriptedChatModel())
    with pytest.raises(ReportNotFound):
        await service.get_run(uuid.uuid4())


async def test_startup_marks_leftover_runs_interrupted() -> None:
    repo = InMemoryReportRunRepository()
    service, _ = _service(ScriptedChatModel(), repo=repo)
    clock = TickingClock()
    base = ReportRun(
        id=uuid.uuid4(),
        question=QUESTION,
        document_ids=None,
        status=ReportStatus.RUNNING,
        stages=(
            Stage(StageName.VALIDATE, StageStatus.DONE, 1, clock(), clock()),
            Stage(StageName.RETRIEVE, StageStatus.RUNNING, 0, clock()),
            Stage(StageName.ANALYZE),
            Stage(StageName.DRAFT),
            Stage(StageName.VERIFY),
        ),
        created_at=clock(),
        updated_at=clock(),
    )
    queued = replace(base, id=uuid.uuid4(), status=ReportStatus.QUEUED)
    done = replace(base, id=uuid.uuid4(), status=ReportStatus.INSUFFICIENT_EVIDENCE)
    for run in (base, queued, done):
        await repo.create(run)

    assert await service.recover_interrupted() == 2

    for run_id in (base.id, queued.id):
        run = repo.runs[run_id]
        assert run.status is ReportStatus.FAILED
        assert run.error_code == "interrupted"
        assert _statuses(run) == {
            "validate": "done",
            "retrieve": "failed",
            "analyze": "skipped",
            "draft": "skipped",
            "verify": "skipped",
        }
    assert repo.runs[done.id] == done


async def test_shutdown_cancels_slow_runs_and_marks_them_interrupted() -> None:
    model = ScriptedChatModel([10.0])
    service, repo = _service(model, shutdown_grace_s=0.05)

    created = await service.create_run(QUESTION)
    await asyncio.sleep(0.02)
    await service.aclose()

    run = repo.runs[created.id]
    assert run.status is ReportStatus.FAILED
    assert run.error_code == "interrupted"
    assert run.stage(StageName.ANALYZE).status is StageStatus.FAILED


async def test_logs_carry_run_id_and_stage_metrics_but_no_evidence_or_prompts() -> None:
    import io
    import json
    import logging

    import structlog

    from agentops.infrastructure.logging import configure_logging

    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    try:
        service, _ = _service(ScriptedChatModel(*HAPPY))
        run = await _run(service)
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()

    output = stream.getvalue()
    lines = [json.loads(line) for line in output.splitlines() if line.strip()]
    stages = [entry for entry in lines if entry["event"] == "report.stage_finished"]
    assert [s["stage"] for s in stages] == ["validate", "retrieve", "analyze", "draft", "verify"]
    assert all(s["run_id"] == str(run.id) and "duration_ms" in s and s["attempts"] == 1 for s in stages)
    assert stages[2]["model"] == "scripted-1"
    finished = next(entry for entry in lines if entry["event"] == "report.run_finished")
    assert finished["status"] == "succeeded"
    assert finished["run_id"] == str(run.id)
    for secret in ("Staff may work remotely", "Laptops are provided", "untrusted data", QUESTION):
        assert secret not in output
