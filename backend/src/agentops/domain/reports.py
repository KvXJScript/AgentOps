"""Report runs: the record a report workflow run leaves behind, and its port."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from agentops.domain.errors import DomainError
from agentops.domain.retrieval import Citation


class ReportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"

    @property
    def is_active(self) -> bool:
        return self in (ReportStatus.QUEUED, ReportStatus.RUNNING)


class StageName(StrEnum):
    VALIDATE = "validate"
    RETRIEVE = "retrieve"
    ANALYZE = "analyze"
    DRAFT = "draft"
    VERIFY = "verify"


STAGE_ORDER: tuple[StageName, ...] = tuple(StageName)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Stage:
    name: StageName
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None  # short and safe to show: counts, never evidence text


@dataclass(frozen=True, slots=True)
class ReportSection:
    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class Report:
    title: str
    summary: str
    sections: tuple[ReportSection, ...]
    references: tuple[Citation, ...]  # resolved from the run's evidence, like chat citations


@dataclass(frozen=True, slots=True)
class ReportRun:
    id: UUID
    question: str
    document_ids: tuple[UUID, ...] | None
    status: ReportStatus
    stages: tuple[Stage, ...]
    created_at: datetime
    updated_at: datetime
    report: Report | None = None
    error_code: str | None = None
    model: str | None = None

    def stage(self, name: StageName) -> Stage:
        return next(s for s in self.stages if s.name is name)

    def with_stage(self, stage: Stage, *, at: datetime) -> ReportRun:
        stages = tuple(stage if s.name is stage.name else s for s in self.stages)
        return replace(self, stages=stages, updated_at=at)


def initial_stages() -> tuple[Stage, ...]:
    return tuple(Stage(name=name) for name in STAGE_ORDER)


def close_stages(stages: tuple[Stage, ...], *, at: datetime) -> tuple[Stage, ...]:
    """End a run's timeline: a running stage becomes failed, pending stages become skipped."""

    def close(stage: Stage) -> Stage:
        if stage.status is StageStatus.RUNNING:
            return replace(stage, status=StageStatus.FAILED, finished_at=at)
        if stage.status is StageStatus.PENDING:
            return replace(stage, status=StageStatus.SKIPPED)
        return stage

    return tuple(close(stage) for stage in stages)


class ReportNotFound(DomainError):
    code = "not_found"
    message = "Report not found"


class RetrievalNotConfigured(DomainError):
    code = "retrieval_not_configured"
    message = "Document retrieval is not available on the server."


class ReportInputInvalid(DomainError):
    code = "validation_error"
    message = "The report request is invalid."


class InvalidModelOutput(DomainError):
    """The model's reply could not be parsed, even after the corrective retry."""

    code = "invalid_model_output"
    message = "The model returned output that could not be used."


class ReportRunRepository(Protocol):
    async def create(self, run: ReportRun) -> None: ...

    async def get(self, run_id: UUID) -> ReportRun | None: ...

    async def list_recent(self, limit: int) -> list[ReportRun]:
        """Newest `created_at` first."""
        ...

    async def save(self, run: ReportRun) -> None:
        """Overwrite the mutable fields (status, stages, report, error_code, model, updated_at)."""
        ...

    async def list_active(self) -> list[ReportRun]:
        """Runs still `queued` or `running`."""
        ...
