from __future__ import annotations

from typing import Any

import pytest

from agentops.domain.errors import LLMAuthFailed, LLMRateLimited, LLMTimeout, LLMUnavailable
from agentops.domain.models import MessageStatus, Role, Usage
from tests.conftest import Harness, parse_sse
from tests.fakes import ScriptedChatModel


async def _send(harness: Harness, content: str = "Hello there", conv_id: str | None = None) -> tuple[Any, str]:
    if conv_id is None:
        conv_id = (await harness.client.post("/conversations")).json()["id"]
    response = await harness.client.post(
        f"/conversations/{conv_id}/messages", json={"content": content}, headers={"X-Request-ID": "req-42"}
    )
    return response, conv_id


@pytest.mark.parametrize("chat_model", [ScriptedChatModel(["Hel", "lo", "!", Usage(12, 3)])])
async def test_success_stream_event_order_and_shape(harness: Harness, chat_model: ScriptedChatModel) -> None:
    response, conv_id = await _send(harness, "  Hello there  ")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-request-id"] == "req-42"
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["message.created", "token", "token", "token", "done"]

    created = events[0][1]
    assert created["user_message"]["role"] == "user"
    assert created["user_message"]["content"] == "Hello there"
    assert created["user_message"]["status"] == "complete"
    assert created["user_message"]["model"] is None
    assert [data["delta"] for _, data in events[1:4]] == ["Hel", "lo", "!"]

    done = events[-1][1]
    assert done["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert done["message"] == {
        "id": created["assistant_message_id"],
        "conversation_id": conv_id,
        "role": "assistant",
        "content": "Hello!",
        "status": "complete",
        "error_code": None,
        "model": "scripted-1",
        "created_at": done["message"]["created_at"],
    }


@pytest.mark.parametrize("chat_model", [ScriptedChatModel(["Hi", " you"])])
async def test_success_persists_both_messages(harness: Harness) -> None:
    _, conv_id = await _send(harness)
    await harness.service.wait_for_pending_writes()

    stored = harness.repo.messages
    assert [(m.role, m.status, m.content) for m in stored] == [
        (Role.USER, MessageStatus.COMPLETE, "Hello there"),
        (Role.ASSISTANT, MessageStatus.COMPLETE, "Hi you"),
    ]
    detail = (await harness.client.get(f"/conversations/{conv_id}")).json()
    assert [m["content"] for m in detail["messages"]] == ["Hello there", "Hi you"]


async def test_first_message_sets_title_once(harness: Harness) -> None:
    long = "Plan a  three-day trip to Kraków with museums, food tours and a day in the Tatra mountains"
    _, conv_id = await _send(harness, long)
    await _send(harness, "Second message should not rename", conv_id)

    title = (await harness.client.get(f"/conversations/{conv_id}")).json()["title"]
    assert title.startswith("Plan a three-day trip to Kraków")
    assert len(title) <= 60
    assert title.endswith("…")


async def test_custom_title_is_kept(harness: Harness) -> None:
    conv_id = (await harness.client.post("/conversations", json={"title": "Mine"})).json()["id"]
    await _send(harness, "Hello", conv_id)

    assert (await harness.client.get(f"/conversations/{conv_id}")).json()["title"] == "Mine"


@pytest.mark.parametrize("content", ["", "   \n\t", "x" * 4001])
async def test_invalid_content_is_rejected_before_anything_is_stored(harness: Harness, content: str) -> None:
    response, _ = await _send(harness, content)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert harness.repo.messages == []


async def test_content_at_max_length_is_accepted(harness: Harness) -> None:
    response, _ = await _send(harness, " " + "x" * 4000 + " ")

    assert response.status_code == 200


@pytest.mark.parametrize("chat_model", [None])
async def test_missing_credentials_is_503_and_nothing_stored(harness: Harness) -> None:
    response, _ = await _send(harness)

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "llm_not_configured",
        "message": "No language model is configured on the server.",
        "request_id": "req-42",
    }
    assert harness.repo.messages == []


@pytest.mark.parametrize(
    ("chat_model", "code", "retryable"),
    [
        (ScriptedChatModel([LLMAuthFailed(provider_status=401)]), "llm_auth_failed", False),
        (ScriptedChatModel([LLMTimeout()]), "llm_timeout", True),
        (ScriptedChatModel([LLMUnavailable(provider_status=503)]), "llm_unavailable", True),
        (ScriptedChatModel([RuntimeError("bug with secret")]), "internal_error", False),
    ],
)
async def test_failure_before_first_token_is_failed(harness: Harness, code: str, retryable: bool) -> None:
    response, _ = await _send(harness)
    await harness.service.wait_for_pending_writes()

    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["message.created", "error"]
    error = events[1][1]
    assert error == {
        "code": code,
        "message": error["message"],
        "retryable": retryable,
        "request_id": "req-42",
        "message_id": events[0][1]["assistant_message_id"],
    }
    assert "secret" not in error["message"]
    assistant = harness.repo.messages[-1]
    assert (assistant.status, assistant.content, assistant.error_code) == (MessageStatus.FAILED, "", code)
    assert str(assistant.id) == error["message_id"]


@pytest.mark.parametrize("chat_model", [ScriptedChatModel([LLMRateLimited(retry_after_s=7.0)])])
async def test_rate_limit_event_carries_retry_after(harness: Harness) -> None:
    response, _ = await _send(harness)

    name, error = parse_sse(response.text)[-1]
    assert name == "error"
    assert error["code"] == "llm_rate_limited"
    assert error["retryable"] is True
    assert error["retry_after_s"] == 7.0


@pytest.mark.parametrize("chat_model", [ScriptedChatModel(["Partial ", "answer", LLMUnavailable()])])
async def test_failure_after_tokens_keeps_partial_content(harness: Harness) -> None:
    response, conv_id = await _send(harness)
    await harness.service.wait_for_pending_writes()

    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["message.created", "token", "token", "error"]
    detail = (await harness.client.get(f"/conversations/{conv_id}")).json()
    assistant = detail["messages"][-1]
    assert assistant["status"] == "incomplete"
    assert assistant["content"] == "Partial answer"
    assert assistant["error_code"] == "llm_unavailable"


@pytest.mark.parametrize("chat_model", [ScriptedChatModel(["a1"], [LLMTimeout()], ["a3"], ["a4"], ["a5"])])
async def test_history_is_last_n_complete_messages(harness: Harness, chat_model: ScriptedChatModel) -> None:
    _, conv_id = await _send(harness, "u1")
    for content in ["u2", "u3", "u4"]:
        await _send(harness, content, conv_id)
        await harness.service.wait_for_pending_writes()

    history, system_prompt = chat_model.calls[-1]
    # history_limit=4 in the harness; the failed reply to u2 is excluded.
    assert [(t.role.value, t.content) for t in history] == [
        ("user", "u2"),
        ("user", "u3"),
        ("assistant", "a3"),
        ("user", "u4"),
    ]
    assert system_prompt
