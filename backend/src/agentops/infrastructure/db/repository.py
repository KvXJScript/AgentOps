from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.domain.errors import ConversationNotFound
from agentops.domain.models import Conversation, Message, MessageStatus, Role
from agentops.infrastructure.db.models import ConversationRow, MessageRow


class SqlConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, conversation: Conversation) -> None:
        async with self._sessions.begin() as session:
            session.add(
                ConversationRow(
                    id=conversation.id,
                    title=conversation.title,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                )
            )

    async def get(self, conversation_id: UUID) -> Conversation | None:
        async with self._sessions() as session:
            row = await session.get(ConversationRow, conversation_id)
            return _conversation(row) if row else None

    async def list_recent(self, limit: int) -> list[Conversation]:
        stmt = select(ConversationRow).order_by(ConversationRow.updated_at.desc(), ConversationRow.id).limit(limit)
        async with self._sessions() as session:
            return [_conversation(row) for row in await session.scalars(stmt)]

    async def delete(self, conversation_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(ConversationRow).where(ConversationRow.id == conversation_id))
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_messages(self, conversation_id: UUID) -> list[Message]:
        stmt = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at, MessageRow.id)
        )
        async with self._sessions() as session:
            return [_message(row) for row in await session.scalars(stmt)]

    async def recent_complete_messages(self, conversation_id: UUID, limit: int) -> list[Message]:
        stmt = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id, MessageRow.status == MessageStatus.COMPLETE.value)
            .order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(stmt))
        return [_message(row) for row in reversed(rows)]

    async def add_message(self, message: Message, *, new_title: str | None = None) -> None:
        values: dict[str, object] = {"updated_at": message.created_at}
        if new_title is not None:
            values["title"] = new_title
        async with self._sessions.begin() as session:
            # Locks the conversation row, so a concurrent delete cannot slip in between.
            result = await session.execute(
                update(ConversationRow).where(ConversationRow.id == message.conversation_id).values(**values)
            )
            if not result.rowcount:  # type: ignore[attr-defined]
                raise ConversationNotFound()
            session.add(
                MessageRow(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    role=message.role.value,
                    content=message.content,
                    status=message.status.value,
                    error_code=message.error_code,
                    model=message.model,
                    input_tokens=message.input_tokens,
                    output_tokens=message.output_tokens,
                    created_at=message.created_at,
                )
            )


def _conversation(row: ConversationRow) -> Conversation:
    return Conversation(id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at)


def _message(row: MessageRow) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=Role(row.role),
        content=row.content,
        status=MessageStatus(row.status),
        error_code=row.error_code,
        model=row.model,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        created_at=row.created_at,
    )
