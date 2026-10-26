"""Deterministic local stub, selected only by an explicit `LLM_PROVIDER=fake`.

It echoes the last user message word by word so the UI can be developed
without a key. A message starting with `!fake:<scenario>` triggers a failure,
which makes every error path reachable from the browser:

    !fake:auth  !fake:rate_limited  !fake:timeout  !fake:unavailable  !fake:midstream

When the system prompt carries `<evidence label="Sn">` blocks (chat with documents),
the echo ends with a citation of the first label, so grounded replies are demoable.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from agentops.domain.errors import LLMAuthFailed, LLMError, LLMRateLimited, LLMTimeout, LLMUnavailable
from agentops.domain.models import ChatChunk, ChatTurn, Role, StreamEnd, TextDelta, Usage

FAKE_MODEL = "fake-echo"

_EVIDENCE_LABEL = re.compile(r'<evidence label="(S\d+)"')

_SCENARIOS: dict[str, type[LLMError]] = {
    "auth": LLMAuthFailed,
    "rate_limited": LLMRateLimited,
    "timeout": LLMTimeout,
    "unavailable": LLMUnavailable,
}


class FakeEchoChatModel:
    provider = "fake"
    model = FAKE_MODEL

    def __init__(self, *, delay_s: float = 0.03) -> None:
        self._delay_s = delay_s

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        last = next((t.content for t in reversed(history) if t.role is Role.USER), "")
        scenario = last.removeprefix("!fake:").split()[0] if last.startswith("!fake:") and len(last) > 6 else None
        if scenario in _SCENARIOS:
            raise _SCENARIOS[scenario]()

        reply = f"[fake-echo] You said: {last}"
        if label := _EVIDENCE_LABEL.search(system_prompt):
            reply += f" (see [{label.group(1)}])"
        words = reply.split(" ")
        for index, word in enumerate(words):
            if scenario == "midstream" and index == 2:
                raise LLMUnavailable()
            await asyncio.sleep(self._delay_s)
            yield TextDelta(word if index == 0 else " " + word)
        yield StreamEnd(Usage(input_tokens=sum(len(t.content.split()) for t in history), output_tokens=len(words)))
