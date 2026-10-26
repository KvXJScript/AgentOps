from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from agentops.api.dependencies import DependenciesDep
from agentops.api.schemas import ErrorEnvelope, Timestamp
from agentops.application.tracker import TrackerService
from agentops.domain.tracker import (
    LIMIT_MAX,
    QUERY_MAX_CHARS,
    TRACKER_LABEL,
    TRACKER_NAME,
    TRACKER_TRANSPORT,
    Issue,
    ToolPolicy,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def get_tracker_service(deps: DependenciesDep) -> TrackerService:
    return deps.tracker_service or TrackerService(None)


TrackerServiceDep = Annotated[TrackerService, Depends(get_tracker_service)]


class IssueOut(BaseModel):
    id: str
    title: str
    body: str
    labels: list[str]
    status: Literal["open", "closed"]
    created_at: Timestamp

    @classmethod
    def of(cls, i: Issue) -> IssueOut:
        return cls(id=i.id, title=i.title, body=i.body, labels=list(i.labels), status=i.status, created_at=i.created_at)


class ToolInfoOut(BaseModel):
    name: str
    access: Literal["read", "write"]
    requires_approval: bool
    description: str

    @classmethod
    def of(cls, p: ToolPolicy) -> ToolInfoOut:
        return cls(name=p.name, access=p.access.value, requires_approval=p.requires_approval, description=p.description)


class TrackerServerOut(BaseModel):
    name: str
    label: str
    transport: Literal["stdio"]
    status: Literal["connected", "unavailable"]


class TrackerOut(BaseModel):
    server: TrackerServerOut
    tools: list[ToolInfoOut]


class IssueListOut(BaseModel):
    items: list[IssueOut]
    truncated: bool


@router.get("/tracker", response_model=TrackerOut)
async def tracker_status(service: TrackerServiceDep) -> TrackerOut:
    status = await service.status()
    return TrackerOut(
        server=TrackerServerOut(
            name=TRACKER_NAME,
            label=TRACKER_LABEL,
            transport=TRACKER_TRANSPORT,
            status="connected" if status.connected else "unavailable",
        ),
        tools=[ToolInfoOut.of(p) for p in status.tools],
    )


@router.get(
    "/tracker/issues",
    response_model=IssueListOut,
    responses={422: {"model": ErrorEnvelope}, 502: {"model": ErrorEnvelope}, 504: {"model": ErrorEnvelope}},
)
async def tracker_issues(
    service: TrackerServiceDep,
    query: Annotated[str, Query(max_length=QUERY_MAX_CHARS)] = "",
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = 20,
) -> IssueListOut:
    page = await service.issues(query or None, limit=limit)
    return IssueListOut(items=[IssueOut.of(i) for i in page.items], truncated=page.truncated)
