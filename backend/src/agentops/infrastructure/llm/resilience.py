"""The single place where LLM timeouts and retries are applied.

Provider adapters only translate SDK errors into `LLMError`s; `ResilientChatModel`
wraps any adapter with an inactivity timeout and bounded retries, so retries
never multiply across layers.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass

from agentops.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from agentops.domain.models import ChatChunk, ChatTurn, TextDelta
from agentops.domain.ports import ChatModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    # A provider asking us to wait longer than this gets its error surfaced
    # to the client (with retry_after_s) instead of holding the request open.
    max_retry_after_s: float = 10.0

    def backoff_s(self, retry_number: int, rand: float) -> float:
        """Exponential backoff with jitter in [ceiling/2, ceiling]; `retry_number` starts at 1."""
        ceiling = min(self.max_delay_s, self.base_delay_s * 2.0 ** (retry_number - 1))
        return ceiling / 2 + ceiling / 2 * rand


Sleep = Callable[[float], Awaitable[None]]


class ResilientChatModel:
    def __init__(
        self,
        inner: ChatModel,
        *,
        policy: RetryPolicy,
        timeout_s: float,
        sleep: Sleep = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._rand = rand

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model(self) -> str:
        return self._inner.model

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        attempt = 0
        while True:
            attempt += 1
            emitted = False
            started = time.monotonic()
            try:
                async for chunk in self._with_timeout(self._inner.stream(history, system_prompt=system_prompt)):
                    if isinstance(chunk, TextDelta):
                        emitted = True
                    yield chunk
                return
            except LLMError as exc:
                delay = self._retry_delay(exc, attempt, emitted)
                logger.warning(
                    "llm.attempt_failed",
                    extra={
                        "provider": self.provider,
                        "model": self.model,
                        "attempt": attempt,
                        "error_code": exc.code,
                        "provider_status": exc.provider_status,
                        "after_first_delta": emitted,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "retry_in_s": None if delay is None else round(delay, 3),
                    },
                )
                if delay is None:
                    raise
                await self._sleep(delay)

    def _retry_delay(self, exc: LLMError, attempt: int, emitted: bool) -> float | None:
        if emitted or not exc.retryable or attempt > self._policy.max_retries:
            return None
        backoff = self._policy.backoff_s(attempt, self._rand())
        if isinstance(exc, LLMRateLimited) and exc.retry_after_s is not None:
            if exc.retry_after_s > self._policy.max_retry_after_s:
                return None
            return max(exc.retry_after_s, backoff)
        return backoff

    async def _with_timeout(self, chunks: AsyncIterator[ChatChunk]) -> AsyncIterator[ChatChunk]:
        """Fail with `LLMTimeout` if the first chunk, or any later one, takes longer than the timeout."""
        iterator = aiter(chunks)
        try:
            while True:
                try:
                    async with asyncio.timeout(self._timeout_s):
                        chunk = await anext(iterator)
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise LLMTimeout() from exc
                yield chunk
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()
