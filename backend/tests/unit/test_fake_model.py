from __future__ import annotations

import pytest

from agentops.domain.errors import LLMAuthFailed, LLMError, LLMRateLimited, LLMTimeout, LLMUnavailable
from agentops.domain.models import ChatTurn, Role, StreamEnd, TextDelta
from agentops.infrastructure.llm.fake import FakeEchoChatModel


async def test_echoes_last_user_message() -> None:
    model = FakeEchoChatModel(delay_s=0)

    chunks = [c async for c in model.stream([ChatTurn(Role.USER, "hello world")], system_prompt="s")]

    text = "".join(c.text for c in chunks if isinstance(c, TextDelta))
    assert text == "[fake-echo] You said: hello world"
    assert isinstance(chunks[-1], StreamEnd)
    assert (model.provider, model.model) == ("fake", "fake-echo")


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("auth", LLMAuthFailed),
        ("rate_limited", LLMRateLimited),
        ("timeout", LLMTimeout),
        ("unavailable", LLMUnavailable),
    ],
)
async def test_scenarios_raise(scenario: str, error: type[LLMError]) -> None:
    model = FakeEchoChatModel(delay_s=0)

    with pytest.raises(error):
        _ = [c async for c in model.stream([ChatTurn(Role.USER, f"!fake:{scenario}")], system_prompt="s")]


async def test_midstream_scenario_fails_after_tokens() -> None:
    model = FakeEchoChatModel(delay_s=0)
    received = []

    with pytest.raises(LLMUnavailable):
        async for chunk in model.stream([ChatTurn(Role.USER, "!fake:midstream")], system_prompt="s"):
            received.append(chunk)
    assert len(received) == 2
