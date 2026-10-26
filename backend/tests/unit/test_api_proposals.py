"""HTTP contract for Phase 4: integrations, proposals, approve/reject."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from agentops.api.dependencies import AppDependencies
from agentops.application.chat_service import ChatService
from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.application.tracker import TrackerService
from agentops.domain.reports import ReportStatus
from agentops.domain.tracker import InvalidToolOutput, McpTimeout
from agentops.main import create_app
from tests.conftest import DatabaseProbe, make_settings
from tests.fakes import InMemoryConversationRepository, ScriptedChatModel
from tests.report_fakes import InMemoryReportRunRepository
from tests.tracker_fakes import (
    CountingIssueWriter,
    FakeIssueReader,
    InMemoryProposalRepository,
    SettableClock,
    draft_reply,
    issue,
    succeeded_run,
)

PROPOSAL_KEYS = {
    "id", "tool", "target", "arguments", "summary", "payload_hash", "status", "source", "related_issues",
    "created_at", "expires_at", "decided_at", "result", "error_code",
}  # fmt: skip


@dataclass
class H:
    client: httpx.AsyncClient
    reader: FakeIssueReader
    writer: CountingIssueWriter
    reports: InMemoryReportRunRepository
    model: ScriptedChatModel
    clock: SettableClock
    report_id: uuid.UUID


async def _harness(model: ScriptedChatModel, *, enabled: bool = True) -> AsyncIterator[H]:
    reader = FakeIssueReader([issue(1, "Remote work follow-up", "body")])
    writer = CountingIssueWriter()
    reports = InMemoryReportRunRepository()
    run = succeeded_run()
    reports.runs[run.id] = run
    repo = InMemoryProposalRepository()
    clock = SettableClock()
    deps = AppDependencies(
        chat_service=ChatService(InMemoryConversationRepository(), model, history_limit=4),
        chat_model=model,
        check_database=DatabaseProbe(),
        tracker_service=TrackerService(reader) if enabled else None,
        proposal_service=ProposalService(repo, reports, reader, model, clock=clock) if enabled else None,
        approval_service=ApprovalService(repo, writer, clock=clock) if enabled else None,
    )
    app = create_app(make_settings(), dependencies=deps)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield H(client, reader, writer, reports, model, clock, run.id)


@pytest.fixture
async def h() -> AsyncIterator[H]:
    async for harness in _harness(ScriptedChatModel([draft_reply()])):
        yield harness


async def _create(h: H) -> dict[str, Any]:
    response = await h.client.post(f"/reports/{h.report_id}/proposals")
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"]


async def test_tracker_status_lists_allowlisted_tools(h: H) -> None:
    body = (await h.client.get("/integrations/tracker")).json()
    assert body["server"] == {
        "name": "local-tracker",
        "label": "Local demo MCP server",
        "transport": "stdio",
        "status": "connected",
    }
    assert [(t["name"], t["access"], t["requires_approval"]) for t in body["tools"]] == [
        ("list_issues", "read", False),
        ("search_issues", "read", False),
        ("create_issue", "write", True),
    ]
    assert all(t["description"] for t in body["tools"])
    h.reader.connected = False
    assert (await h.client.get("/integrations/tracker")).json()["server"]["status"] == "unavailable"


async def test_tracker_issues_list_search_and_validation(h: H) -> None:
    listed = (await h.client.get("/integrations/tracker/issues")).json()
    assert listed["truncated"] is False
    assert set(listed["items"][0]) == {"id", "title", "body", "labels", "status", "created_at"}
    assert listed["items"][0]["created_at"].endswith("Z")
    await h.client.get("/integrations/tracker/issues", params={"query": "remote", "limit": 5})
    assert h.reader.searches == [("remote", 5)]
    _error(await h.client.get("/integrations/tracker/issues", params={"limit": 0}), 422, "validation_error")
    _error(await h.client.get("/integrations/tracker/issues", params={"limit": 51}), 422, "validation_error")
    _error(await h.client.get("/integrations/tracker/issues", params={"query": "x" * 201}), 422, "validation_error")


@pytest.mark.parametrize(
    ("error", "status", "code"), [(McpTimeout(), 504, "mcp_timeout"), (InvalidToolOutput(), 502, "invalid_tool_output")]
)
async def test_tracker_errors_use_the_envelope(h: H, error: Exception, status: int, code: str) -> None:
    h.reader.error = error
    _error(await h.client.get("/integrations/tracker/issues"), status, code)


async def test_create_proposal_201_then_200_for_the_existing_pending_one(h: H) -> None:
    body = await _create(h)
    assert set(body) == PROPOSAL_KEYS
    assert body["status"] == "pending"
    assert (body["tool"], body["target"]) == ("create_issue", "local-tracker")
    assert body["source"] == {"type": "report", "id": str(h.report_id)}
    assert body["result"] is None
    assert body["decided_at"] is None
    assert [i["id"] for i in body["related_issues"]] == ["LT-1"]
    again = await h.client.post(f"/reports/{h.report_id}/proposals")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


async def test_create_proposal_errors(h: H) -> None:
    _error(await h.client.post(f"/reports/{uuid.uuid4()}/proposals"), 404, "not_found")
    running = succeeded_run(status=ReportStatus.RUNNING)
    h.reports.runs[running.id] = running
    _error(await h.client.post(f"/reports/{running.id}/proposals"), 409, "report_not_ready")


async def test_invalid_model_output_is_502() -> None:
    async for h in _harness(ScriptedChatModel(["nope"], ["still nope"])):
        _error(await h.client.post(f"/reports/{h.report_id}/proposals"), 502, "invalid_model_output")


async def test_approve_flow_hash_mismatch_execute_and_repeat(h: H) -> None:
    p = await _create(h)
    url = f"/proposals/{p['id']}/approve"
    _error(await h.client.post(url, json={"payload_hash": "0" * 64}), 409, "payload_mismatch")
    _error(await h.client.post(url, json={}), 422, "validation_error")
    assert h.writer.calls == []

    done = await h.client.post(url, json={"payload_hash": p["payload_hash"]})
    assert done.status_code == 200
    assert done.json()["status"] == "executed"
    assert done.json()["result"] == {"issue_id": "LT-100"}
    again = await h.client.post(url, json={"payload_hash": p["payload_hash"]})
    assert again.status_code == 200
    assert again.json() == done.json()
    _error(await h.client.post(url, json={"payload_hash": "0" * 64}), 409, "payload_mismatch")
    _error(await h.client.post(f"/proposals/{p['id']}/reject"), 409, "proposal_not_pending")
    assert len(h.writer.calls) == 1


async def test_reject_then_approve_is_refused(h: H) -> None:
    p = await _create(h)
    rejected = await h.client.post(f"/proposals/{p['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    _error(
        await h.client.post(f"/proposals/{p['id']}/approve", json={"payload_hash": p["payload_hash"]}),
        409,
        "proposal_not_pending",
    )
    assert h.writer.calls == []


async def test_expired_reads_back_and_is_410(h: H) -> None:
    p = await _create(h)
    h.clock.advance(minutes=16)
    assert (await h.client.get(f"/proposals/{p['id']}")).json()["status"] == "expired"
    assert [x["id"] for x in (await h.client.get("/proposals", params={"status": "expired"})).json()["items"]] == [
        p["id"]
    ]
    assert (await h.client.get("/proposals", params={"status": "pending"})).json()["items"] == []
    _error(
        await h.client.post(f"/proposals/{p['id']}/approve", json={"payload_hash": p["payload_hash"]}),
        410,
        "proposal_expired",
    )
    _error(await h.client.post(f"/proposals/{p['id']}/reject"), 410, "proposal_expired")


async def test_list_and_get(h: H) -> None:
    p = await _create(h)
    assert [x["id"] for x in (await h.client.get("/proposals")).json()["items"]] == [p["id"]]
    assert (await h.client.get(f"/proposals/{p['id']}")).json() == p
    _error(await h.client.get(f"/proposals/{uuid.uuid4()}"), 404, "not_found")
    _error(await h.client.get("/proposals", params={"status": "bogus"}), 422, "validation_error")


async def test_disabled_tracker_reports_unavailable() -> None:
    async for h in _harness(ScriptedChatModel(), enabled=False):
        status = (await h.client.get("/integrations/tracker")).json()
        assert status["server"]["status"] == "unavailable"
        _error(await h.client.get("/integrations/tracker/issues"), 502, "mcp_unavailable")
        _error(await h.client.post(f"/reports/{h.report_id}/proposals"), 502, "mcp_unavailable")
        _error(await h.client.get("/proposals"), 502, "mcp_unavailable")
