from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.domain.errors import ConversationNotFound
from agentops.domain.models import Conversation, Message, MessageStatus, Role
from agentops.infrastructure.db.repository import SqlConversationRepository
from agentops.infrastructure.db.session import create_engine, ping
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _conversation(title: str = "New conversation", at: datetime = T0) -> Conversation:
    return Conversation(id=uuid.uuid4(), title=title, created_at=at, updated_at=at)


def _message(
    conv: Conversation, role: Role, content: str, at: datetime, status: MessageStatus = MessageStatus.COMPLETE
) -> Message:
    return Message(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        role=role,
        content=content,
        status=status,
        created_at=at,
        model="m" if role is Role.ASSISTANT else None,
        error_code=None if status is MessageStatus.COMPLETE else "llm_timeout",
        input_tokens=5 if role is Role.ASSISTANT else None,
        output_tokens=2 if role is Role.ASSISTANT else None,
    )


@pytest.fixture
def repo(session_factory: async_sessionmaker[AsyncSession]) -> SqlConversationRepository:
    return SqlConversationRepository(session_factory)


async def test_create_get_and_list_order(repo: SqlConversationRepository) -> None:
    older, newer = _conversation("older", T0), _conversation("newer", T0 + timedelta(minutes=1))
    await repo.create(older)
    await repo.create(newer)

    assert await repo.get(older.id) == older
    assert await repo.get(uuid.uuid4()) is None
    assert [c.title for c in await repo.list_recent(10)] == ["newer", "older"]
    assert len(await repo.list_recent(1)) == 1


async def test_add_message_bumps_updated_at_and_title(repo: SqlConversationRepository) -> None:
    conv = _conversation()
    await repo.create(conv)
    later = T0 + timedelta(hours=1)

    await repo.add_message(_message(conv, Role.USER, "hello", later), new_title="hello")

    stored = await repo.get(conv.id)
    assert stored is not None
    assert (stored.title, stored.updated_at) == ("hello", later)


async def test_messages_round_trip_oldest_first(repo: SqlConversationRepository) -> None:
    conv = _conversation()
    await repo.create(conv)
    first = _message(conv, Role.USER, "zażółć gęślą jaźń", T0 + timedelta(seconds=1))
    second = _message(conv, Role.ASSISTANT, "", T0 + timedelta(seconds=2), MessageStatus.FAILED)
    await repo.add_message(second)
    await repo.add_message(first)

    assert await repo.list_messages(conv.id) == [first, second]


async def test_recent_complete_messages(repo: SqlConversationRepository) -> None:
    conv = _conversation()
    await repo.create(conv)
    msgs = [
        _message(conv, Role.USER, "u1", T0 + timedelta(seconds=1)),
        _message(conv, Role.ASSISTANT, "a1", T0 + timedelta(seconds=2)),
        _message(conv, Role.USER, "u2", T0 + timedelta(seconds=3)),
        _message(conv, Role.ASSISTANT, "partial", T0 + timedelta(seconds=4), MessageStatus.INCOMPLETE),
        _message(conv, Role.USER, "u3", T0 + timedelta(seconds=5)),
    ]
    for m in msgs:
        await repo.add_message(m)

    assert [m.content for m in await repo.recent_complete_messages(conv.id, 3)] == ["a1", "u2", "u3"]


async def test_add_message_to_missing_conversation_raises(repo: SqlConversationRepository) -> None:
    with pytest.raises(ConversationNotFound):
        await repo.add_message(_message(_conversation(), Role.USER, "x", T0))


async def test_delete_cascades(
    repo: SqlConversationRepository, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    conv = _conversation()
    await repo.create(conv)
    await repo.add_message(_message(conv, Role.USER, "x", T0))

    assert await repo.delete(conv.id) is True
    assert await repo.delete(conv.id) is False
    async with session_factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM messages"))).scalar_one() == 0


async def test_user_messages_must_be_complete(session_factory: async_sessionmaker[AsyncSession]) -> None:
    repo = SqlConversationRepository(session_factory)
    conv = _conversation()
    await repo.create(conv)

    with pytest.raises(Exception, match="ck_messages_user_messages_complete"):
        await repo.add_message(_message(conv, Role.USER, "x", T0, MessageStatus.FAILED))


async def test_ping(database_url: str) -> None:
    engine = create_engine(database_url)
    assert await ping(engine) is True
    await engine.dispose()
    unreachable = create_engine("postgresql+asyncpg://x:y@127.0.0.1:1/none", connect_timeout_s=0.5)
    assert await ping(unreachable) is False
    await unreachable.dispose()


async def test_schema_has_vector_extension_and_tables(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        ext = await session.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'"))
        assert ext.scalar_one_or_none() == "vector"
        tables = await session.run_sync(lambda s: inspect(s.connection()).get_table_names())
    assert {"conversations", "messages", "alembic_version"} <= set(tables)


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
