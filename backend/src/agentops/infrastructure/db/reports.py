"""Persistence for report runs. Stages and the report are stored as JSONB snapshots."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from agentops.domain.reports import (
    Report,
    ReportRun,
    ReportSection,
    ReportStatus,
    Stage,
    StageName,
    StageStatus,
)
from agentops.infrastructure.db.documents import citation_from_json, citation_to_json, existing_chunk_ids
from agentops.infrastructure.db.models import Base

_STATUSES = ", ".join(f"'{s.value}'" for s in ReportStatus)
_ACTIVE = (ReportStatus.QUEUED.value, ReportStatus.RUNNING.value)


class ReportRunRow(Base):
    __tablename__ = "report_runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    # none_as_null: Python None must be SQL NULL (not JSON null) for the check constraint and `IS NULL`.
    document_ids: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(String(32))
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUSES})", name="status"),
        CheckConstraint("status = 'succeeded' OR report IS NULL", name="report_only_when_succeeded"),
        Index("ix_report_runs_created_at", "created_at"),
        Index("ix_report_runs_status", "status"),
    )


class SqlReportRunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, run: ReportRun) -> None:
        async with self._sessions.begin() as session:
            session.add(ReportRunRow(id=run.id, question=run.question, created_at=run.created_at, **_mutable(run)))

    async def get(self, run_id: UUID) -> ReportRun | None:
        """The full run; references whose chunk no longer exists are flagged `document_deleted`."""
        async with self._sessions() as session:
            row = await session.get(ReportRunRow, run_id)
            if row is None:
                return None
            cited = {UUID(c["chunk_id"]) for c in (row.report or {}).get("references", [])}
            alive = await existing_chunk_ids(session, cited)
            return _run(row, alive_chunk_ids=alive)

    async def list_recent(self, limit: int) -> list[ReportRun]:
        stmt = select(ReportRunRow).order_by(ReportRunRow.created_at.desc(), ReportRunRow.id).limit(limit)
        async with self._sessions() as session:
            return [_run(row) for row in await session.scalars(stmt)]

    async def save(self, run: ReportRun) -> None:
        async with self._sessions.begin() as session:
            await session.execute(update(ReportRunRow).where(ReportRunRow.id == run.id).values(**_mutable(run)))

    async def list_active(self) -> list[ReportRun]:
        stmt = select(ReportRunRow).where(ReportRunRow.status.in_(_ACTIVE)).order_by(ReportRunRow.created_at)
        async with self._sessions() as session:
            return [_run(row) for row in await session.scalars(stmt)]


def _mutable(run: ReportRun) -> dict[str, Any]:
    return {
        "document_ids": [str(d) for d in run.document_ids] if run.document_ids is not None else None,
        "status": run.status.value,
        "stages": [_stage_json(s) for s in run.stages],
        "report": _report_json(run.report) if run.report else None,
        "error_code": run.error_code,
        "model": run.model,
        "updated_at": run.updated_at,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _stage_json(stage: Stage) -> dict[str, Any]:
    return {
        "name": stage.name.value,
        "status": stage.status.value,
        "attempts": stage.attempts,
        "started_at": _iso(stage.started_at),
        "finished_at": _iso(stage.finished_at),
        "detail": stage.detail,
    }


def _stage(data: dict[str, Any]) -> Stage:
    return Stage(
        name=StageName(data["name"]),
        status=StageStatus(data["status"]),
        attempts=int(data.get("attempts", 0)),
        started_at=_dt(data.get("started_at")),
        finished_at=_dt(data.get("finished_at")),
        detail=data.get("detail"),
    )


def _report_json(report: Report) -> dict[str, Any]:
    return {
        "title": report.title,
        "summary": report.summary,
        "sections": [{"heading": s.heading, "body": s.body} for s in report.sections],
        "references": [citation_to_json(c) for c in report.references],
    }


def _report(data: dict[str, Any], alive_chunk_ids: set[UUID] | None) -> Report:
    return Report(
        title=data["title"],
        summary=data["summary"],
        sections=tuple(ReportSection(heading=s["heading"], body=s["body"]) for s in data["sections"]),
        references=tuple(
            citation_from_json(
                c, document_deleted=alive_chunk_ids is not None and UUID(c["chunk_id"]) not in alive_chunk_ids
            )
            for c in data["references"]
        ),
    )


def _run(row: ReportRunRow, *, alive_chunk_ids: set[UUID] | None = None) -> ReportRun:
    """`alive_chunk_ids=None` skips the deleted-source check (lists, recovery)."""
    stages = {s.name: s for s in (_stage(d) for d in row.stages)}
    return ReportRun(
        id=row.id,
        question=row.question,
        document_ids=tuple(UUID(d) for d in row.document_ids) if row.document_ids is not None else None,
        status=ReportStatus(row.status),
        stages=tuple(stages.get(name, Stage(name=name)) for name in StageName),
        report=_report(row.report, alive_chunk_ids) if row.report else None,
        error_code=row.error_code,
        model=row.model,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
