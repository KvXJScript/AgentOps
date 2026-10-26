"""Minimal in-memory doubles for the eval runner (independent of `tests/`, so test refactors can't break it)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from agentops.domain.errors import ConversationNotFound
from agentops.domain.models import ChatChunk, ChatTurn, Conversation, Message, StreamEnd, TextDelta, Usage
from agentops.domain.retrieval import RetrievalTrace


class ScriptedModel:
    """Each `stream` call replies with the next scripted string (or raises it if it's an exception)."""

    provider = "scripted"
    model = "scripted-eval"

    def __init__(self, *replies: str | BaseException) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[list[ChatTurn], str]] = []

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        self.calls.append((list(history), system_prompt))
        reply = self._replies.pop(0) if self._replies else ""
        if isinstance(reply, BaseException):
            raise reply
        yield TextDelta(reply)
        yield StreamEnd(Usage())


class CallCap:
    """Wraps a real model and refuses to make more than `limit` calls (live mode)."""

    def __init__(self, inner: Any, limit: int) -> None:
        self._inner = inner
        self.limit = limit
        self.calls = 0

    @property
    def provider(self) -> str:
        return str(self._inner.provider)

    @property
    def model(self) -> str:
        return str(self._inner.model)

    async def stream(self, history: Sequence[ChatTurn], *, system_prompt: str) -> AsyncIterator[ChatChunk]:
        if self.calls >= self.limit:
            raise RuntimeError(f"live call cap of {self.limit} reached")
        self.calls += 1
        async for chunk in self._inner.stream(history, system_prompt=system_prompt):
            yield chunk


class MemoryConversations:
    def __init__(self) -> None:
        self.conversations: dict[UUID, Conversation] = {}
        self.messages: list[Message] = []
        self.retrievals: dict[UUID, RetrievalTrace] = {}

    async def create(self, conversation: Conversation) -> None:
        self.conversations[conversation.id] = conversation

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return self.conversations.get(conversation_id)

    async def list_recent(self, limit: int) -> list[Conversation]:
        return list(self.conversations.values())[:limit]

    async def delete(self, conversation_id: UUID) -> bool:
        return self.conversations.pop(conversation_id, None) is not None

    async def list_messages(self, conversation_id: UUID) -> list[Message]:
        return [m for m in self.messages if m.conversation_id == conversation_id]

    async def recent_complete_messages(self, conversation_id: UUID, limit: int) -> list[Message]:
        return [m for m in await self.list_messages(conversation_id) if m.status.value == "complete"][-limit:]

    async def add_message(
        self, message: Message, *, new_title: str | None = None, retrieval: RetrievalTrace | None = None
    ) -> None:
        conversation = self.conversations.get(message.conversation_id)
        if conversation is None:
            raise ConversationNotFound()
        self.conversations[conversation.id] = replace(conversation, title=new_title or conversation.title)
        self.messages.append(message)
        if retrieval is not None:
            self.retrievals[message.id] = retrieval

    async def get_retrieval(self, message_id: UUID) -> RetrievalTrace | None:
        return self.retrievals.get(message_id)


class StageRecorder:
    """`StageTracker` for the report graph: records stage order, attempts and details."""

    def __init__(self) -> None:
        self.stages: list[tuple[str, int, str | None]] = []

    async def stage_started(self, name: Any) -> Any:
        from agentops.application.reports.nodes import StageContext

        return StageContext()

    async def stage_finished(self, name: Any, ctx: Any) -> None:
        self.stages.append((name.value, ctx.attempts, ctx.detail))


# --- Phase 4 doubles (only used when the proposal code is present) ---------------------------------
class MemoryReports:
    def __init__(self, runs: Sequence[Any] = ()) -> None:
        self.runs = {r.id: r for r in runs}

    async def get(self, run_id: UUID) -> Any:
        return self.runs.get(run_id)

    async def create(self, run: Any) -> None:
        self.runs[run.id] = run

    async def save(self, run: Any) -> None:
        self.runs[run.id] = run

    async def list_recent(self, limit: int) -> list[Any]:
        return list(self.runs.values())[:limit]

    async def list_active(self) -> list[Any]:
        return []


class MemoryProposals:
    def __init__(self) -> None:
        self.rows: dict[UUID, Any] = {}

    async def create_pending(self, proposal: Any, *, now: datetime) -> tuple[Any, bool]:
        live = await self.find_live_pending(proposal.source_id, now=now)
        if live is not None:
            return live, False
        self.rows[proposal.id] = proposal
        return proposal, True

    async def get(self, proposal_id: UUID) -> Any:
        return self.rows.get(proposal_id)

    async def find_live_pending(self, source_id: UUID, *, now: datetime) -> Any:
        for p in self.rows.values():
            if p.source_id == source_id and p.status.value == "pending" and now < p.expires_at:
                return p
        return None

    async def list_recent(self, *, status: Any, now: datetime, limit: int) -> list[Any]:
        return list(self.rows.values())[:limit]

    async def claim_for_execution(self, proposal_id: UUID, *, payload_hash: str, now: datetime) -> bool:
        from agentops.domain.proposals import ProposalStatus

        p = self.rows.get(proposal_id)
        if p is None or p.status is not ProposalStatus.PENDING or p.payload_hash != payload_hash:
            return False
        if now >= p.expires_at:
            return False
        self.rows[proposal_id] = replace(p, status=ProposalStatus.EXECUTING)
        return True

    async def finish_execution(
        self, proposal_id: UUID, *, status: Any, at: datetime, result_issue_id: str | None, error_code: str | None
    ) -> Any:
        p = self.rows.get(proposal_id)
        if p is None or p.status.value != "executing":
            return None
        self.rows[proposal_id] = replace(
            p, status=status, decided_at=at, result_issue_id=result_issue_id, error_code=error_code
        )
        return self.rows[proposal_id]

    async def reject(self, proposal_id: UUID, *, now: datetime) -> bool:
        from agentops.domain.proposals import ProposalStatus

        p = self.rows.get(proposal_id)
        if p is None or p.status is not ProposalStatus.PENDING or now >= p.expires_at:
            return False
        self.rows[proposal_id] = replace(p, status=ProposalStatus.REJECTED, decided_at=now)
        return True

    async def mark_expired(self, proposal_id: UUID, *, now: datetime) -> None:
        from agentops.domain.proposals import ProposalStatus

        p = self.rows.get(proposal_id)
        if p is not None and p.status is ProposalStatus.PENDING and now >= p.expires_at:
            self.rows[proposal_id] = replace(p, status=ProposalStatus.EXPIRED)

    async def list_executing(self) -> list[Any]:
        return [p for p in self.rows.values() if p.status.value == "executing"]


class StaticIssues:
    """`IssueReader` returning fixed issues (whose text may carry an injection)."""

    def __init__(self, issues: Sequence[Any]) -> None:
        self.issues = list(issues)

    async def status(self) -> Any:
        raise NotImplementedError

    async def list_issues(self, *, limit: int) -> Any:
        from agentops.domain.tracker import IssuePage

        return IssuePage(items=tuple(self.issues[:limit]), truncated=False)

    async def search_issues(self, query: str, *, limit: int) -> Any:
        return await self.list_issues(limit=limit)


class SpyWriter:
    """`IssueWriter` that records every call; the eval asserts when it must stay at zero."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def create_issue(self, draft: Any, *, idempotency_key: str) -> Any:
        from datetime import UTC
        from datetime import datetime as dt

        from agentops.domain.tracker import Issue

        self.calls.append((draft, idempotency_key))
        return Issue(
            id=f"LT-{len(self.calls)}",
            title=draft.title,
            body=draft.body,
            labels=draft.labels,
            status="open",
            created_at=dt.now(UTC),
        )
