from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from agentops.domain.errors import (
    LLMAuthFailed,
    LLMError,
    LLMRateLimited,
    LLMRequestRejected,
    LLMTimeout,
    LLMUnavailable,
)
from agentops.domain.models import ChatChunk, ChatTurn, Role, StreamEnd, TextDelta, Usage
from agentops.infrastructure.llm.gemini import GeminiChatModel

HISTORY = [ChatTurn(Role.USER, "Hi"), ChatTurn(Role.ASSISTANT, "Hello!"), ChatTurn(Role.USER, "Weather?")]


def _response(text: str | None, usage: tuple[int, int] | None = None) -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0], candidates_token_count=usage[1]
        )
        if usage
        else None,
    )


class FakeModels:
    def __init__(self, items: list[types.GenerateContentResponse | Exception], fail_on_call: Exception | None = None):
        self.items = items
        self.fail_on_call = fail_on_call
        self.kwargs: dict[str, Any] = {}

    async def generate_content_stream(self, **kwargs: Any) -> AsyncIterator[types.GenerateContentResponse]:
        self.kwargs = kwargs
        if self.fail_on_call:
            raise self.fail_on_call

        async def gen() -> AsyncIterator[types.GenerateContentResponse]:
            for item in self.items:
                if isinstance(item, Exception):
                    raise item
                yield item

        return gen()


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.aio = type("Aio", (), {"models": models})()


def _model(models: FakeModels) -> GeminiChatModel:
    return GeminiChatModel(
        api_key="unused",
        model="gemini-3.8-flash",
        max_output_tokens=128,
        timeout_s=5,
        client=FakeClient(models),  # type: ignore[arg-type]
    )


async def _collect(model: GeminiChatModel) -> list[ChatChunk]:
    return [c async for c in model.stream(HISTORY, system_prompt="Be nice.")]


async def test_streams_text_and_usage_with_mapped_roles() -> None:
    models = FakeModels([_response("Sun"), _response(None), _response("ny", usage=(30, 2))])

    chunks = await _collect(_model(models))

    assert chunks == [TextDelta("Sun"), TextDelta("ny"), StreamEnd(Usage(30, 2))]
    assert models.kwargs["model"] == "gemini-3.8-flash"
    assert [c.role for c in models.kwargs["contents"]] == ["user", "model", "user"]
    config: types.GenerateContentConfig = models.kwargs["config"]
    assert config.system_instruction == "Be nice."
    assert config.max_output_tokens == 128


def _api_error(code: int) -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"code": code, "message": "raw provider text", "status": "X"}})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, LLMAuthFailed),
        (402, LLMAuthFailed),
        (403, LLMAuthFailed),
        (429, LLMRateLimited),
        (400, LLMRequestRejected),
        (404, LLMRequestRejected),
        (500, LLMUnavailable),
        (503, LLMUnavailable),
        (504, LLMTimeout),
    ],
)
async def test_api_errors_are_mapped(code: int, expected: type[LLMError]) -> None:
    with pytest.raises(expected) as info:
        await _collect(_model(FakeModels([], fail_on_call=_api_error(code))))
    assert info.value.provider_status == code
    assert "raw provider text" not in info.value.message
    if isinstance(info.value, LLMRateLimited):
        assert info.value.retry_after_s is None


@pytest.mark.parametrize(
    ("exc", "expected"), [(httpx.ReadTimeout("t"), LLMTimeout), (httpx.ConnectError("c"), LLMUnavailable)]
)
async def test_transport_errors_are_mapped(exc: Exception, expected: type[LLMError]) -> None:
    with pytest.raises(expected):
        await _collect(_model(FakeModels([], fail_on_call=exc)))


async def test_error_mid_stream_after_text() -> None:
    received: list[ChatChunk] = []
    with pytest.raises(LLMUnavailable):
        async for chunk in _model(FakeModels([_response("par"), _api_error(503)])).stream(HISTORY, system_prompt="s"):
            received.append(chunk)
    assert received == [TextDelta("par")]


def test_sdk_retries_are_disabled() -> None:
    model = GeminiChatModel(api_key="k", model="m", max_output_tokens=1, timeout_s=3)

    options = model._client._api_client._http_options
    assert options.retry_options is not None
    assert options.retry_options.attempts == 1
    assert options.timeout == 3000
