"""Report runs: create, execute in-process in the background, persist progress.

Execution model (deliberately simple, single process):
- `create_run` stores a `queued` run and starts an asyncio task; the client polls.
- Each stage transition is persisted, so polling shows live progress.
- The whole run is bounded by `timeout_s`; provider timeouts/retries come from
  `ResilientChatModel` and are not repeated here.
- On shutdown, active runs get a short grace period, then are cancelled and
  stored as `failed`/`interrupted`. At startup, any run still `queued`/`running`
  (the process died) is marked the same way.

Seam for later: replace the task set with a durable queue + LangGraph
checkpointer to make runs resumable and to run on several workers (startup
recovery assumes one process owns all active runs).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

import structlog

from agentops.application.reports.graph import (
    MAX_GRAPH_STEPS,
    WorkflowServices,
    build_report_graph,
    run_report_graph,
)
from agentops.application.reports.nodes import ReportState, RetrievalPolicy, StageContext
from agentops.domain.errors import DomainError, LLMNotConfigured
from agentops.domain.ports import ChatModel
from agentops.domain.reports import (
    Report,
    ReportNotFound,
    ReportRun,
    ReportRunRepository,
    ReportStatus,
    RetrievalNotConfigured,
    StageName,
    StageStatus,
    close_stages,
    initial_stages,
)
from agentops.domain.retrieval import Retriever

logger = logging.getLogger(__name__)

LIST_LIMIT = 50
TIMEOUT_CODE = "report_timeout"
INTERRUPTED_CODE = "interrupted"
INTERNAL_CODE = "internal_error"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReportSettings:
    timeout_s: float = 120.0
    top_k: int = 8
    min_score: float = 0.3
    max_evidence_chars: int = 12_000
    max_concurrent_runs: int = 2
    max_graph_steps: int = MAX_GRAPH_STEPS
    shutdown_grace_s: float = 5.0


class ReportService:
    def __init__(
        self,
        repository: ReportRunRepository,
        *,
        retriever: Retriever | None,
        chat_model: ChatModel | None,
        settings: ReportSettings | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = repository
        self._retriever = retriever
        self._model = chat_model
        self._settings = settings or ReportSettings()
        self._clock = clock
        self._slots = asyncio.Semaphore(self._settings.max_concurrent_runs)
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_run(self, question: str, document_ids: Sequence[UUID] | None = None) -> ReportRun:
        """Store a queued run and start it. Fails fast (nothing stored) if the workflow cannot run at all."""
        if self._model is None:
            raise LLMNotConfigured()
        if self._retriever is None:
            raise RetrievalNotConfigured()
        now = self._clock()
        run = ReportRun(
            id=uuid.uuid4(),
            question=question,
            document_ids=tuple(dict.fromkeys(document_ids)) if document_ids is not None else None,
            status=ReportStatus.QUEUED,
            stages=initial_stages(),
            created_at=now,
            updated_at=now,
            model=self._model.model,
        )
        await self._repo.create(run)
        services = WorkflowServices(
            retriever=self._retriever,
            model=self._model,
            policy=RetrievalPolicy(
                top_k=self._settings.top_k,
                min_score=self._settings.min_score,
                max_evidence_chars=self._settings.max_evidence_chars,
            ),
        )
        task = asyncio.create_task(self._execute(run, services), name=f"report-run-{run.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("report.run_queued", extra={"run_id": str(run.id), "scoped": document_ids is not None})
        return run

    async def list_runs(self) -> list[ReportRun]:
        return await self._repo.list_recent(LIST_LIMIT)

    async def get_run(self, run_id: UUID) -> ReportRun:
        run = await self._repo.get(run_id)
        if run is None:
            raise ReportNotFound()
        return run

    async def recover_interrupted(self) -> int:
        """Mark runs left `queued`/`running` by a previous process as failed/interrupted."""
        now = self._clock()
        active = await self._repo.list_active()
        for run in active:
            await self._repo.save(
                replace(
                    run,
                    status=ReportStatus.FAILED,
                    error_code=INTERRUPTED_CODE,
                    stages=close_stages(run.stages, at=now),
                    updated_at=now,
                )
            )
        if active:
            logger.warning("report.runs_interrupted", extra={"count": len(active)})
        return len(active)

    async def wait_for_runs(self) -> None:
        """Wait until every background run has finished (tests, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def aclose(self) -> None:
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(set(self._tasks), timeout=self._settings.shutdown_grace_s)
        for task in pending:
            task.cancel()
        await self.wait_for_runs()

    async def _execute(self, run: ReportRun, services: WorkflowServices) -> None:
        structlog.contextvars.bind_contextvars(run_id=str(run.id))
        tracker = _RunTracker(run, self._repo, self._clock)
        initial: ReportState = {
            "question": run.question,
            "document_ids": list(run.document_ids) if run.document_ids is not None else None,
        }
        started = time.monotonic()
        status: ReportStatus = ReportStatus.FAILED
        report: Report | None = None
        error_code: str | None = INTERNAL_CODE
        try:
            async with self._slots:
                timeout = asyncio.timeout(self._settings.timeout_s)
                try:
                    async with timeout:
                        graph = build_report_graph(services, tracker)
                        final = await run_report_graph(graph, initial, max_steps=self._settings.max_graph_steps)
                except TimeoutError:
                    if not timeout.expired():
                        raise
                    error_code = TIMEOUT_CODE
                else:
                    report = final.get("report")
                    if report is not None and not final.get("insufficient"):
                        status, error_code = ReportStatus.SUCCEEDED, None
                    else:
                        status, report, error_code = ReportStatus.INSUFFICIENT_EVIDENCE, None, None
        except asyncio.CancelledError:
            await self._finish(tracker, ReportStatus.FAILED, None, INTERRUPTED_CODE, started)
            raise
        except DomainError as exc:
            error_code = exc.code
        except Exception:
            logger.exception("report.run_crashed", extra={"stage": tracker.current_stage})
            error_code = INTERNAL_CODE
        await self._finish(tracker, status, report, error_code, started)

    async def _finish(
        self,
        tracker: _RunTracker,
        status: ReportStatus,
        report: Report | None,
        error_code: str | None,
        started: float,
    ) -> None:
        try:
            await tracker.finish(status, report=report, error_code=error_code)
        except Exception:
            logger.exception("report.persist_failed", extra={"status": status.value})
        logger.info(
            "report.run_finished",
            extra={
                "status": status.value,
                "error_code": error_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "model": tracker.run.model,
                "references": len(report.references) if report else 0,
            },
        )


class _RunTracker:
    """Persists the stage timeline of one run as the graph moves through it."""

    def __init__(self, run: ReportRun, repository: ReportRunRepository, clock: Callable[[], datetime]) -> None:
        self.run = run
        self._repo = repository
        self._clock = clock
        self._current: tuple[StageName, StageContext, float] | None = None

    @property
    def current_stage(self) -> str | None:
        return self._current[0].value if self._current else None

    async def stage_started(self, name: StageName) -> StageContext:
        now = self._clock()
        stage = replace(self.run.stage(name), status=StageStatus.RUNNING, started_at=now)
        self.run = replace(self.run.with_stage(stage, at=now), status=ReportStatus.RUNNING)
        ctx = StageContext()
        self._current = (name, ctx, time.monotonic())
        await self._repo.save(self.run)
        return ctx

    async def stage_finished(self, name: StageName, ctx: StageContext) -> None:
        now = self._clock()
        stage = replace(
            self.run.stage(name),
            status=StageStatus.DONE,
            finished_at=now,
            attempts=max(1, ctx.attempts),
            detail=ctx.detail,
        )
        self.run = self.run.with_stage(stage, at=now)
        self._log_stage(StageStatus.DONE)
        self._current = None
        await self._repo.save(self.run)

    async def finish(self, status: ReportStatus, *, report: Report | None, error_code: str | None) -> None:
        now = self._clock()
        if self._current is not None:
            name, ctx, _ = self._current
            stage = replace(self.run.stage(name), attempts=max(1, ctx.attempts), detail=ctx.detail)
            self.run = self.run.with_stage(stage, at=now)
            self._log_stage(StageStatus.FAILED, error_code=error_code)
            self._current = None
        self.run = replace(
            self.run,
            status=status,
            stages=close_stages(self.run.stages, at=now),
            report=report,
            error_code=error_code,
            updated_at=now,
        )
        await self._repo.save(self.run)

    def _log_stage(self, status: StageStatus, *, error_code: str | None = None) -> None:
        assert self._current is not None
        name, ctx, started = self._current
        logger.info(
            "report.stage_finished",
            extra={
                "stage": name.value,
                "status": status.value,
                "attempts": max(1, ctx.attempts),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "model": self.run.model if name in (StageName.ANALYZE, StageName.DRAFT) else None,
                "error_code": error_code,
            },
        )
