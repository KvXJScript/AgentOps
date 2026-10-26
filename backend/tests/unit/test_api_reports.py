from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from agentops.api.dependencies import AppDependencies
from agentops.application.chat_service import ChatService
from agentops.application.reports.service import ReportService, ReportSettings
from agentops.domain.ports import ChatModel
from agentops.main import create_app
from tests.conftest import DatabaseProbe, make_settings
from tests.fakes import InMemoryConversationRepository, ScriptedChatModel
from tests.report_fakes import FakeRetriever, InMemoryReportRunRepository, chunk, draft_json, findings_json

QUESTION = "What does the handbook say about remote work?"
STAGE_KEYS = {"name", "status", "attempts", "started_at", "finished_at", "detail"}
RUN_KEYS = {"id", "question", "document_ids", "status", "stages", "error_code", "model", "created_at", "updated_at"}


@dataclass
class ReportHarness:
    client: httpx.AsyncClient
    service: ReportService
    repo: InMemoryReportRunRepository
    retriever: FakeRetriever


def _happy_model() -> ScriptedChatModel:
    return ScriptedChatModel([findings_json(("Two remote days", ["S1"]))], [draft_json()])


async def _harness(model: ChatModel | None) -> AsyncIterator[ReportHarness]:
    repo = InMemoryReportRunRepository()
    retriever = FakeRetriever([chunk("Staff may work remotely two days a week.", score=0.9)])
    service = ReportService(repo, retriever=retriever, chat_model=model, settings=ReportSettings(min_score=0.5))

    deps = AppDependencies(
        chat_service=ChatService(InMemoryConversationRepository(), model, history_limit=4),
        chat_model=model,
        check_database=DatabaseProbe(),
        retriever=retriever,
        report_service=service,
    )
    app = create_app(make_settings(), dependencies=deps)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        yield ReportHarness(client=client, service=service, repo=repo, retriever=retriever)
    await service.wait_for_runs()


@pytest.fixture
async def h() -> AsyncIterator[ReportHarness]:
    async for harness in _harness(_happy_model()):
        yield harness


async def test_create_returns_202_queued_run(h: ReportHarness) -> None:
    response = await h.client.post("/reports", json={"question": f"  {QUESTION}  "})

    assert response.status_code == 202
    body = response.json()
    assert set(body) == RUN_KEYS | {"report"}
    assert body["status"] == "queued"
    assert body["question"] == QUESTION
    assert body["document_ids"] is None
    assert body["report"] is None
    assert body["model"] == "scripted-1"
    assert [s["name"] for s in body["stages"]] == ["validate", "retrieve", "analyze", "draft", "verify"]
    assert all(set(s) == STAGE_KEYS and s["status"] == "pending" for s in body["stages"])
    assert body["created_at"].endswith("Z")


async def test_poll_until_succeeded(h: ReportHarness) -> None:
    created = (await h.client.post("/reports", json={"question": QUESTION})).json()
    await h.service.wait_for_runs()

    response = await h.client.get(f"/reports/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert {s["status"] for s in body["stages"]} == {"done"}
    assert all(s["finished_at"].endswith("Z") for s in body["stages"])
    report = body["report"]
    assert report["title"] == "Remote work policy"
    assert report["sections"] == [{"heading": "Eligibility", "body": "All staff after probation [S1]."}]
    (ref,) = report["references"]
    assert set(ref) == {
        "marker",
        "chunk_id",
        "document_id",
        "document_title",
        "excerpt",
        "location",
        "document_deleted",
    }
    assert ref["marker"] == "S1"
    assert ref["document_deleted"] is False
    assert ref["location"] == {"char_start": 0, "char_end": 40, "page": 2, "heading_path": ["Intro"]}


async def test_document_scope_is_deduplicated_and_passed_to_retrieval(h: ReportHarness) -> None:
    doc = str(uuid.uuid4())
    created = (await h.client.post("/reports", json={"question": QUESTION, "document_ids": [doc, doc]})).json()
    await h.service.wait_for_runs()

    assert created["document_ids"] == [doc]
    assert h.retriever.calls[0][2] == [uuid.UUID(doc)]
    body = (await h.client.get(f"/reports/{created['id']}")).json()
    assert body["stages"][0]["detail"] == "Scope: 1 document"


async def test_list_is_newest_first_without_report_bodies(h: ReportHarness) -> None:
    first = (await h.client.post("/reports", json={"question": QUESTION})).json()
    await h.service.wait_for_runs()
    second = (await h.client.post("/reports", json={"question": "Another question about laptops?"})).json()
    await h.service.wait_for_runs()

    response = await h.client.get("/reports")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["id"] for i in items] == [second["id"], first["id"]]
    assert all(set(i) == RUN_KEYS for i in items)


async def test_unknown_report_is_404_envelope(h: ReportHarness) -> None:
    response = await h.client.get(f"/reports/{uuid.uuid4()}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": "too short"},
        {"question": "          padded         "},
        {"question": "x" * 1001},
        {"question": QUESTION, "document_ids": []},
        {"question": QUESTION, "document_ids": ["not-a-uuid"]},
        {"question": QUESTION, "document_ids": [str(uuid.uuid4()) for _ in range(51)]},
        {"question": QUESTION, "extra": True},
    ],
)
async def test_invalid_requests_are_422(h: ReportHarness, payload: dict[str, Any]) -> None:
    response = await h.client.post("/reports", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert h.repo.runs == {}


async def test_no_model_is_503_and_nothing_stored() -> None:
    async for harness in _harness(None):
        response = await harness.client.post("/reports", json={"question": QUESTION})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_not_configured"
        assert harness.repo.runs == {}


async def test_reports_unavailable_without_service() -> None:
    deps = AppDependencies(
        chat_service=ChatService(InMemoryConversationRepository(), None, history_limit=4),
        chat_model=None,
        check_database=DatabaseProbe(),
    )
    app = create_app(make_settings(), dependencies=deps)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
        response = await client.get("/reports")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


async def test_startup_hook_runs_in_lifespan() -> None:
    ran: list[bool] = []

    async def startup() -> None:
        ran.append(True)

    deps = AppDependencies(
        chat_service=ChatService(InMemoryConversationRepository(), None, history_limit=4),
        chat_model=None,
        check_database=DatabaseProbe(),
        startup=startup,
    )
    app = create_app(make_settings(), dependencies=deps)
    async with app.router.lifespan_context(app):
        assert ran == [True]
