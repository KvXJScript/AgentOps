from __future__ import annotations

import asyncio

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
from agentops.infrastructure.llm.resilience import ResilientChatModel, RetryPolicy
from tests.fakes import ScriptedChatModel, ScriptStep

HISTORY = [ChatTurn(Role.USER, "hi")]


class RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def resilient(
    *scripts: list[ScriptStep], max_retries: int = 2, timeout_s: float = 5.0, rand: float = 0.5
) -> tuple[ResilientChatModel, ScriptedChatModel, RecordingSleep]:
    inner = ScriptedChatModel(*scripts)
    sleep = RecordingSleep()
    model = ResilientChatModel(
        inner,
        policy=RetryPolicy(max_retries=max_retries, base_delay_s=1.0, max_delay_s=4.0, max_retry_after_s=10.0),
        timeout_s=timeout_s,
        sleep=sleep,
        rand=lambda: rand,
    )
    return model, inner, sleep


async def collect(model: ResilientChatModel) -> list[ChatChunk]:
    return [chunk async for chunk in model.stream(HISTORY, system_prompt="sys")]


async def test_retries_transient_errors_then_succeeds() -> None:
    model, inner, sleep = resilient([LLMUnavailable()], [LLMTimeout()], ["ok"])

    chunks = await collect(model)

    assert chunks == [TextDelta("ok"), StreamEnd(Usage())]
    assert len(inner.calls) == 3
    assert sleep.delays == [0.75, 1.5]  # ceilings 1s, 2s with rand=0.5 -> 75%


async def test_gives_up_after_max_retries() -> None:
    model, inner, sleep = resilient([LLMUnavailable()], [LLMUnavailable()], [LLMUnavailable()], ["never"])

    with pytest.raises(LLMUnavailable):
        await collect(model)
    assert len(inner.calls) == 3
    assert len(sleep.delays) == 2


@pytest.mark.parametrize("error", [LLMAuthFailed(), LLMRequestRejected()])
async def test_does_not_retry_permanent_errors(error: LLMError) -> None:
    model, inner, sleep = resilient([error], ["never"])

    with pytest.raises(type(error)):
        await collect(model)
    assert len(inner.calls) == 1
    assert sleep.delays == []


async def test_never_retries_after_first_token() -> None:
    model, inner, sleep = resilient(["partial", LLMUnavailable()], ["never"])

    received: list[ChatChunk] = []
    with pytest.raises(LLMUnavailable):
        async for chunk in model.stream(HISTORY, system_prompt="sys"):
            received.append(chunk)
    assert received == [TextDelta("partial")]
    assert len(inner.calls) == 1
    assert sleep.delays == []


async def test_honours_retry_after() -> None:
    model, _, sleep = resilient([LLMRateLimited(retry_after_s=3.0)], ["ok"])

    await collect(model)

    assert sleep.delays == [3.0]


async def test_retry_after_beyond_cap_is_surfaced_not_waited() -> None:
    model, inner, sleep = resilient([LLMRateLimited(retry_after_s=60.0)], ["ok"])

    with pytest.raises(LLMRateLimited) as info:
        await collect(model)
    assert info.value.retry_after_s == 60.0
    assert len(inner.calls) == 1
    assert sleep.delays == []


@pytest.mark.parametrize("rand", [0.0, 0.25, 0.999999])
def test_backoff_jitter_is_bounded(rand: float) -> None:
    policy = RetryPolicy(base_delay_s=0.5, max_delay_s=8.0)
    for retry in range(1, 10):
        ceiling = min(8.0, 0.5 * 2 ** (retry - 1))
        delay = policy.backoff_s(retry, rand)
        assert ceiling / 2 <= delay <= ceiling


async def test_timeout_waiting_for_first_chunk_is_llm_timeout_and_retried() -> None:
    model, inner, sleep = resilient([1.0, "late"], ["ok"], timeout_s=0.01)

    chunks = await collect(model)

    assert chunks[0] == TextDelta("ok")
    assert len(inner.calls) == 2
    assert len(sleep.delays) == 1


async def test_stall_after_first_token_times_out_without_retry() -> None:
    model, inner, _ = resilient(["first", 1.0, "late"], ["never"], timeout_s=0.01)

    with pytest.raises(LLMTimeout):
        await collect(model)
    assert len(inner.calls) == 1


async def test_exposes_inner_identity() -> None:
    model, _, _ = resilient(["ok"])

    assert (model.provider, model.model) == ("scripted", "scripted-1")


async def test_cancellation_is_not_swallowed() -> None:
    model, _, _ = resilient([5.0, "late"], timeout_s=10)

    task = asyncio.create_task(collect(model))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
