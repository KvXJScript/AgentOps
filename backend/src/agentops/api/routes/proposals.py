from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, StringConstraints

from agentops.api.dependencies import DependenciesDep
from agentops.api.routes.integrations import IssueOut
from agentops.api.schemas import ErrorEnvelope, Timestamp
from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.domain.proposals import ActionProposal, ProposalStatus
from agentops.domain.tracker import McpUnavailable

router = APIRouter(tags=["proposals"])

_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorEnvelope} for code in (404, 409, 410, 422, 429, 502, 503, 504)
}


def get_proposal_service(deps: DependenciesDep) -> ProposalService:
    if deps.proposal_service is None:
        raise McpUnavailable("The tracker integration is not enabled on this server.")
    return deps.proposal_service


def get_approval_service(deps: DependenciesDep) -> ApprovalService:
    if deps.approval_service is None:
        raise McpUnavailable("The tracker integration is not enabled on this server.")
    return deps.approval_service


ProposalServiceDep = Annotated[ProposalService, Depends(get_proposal_service)]
ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]

StatusName = Literal["pending", "executing", "executed", "rejected", "expired", "failed"]


class ProposalArgumentsOut(BaseModel):
    title: str
    body: str
    labels: list[Literal["bug", "docs", "follow-up", "question"]]


class ProposalSourceOut(BaseModel):
    type: Literal["report"]
    id: UUID


class ProposalResultOut(BaseModel):
    issue_id: str


class ActionProposalOut(BaseModel):
    id: UUID
    tool: Literal["create_issue"]
    target: Literal["local-tracker"]
    arguments: ProposalArgumentsOut
    summary: str
    payload_hash: str
    status: StatusName
    source: ProposalSourceOut
    related_issues: list[IssueOut]
    created_at: Timestamp
    expires_at: Timestamp
    decided_at: Timestamp | None
    result: ProposalResultOut | None
    error_code: str | None

    @classmethod
    def of(cls, p: ActionProposal) -> ActionProposalOut:
        return cls.model_validate(
            {
                "id": p.id,
                "tool": p.tool,
                "target": p.target,
                "arguments": {"title": p.arguments.title, "body": p.arguments.body, "labels": list(p.arguments.labels)},
                "summary": p.summary,
                "payload_hash": p.payload_hash,
                "status": p.status.value,
                "source": {"type": p.source_type, "id": p.source_id},
                "related_issues": [IssueOut.of(i) for i in p.related_issues],
                "created_at": p.created_at,
                "expires_at": p.expires_at,
                "decided_at": p.decided_at,
                "result": {"issue_id": p.result_issue_id} if p.result_issue_id else None,
                "error_code": p.error_code,
            }
        )


class ProposalListOut(BaseModel):
    items: list[ActionProposalOut]


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: Annotated[str, StringConstraints(min_length=1, max_length=128)]


@router.post(
    "/reports/{report_id}/proposals",
    status_code=status.HTTP_201_CREATED,
    response_model=ActionProposalOut,
    responses={200: {"model": ActionProposalOut}, **_ERRORS},
)
async def create_proposal(report_id: UUID, service: ProposalServiceDep, response: Response) -> ActionProposalOut:
    proposal, created = await service.create_from_report(report_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return ActionProposalOut.of(proposal)


@router.get("/proposals", response_model=ProposalListOut, responses={422: {"model": ErrorEnvelope}})
async def list_proposals(
    service: ProposalServiceDep, status_filter: Annotated[StatusName | None, Query(alias="status")] = None
) -> ProposalListOut:
    items = await service.list(ProposalStatus(status_filter) if status_filter else None)
    return ProposalListOut(items=[ActionProposalOut.of(p) for p in items])


@router.get("/proposals/{proposal_id}", response_model=ActionProposalOut, responses={404: {"model": ErrorEnvelope}})
async def get_proposal(proposal_id: UUID, service: ProposalServiceDep) -> ActionProposalOut:
    return ActionProposalOut.of(await service.get(proposal_id))


@router.post("/proposals/{proposal_id}/approve", response_model=ActionProposalOut, responses=_ERRORS)
async def approve_proposal(proposal_id: UUID, body: ApproveRequest, service: ApprovalServiceDep) -> ActionProposalOut:
    return ActionProposalOut.of(await service.approve(proposal_id, body.payload_hash))


@router.post("/proposals/{proposal_id}/reject", response_model=ActionProposalOut, responses=_ERRORS)
async def reject_proposal(proposal_id: UUID, service: ApprovalServiceDep) -> ActionProposalOut:
    return ActionProposalOut.of(await service.reject(proposal_id))
