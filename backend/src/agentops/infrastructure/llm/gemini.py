"""Google Gemini via the `google-genai` SDK (`client.aio.models.generate_content_stream`).

SDK retries are disabled (`HttpRetryOptions(attempts=1)`); `ResilientChatModel`
owns retries. Gemini sends no Retry-After header, so 429s carry no hint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import httpx
from google import genai
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

_ROLES = {Role.USER: "user", Role.ASSISTANT: "model"}


class GeminiChatModel:
    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int,
        timeout_s: float,
        client: genai.Client | None = None,
    ) -> None:
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        contents = [types.Content(role=_ROLES[t.role], parts=[types.Part(text=t.content)]) for t in history]
        config = types.GenerateContentConfig(
            system_instruction=system_prompt, max_output_tokens=self._max_output_tokens
        )
        usage = Usage()
        try:
            responses = await self._client.aio.models.generate_content_stream(
                model=self.model, contents=contents, config=config
            )
            async for response in responses:
                meta = response.usage_metadata
                if meta is not None:
                    usage = Usage(meta.prompt_token_count, meta.candidates_token_count)
                text = response.text
                if text:
                    yield TextDelta(text)
        except (genai_errors.APIError, httpx.TransportError) as exc:
            raise map_gemini_error(exc) from exc
        yield StreamEnd(usage)


def map_gemini_error(exc: Exception) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeout()
    if isinstance(exc, httpx.TransportError):
        return LLMUnavailable()
    if isinstance(exc, genai_errors.APIError):
        code = exc.code
        if code in (401, 402, 403):
            # 402 (billing/payment required) is a credential/account problem, not transient.
            return LLMAuthFailed(provider_status=code)
        if code == 429:
            return LLMRateLimited(retry_after_s=None, provider_status=code)
        if code in (408, 504):
            return LLMTimeout(provider_status=code)
        if code >= 500:
            return LLMUnavailable(provider_status=code)
        return LLMRequestRejected(provider_status=code)
    return LLMUnavailable()
