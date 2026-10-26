"""Groq via its OpenAI-compatible endpoint, using the `openai` SDK.

The OpenAI SDK is used rather than `groq` because the wire protocol is the
de-facto standard: the same adapter works for any OpenAI-compatible host by
changing `base_url`. SDK retries are disabled; `ResilientChatModel` owns them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam

from agentops.domain.errors import (
    LLMAuthFailed,
    LLMError,
    LLMRateLimited,
    LLMRequestRejected,
    LLMTimeout,
    LLMUnavailable,
)
from agentops.domain.models import ChatChunk, ChatTurn, StreamEnd, TextDelta, Usage
from agentops.infrastructure.llm._http import parse_retry_after

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqChatModel:
    provider = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int,
        timeout_s: float,
        base_url: str = GROQ_BASE_URL,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=0
        )

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
        messages += [{"role": turn.role.value, "content": turn.content} for turn in history]  # type: ignore[misc]
        usage = Usage()
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=self._max_output_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async with response:
                async for chunk in response:
                    usage = _usage(chunk) or usage
                    for choice in chunk.choices:
                        if choice.delta.content:
                            yield TextDelta(choice.delta.content)
        except openai.APIError as exc:
            raise map_openai_error(exc) from exc
        yield StreamEnd(usage)


def _usage(chunk: Any) -> Usage | None:
    raw = chunk.usage
    if raw is None:
        # Older Groq responses put usage under the `x_groq` extension field.
        extra = (getattr(chunk, "model_extra", None) or {}).get("x_groq") or {}
        raw_dict = extra.get("usage") if isinstance(extra, dict) else None
        if not raw_dict:
            return None
        return Usage(raw_dict.get("prompt_tokens"), raw_dict.get("completion_tokens"))
    return Usage(raw.prompt_tokens, raw.completion_tokens)


def map_openai_error(exc: openai.APIError) -> LLMError:
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeout()
    if isinstance(exc, openai.APIConnectionError):
        return LLMUnavailable()
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return LLMAuthFailed(provider_status=status)
        if status == 429:
            return LLMRateLimited(
                retry_after_s=parse_retry_after(exc.response.headers.get("retry-after")), provider_status=status
            )
        if status == 408:
            return LLMTimeout(provider_status=status)
        if status == 498 or status >= 500:
            return LLMUnavailable(provider_status=status)
        return LLMRequestRejected(provider_status=status)
    # Error reported inside an otherwise successful stream.
    return LLMUnavailable()
