from __future__ import annotations

import pytest

from agentops.application.chat_service import ChatService, CompletedEvent, FailedEvent, TokenEvent, title_from_content
from agentops.domain.errors import LLMNotConfigured
from agentops.domain.models import MessageStatus
from tests.fakes import InMemoryConversationRepository, ScriptedChatModel


async def test_client_disconnect_mid_stream_keeps_partial_answer() -> None:
    repo = InMemoryConversationRepository()
    service = ChatService(repo, ScriptedChatModel(["one ", "two ", "three"]), history_limit=10)
    conv = await service.create_conversation()

    reply = await service.send_message(conv.id, "hi")
    assert isinstance(await anext(reply.events), TokenEvent)
    await reply.events.aclose()
    await service.wait_for_pending_writes()

    assistant = repo.messages[-1]
    assert assistant.id == reply.assistant_message_id
    assert (assistant.status, assistant.content, assistant.error_code) == (MessageStatus.INCOMPLETE, "one ", None)


async def test_disconnect_before_any_token_records_failed() -> None:
    repo = InMemoryConversationRepository()
    service = ChatService(repo, ScriptedChatModel(["x"]), history_limit=10)
    conv = await service.create_conversation()

    reply = await service.send_message(conv.id, "hi")
    await reply.events.aclose()  # never started
    await service.wait_for_pending_writes()

    # An un-started generator runs no code, so nothing beyond the user message is stored.
    assert [m.role.value for m in repo.messages] == ["user"]


async def test_persistence_failure_after_stream_reports_internal_error() -> None:
    repo = InMemoryConversationRepository()
    service = ChatService(repo, ScriptedChatModel(["ok"]), history_limit=10)
    conv = await service.create_conversation()
    reply = await service.send_message(conv.id, "hi")
    repo.fail_add_message = True

    events = [event async for event in reply.events]

    assert isinstance(events[-1], FailedEvent)
    assert events[-1].error.code == "internal_error"


async def test_conversation_deleted_mid_stream_still_completes() -> None:
    repo = InMemoryConversationRepository()
    service = ChatService(repo, ScriptedChatModel(["ok"]), history_limit=10)
    conv = await service.create_conversation()
    reply = await service.send_message(conv.id, "hi")
    await service.delete_conversation(conv.id)

    events = [event async for event in reply.events]

    assert isinstance(events[-1], CompletedEvent)
    assert repo.messages == []


async def test_not_configured_raises_before_storing() -> None:
    repo = InMemoryConversationRepository()
    service = ChatService(repo, None, history_limit=10)
    conv = await service.create_conversation()

    with pytest.raises(LLMNotConfigured):
        await service.send_message(conv.id, "hi")
    assert repo.messages == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [("short", "short"), ("  many   spaces\nnewline ", "many spaces newline"), ("a" * 61, "a" * 59 + "…")],
)
def test_title_from_content(content: str, expected: str) -> None:
    assert title_from_content(content) == expected
