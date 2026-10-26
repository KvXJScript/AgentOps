"""Test doubles and builders for the report workflow."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from agentops.domain.reports import ReportRun, ReportStatus
from agentops.domain.retrieval import ChunkLocation, RetrievedChunk

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


class TickingClock:
    """Deterministic clock: every call advances one second."""

    def __init__(self, start: datetime = T0) -> None:
        self._now = start

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


def chunk(text: str, *, score: float = 0.8, title: str = "Handbook", rank: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=title,
        rank=rank,
        score=score,
        text=text,
        location=ChunkLocation(char_start=0, char_end=len(text), page=2, heading_path=("Intro",)),
    )


class FakeRetriever:
    def __init__(self, chunks: Sequence[RetrievedChunk] = (), *, error: BaseException | None = None) -> None:
        self.chunks = list(chunks)
        self.error = error
        self.calls: list[tuple[str, int, list[UUID] | None]] = []

    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        self.calls.append((query, top_k, list(document_ids) if document_ids is not None else None))
        if self.error is not None:
            raise self.error
        return self.chunks[:top_k]


class InMemoryReportRunRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, ReportRun] = {}
        self.history: list[ReportRun] = []  # every write, to inspect progress snapshots

    async def create(self, run: ReportRun) -> None:
        self.runs[run.id] = run
        self.history.append(run)

    async def get(self, run_id: UUID) -> ReportRun | None:
        return self.runs.get(run_id)

    async def list_recent(self, limit: int) -> list[ReportRun]:
        return sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    async def save(self, run: ReportRun) -> None:
        assert run.id in self.runs
        self.runs[run.id] = run
        self.history.append(run)

    async def list_active(self) -> list[ReportRun]:
        return [r for r in self.runs.values() if r.status in (ReportStatus.QUEUED, ReportStatus.RUNNING)]


def findings_json(*items: tuple[str, list[str]]) -> str:
    return json.dumps({"findings": [{"statement": s, "citations": c} for s, c in items]})


def draft_json(title: str = "Remote work policy", summary: str = "Staff may work remotely [S1].", **extra: Any) -> str:
    sections = extra.pop("sections", [{"heading": "Eligibility", "body": "All staff after probation [S1][S2]."}])
    return json.dumps({"title": title, "summary": summary, "sections": sections, **extra})
