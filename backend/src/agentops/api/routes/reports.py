from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentops.api.dependencies import DependenciesDep
from agentops.api.schemas import CitationOut, ErrorEnvelope, Timestamp
from agentops.application.reports.nodes import MAX_DOCUMENT_IDS, QUESTION_MAX_CHARS, QUESTION_MIN_CHARS
from agentops.application.reports.service import ReportService
from agentops.domain.errors import DomainError
from agentops.domain.reports import Report, ReportRun, Stage

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportsUnavailable(DomainError):
    code = "internal_error"
    message = "Reports are not available on this server."


def get_report_service(deps: DependenciesDep) -> ReportService:
    if deps.report_service is None:
        raise ReportsUnavailable()
    return deps.report_service


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


class CreateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=QUESTION_MIN_CHARS, max_length=QUESTION_MAX_CHARS)
    ]
    document_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_DOCUMENT_IDS)] | None = None


class StageOut(BaseModel):
    name: Literal["validate", "retrieve", "analyze", "draft", "verify"]
    status: Literal["pending", "running", "done", "skipped", "failed"]
    attempts: int
    started_at: Timestamp | None
    finished_at: Timestamp | None
    detail: str | None

    @classmethod
    def of(cls, s: Stage) -> StageOut:
        return cls(
            name=s.name.value,
            status=s.status.value,
            attempts=s.attempts,
            started_at=s.started_at,
            finished_at=s.finished_at,
            detail=s.detail,
        )


class ReportSectionOut(BaseModel):
    heading: str
    body: str


class ReportOut(BaseModel):
    title: str
    summary: str
    sections: list[ReportSectionOut]
    references: list[CitationOut]

    @classmethod
    def of(cls, r: Report) -> ReportOut:
        return cls(
            title=r.title,
            summary=r.summary,
            sections=[ReportSectionOut(heading=s.heading, body=s.body) for s in r.sections],
            references=[CitationOut.of(c) for c in r.references],
        )


class ReportRunSummaryOut(BaseModel):
    """A run as listed: everything except the report body."""

    id: UUID
    question: str
    document_ids: list[UUID] | None
    status: Literal["queued", "running", "succeeded", "insufficient_evidence", "failed"]
    stages: list[StageOut]
    error_code: str | None
    model: str | None
    created_at: Timestamp
    updated_at: Timestamp

    @classmethod
    def fields_of(cls, run: ReportRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "question": run.question,
            "document_ids": list(run.document_ids) if run.document_ids is not None else None,
            "status": run.status.value,
            "stages": [StageOut.of(s) for s in run.stages],
            "error_code": run.error_code,
            "model": run.model,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    @classmethod
    def of(cls, run: ReportRun) -> ReportRunSummaryOut:
        return cls(**cls.fields_of(run))


class ReportRunOut(ReportRunSummaryOut):
    report: ReportOut | None

    @classmethod
    def of(cls, run: ReportRun) -> ReportRunOut:
        return cls(**cls.fields_of(run), report=ReportOut.of(run.report) if run.report else None)


class ReportRunListOut(BaseModel):
    items: list[ReportRunSummaryOut]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReportRunOut,
    responses={422: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}},
)
async def create_report(body: CreateReportRequest, service: ReportServiceDep) -> ReportRunOut:
    run = await service.create_run(body.question, body.document_ids)
    return ReportRunOut.of(run)


@router.get("", response_model=ReportRunListOut)
async def list_reports(service: ReportServiceDep) -> ReportRunListOut:
    return ReportRunListOut(items=[ReportRunSummaryOut.of(r) for r in await service.list_runs()])


@router.get("/{run_id}", response_model=ReportRunOut, responses={404: {"model": ErrorEnvelope}})
async def get_report(run_id: UUID, service: ReportServiceDep) -> ReportRunOut:
    return ReportRunOut.of(await service.get_run(run_id))
