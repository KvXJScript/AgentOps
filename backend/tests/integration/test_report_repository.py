from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.application.reports.service import ReportService
from agentops.domain.reports import (
    Report,
    ReportRun,
    ReportSection,
    ReportStatus,
    Stage,
    StageName,
    StageStatus,
    initial_stages,
)
from agentops.domain.retrieval import ChunkLocation, Citation
from agentops.infrastructure.db.reports import SqlReportRunRepository
from tests.report_fakes import T0

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(session_factory: async_sessionmaker[AsyncSession]) -> SqlReportRunRepository:
    async with session_factory.begin() as session:
        await session.execute(text("TRUNCATE report_runs"))
    return SqlReportRunRepository(session_factory)


def _run(at_minutes: int = 0, status: ReportStatus = ReportStatus.QUEUED) -> ReportRun:
    at = T0 + timedelta(minutes=at_minutes)
    return ReportRun(
        id=uuid.uuid4(),
        question="What does the handbook say?",
        document_ids=(uuid.uuid4(),),
        status=status,
        stages=initial_stages(),
        created_at=at,
        updated_at=at,
        model="m-1",
    )


def _report() -> Report:
    return Report(
        title="Policy",
        summary="Remote work allowed [S1].",
        sections=(ReportSection(heading="Eligibility", body="All staff [S1]."),),
        references=(
            Citation(
                marker="S1",
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                document_title="Handbook",
                excerpt="Staff may work remotely.",
                location=ChunkLocation(char_start=3, char_end=27, page=None, heading_path=("HR", "Remote")),
            ),
        ),
    )


async def test_create_get_and_round_trip_of_a_finished_run(repo: SqlReportRunRepository) -> None:
    run = _run()
    await repo.create(run)
    assert await repo.get(run.id) == run
    assert await repo.get(uuid.uuid4()) is None

    stages = tuple(
        replace(
            s, status=StageStatus.DONE, attempts=2, started_at=T0, finished_at=T0 + timedelta(seconds=3), detail="ok"
        )
        for s in run.stages
    )
    finished = replace(
        run, status=ReportStatus.SUCCEEDED, stages=stages, report=_report(), updated_at=T0 + timedelta(1)
    )
    await repo.save(finished)

    stored = await repo.get(run.id)
    assert stored is not None
    assert stored.report is not None
    # The cited chunk does not exist in document_chunks, so its source reads back as deleted;
    # everything else round-trips exactly.
    (ref,) = stored.report.references
    assert ref.document_deleted is True
    assert replace(ref, document_deleted=False) == finished.report.references[0]  # type: ignore[union-attr]
    assert replace(stored, report=finished.report) == finished
    # Lists never carry report bodies' deletion checks (and the API omits `report` there).
    assert (await repo.list_recent(1))[0].status is ReportStatus.SUCCEEDED


async def test_list_recent_and_active(repo: SqlReportRunRepository) -> None:
    old, new = _run(0), _run(1, ReportStatus.RUNNING)
    done = _run(2, ReportStatus.FAILED)
    for run in (old, new, done):
        await repo.create(run)

    assert [r.id for r in await repo.list_recent(10)] == [done.id, new.id, old.id]
    assert len(await repo.list_recent(1)) == 1
    assert {r.id for r in await repo.list_active()} == {old.id, new.id}


async def test_report_only_allowed_on_succeeded_runs(repo: SqlReportRunRepository) -> None:
    run = _run()
    await repo.create(run)
    with pytest.raises(IntegrityError):
        await repo.save(replace(run, status=ReportStatus.FAILED, report=_report()))


async def test_startup_recovery_against_the_database(repo: SqlReportRunRepository) -> None:
    running = _run(0, ReportStatus.RUNNING)
    running = running.with_stage(Stage(StageName.VALIDATE, StageStatus.RUNNING, 0, T0), at=T0)
    await repo.create(running)

    service = ReportService(repo, retriever=None, chat_model=None, clock=lambda: T0 + timedelta(hours=1))
    assert await service.recover_interrupted() == 1

    stored = await repo.get(running.id)
    assert stored is not None
    assert stored.status is ReportStatus.FAILED
    assert stored.error_code == "interrupted"
    assert stored.stage(StageName.VALIDATE).status is StageStatus.FAILED
    assert stored.stage(StageName.VERIFY).status is StageStatus.SKIPPED
    assert await repo.list_active() == []
