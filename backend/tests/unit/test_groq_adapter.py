from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from agentops.domain.errors import (
    LLMAuthFailed,
    LLMError,
    LLMRateLimited,
    LLMRequestRejected,
    LLMTimeout,
    LLMUnavailable,
)
from agentops.domain.models import ChatChunk, ChatTurn, Role, StreamEnd, TextDelta, Usage
from agentops.infrastructure.llm.groq import GroqChatModel

KEY = "gsk_test_key_never_logged"
HISTORY = [ChatTurn(Role.USER, "Hi"), ChatTurn(Role.ASSISTANT, "Hello!"), ChatTurn(Role.USER, "How are you?")]


def _chunk(**fields: Any) -> str:
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "llama-3.3-70b-versatile"}
    return "data: " + json.dumps(base | fields) + "\n\n"


def _model(handler: Callable[[httpx2.Request], httpx2.Response]) -> GroqChatModel:
    client = openai.AsyncOpenAI(
        api_key=KEY,
        base_url="https://api.groq.test/openai/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return GroqChatModel(
        api_key=KEY, model="llama-3.3-70b-versatile", max_output_tokens=256, timeout_s=5, client=client
    )


async def _collect(model: GroqChatModel) -> list[ChatChunk]:
    return [c async for c in model.stream(HISTORY, system_prompt="Be nice.")]


async def test_streams_deltas_and_usage_and_sends_expected_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = (
            _chunk(choices=[{"index": 0, "delta": {"role": "assistant", "content": "Fine"}, "finish_reason": None}])
            + _chunk(choices=[{"index": 0, "delta": {"content": ", thanks"}, "finish_reason": "stop"}])
            + _chunk(choices=[], usage={"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23})
            + "data: [DONE]\n\n"
        )
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    chunks = await _collect(_model(handler))

    assert chunks == [TextDelta("Fine"), TextDelta(", thanks"), StreamEnd(Usage(20, 3))]
    assert seen["auth"] == f"Bearer {KEY}"
    body = seen["body"]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_completion_tokens"] == 256
    assert body["messages"] == [
        {"role": "system", "content": "Be nice."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ]


async def test_usage_from_x_groq_extension() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = (
            _chunk(
                choices=[{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
                x_groq={"usage": {"prompt_tokens": 4, "completion_tokens": 1}},
            )
            + "data: [DONE]\n\n"
        )
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    assert (await _collect(_model(handler)))[-1] == StreamEnd(Usage(4, 1))


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (401, {}, LLMAuthFailed),
        (403, {}, LLMAuthFailed),
        (429, {"retry-after": "12"}, LLMRateLimited),
        (400, {}, LLMRequestRejected),
        (404, {}, LLMRequestRejected),
        (408, {}, LLMTimeout),
        (498, {}, LLMUnavailable),
        (500, {}, LLMUnavailable),
        (503, {}, LLMUnavailable),
    ],
)
async def test_http_errors_are_mapped(status: int, headers: dict[str, str], expected: type[LLMError]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "nope", "type": "x"}}, headers=headers)

    with pytest.raises(expected) as info:
        await _collect(_model(handler))
    assert info.value.provider_status == status
    assert "nope" not in info.value.message
    if expected is LLMRateLimited:
        assert isinstance(info.value, LLMRateLimited)
        assert info.value.retry_after_s == 12.0


async def test_rate_limit_without_retry_after() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": {"message": "slow down"}})

    with pytest.raises(LLMRateLimited) as info:
        await _collect(_model(handler))
    assert info.value.retry_after_s is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [(httpx2.ReadTimeout("t"), LLMTimeout), (httpx2.ConnectError("c"), LLMUnavailable)],
)
async def test_transport_errors_are_mapped(exc: Exception, expected: type[LLMError]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    with pytest.raises(expected):
        await _collect(_model(handler))


async def test_error_inside_stream_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = (
            _chunk(choices=[{"index": 0, "delta": {"content": "par"}, "finish_reason": None}])
            + 'data: {"error": {"message": "overloaded"}}\n\n'
        )
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    received: list[ChatChunk] = []
    with pytest.raises(LLMUnavailable):
        async for chunk in _model(handler).stream(HISTORY, system_prompt="s"):
            received.append(chunk)
    assert received == [TextDelta("par")]


def test_sdk_retries_are_disabled_by_default() -> None:
    model = GroqChatModel(api_key=KEY, model="m", max_output_tokens=1, timeout_s=3)

    assert model._client.max_retries == 0
