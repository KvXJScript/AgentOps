"""The MCP client adapter against the REAL `local-tracker` subprocess (and a fault server for failures)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from mcp.client.stdio import get_default_environment

from agentops.domain.tracker import (
    InvalidToolArguments,
    InvalidToolOutput,
    IssueDraft,
    McpTimeout,
    McpUnavailable,
    ToolAccess,
)
from agentops.infrastructure.logging import configure_logging
from agentops.infrastructure.mcp.tracker import (
    McpIssueReader,
    McpIssueWriter,
    McpTrackerConnection,
    server_parameters,
)
from local_tracker_mcp.store import INJECTION_ISSUE_ID, INJECTION_PAYLOAD, IssueStore
from tests.tracker_fakes import tracker_config


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[McpTrackerConnection]:
    connection = McpTrackerConnection(tracker_config(tmp_path / "tracker"))
    await connection.start()
    yield connection
    await connection.aclose()


async def _count(conn: McpTrackerConnection) -> int:
    return len((await McpIssueReader(conn).list_issues(limit=50)).items)


async def test_connects_and_reports_only_allowlisted_tools(conn: McpTrackerConnection) -> None:
    status = await McpIssueReader(conn).status()
    assert status.connected
    assert [t.name for t in status.tools] == ["list_issues", "search_issues", "create_issue"]
    assert {t.name: (t.access.value, t.requires_approval) for t in status.tools}["create_issue"] == ("write", True)


async def test_reads_typed_issues_and_returns_the_injection_as_plain_data(conn: McpTrackerConnection) -> None:
    reader = McpIssueReader(conn)
    page = await reader.list_issues(limit=3)
    assert [i.id for i in page.items] == ["LT-8", "LT-7", "LT-6"]
    assert not page.truncated
    assert all(i.created_at.tzinfo is not None for i in page.items)
    found = await reader.search_issues("equipment budget", limit=5)
    injected = next(i for i in found.items if i.id == INJECTION_ISSUE_ID)
    assert INJECTION_PAYLOAD in injected.body


async def test_disallowed_tool_is_never_exposed_or_callable(conn: McpTrackerConnection) -> None:
    reader = McpIssueReader(conn)
    assert not hasattr(reader, "create_issue")
    assert not any(hasattr(obj, "delete_all_issues") for obj in (reader, McpIssueWriter(conn), conn))
    for access in ToolAccess:
        with pytest.raises(RuntimeError):
            await conn.call("delete_all_issues", {}, access=access)
    # The write tool cannot be reached with read access (what the reader facade uses).
    with pytest.raises(RuntimeError):
        await conn.call("create_issue", {}, access=ToolAccess.READ)
    assert await _count(conn) == 8  # the decoy really deletes, so it was not called


async def test_narrowed_allowlist_blocks_tools_without_calling_them(tmp_path: Path) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", allowlist=frozenset({"list_issues"})))
    try:
        reader = McpIssueReader(connection)
        assert [t.name for t in (await reader.status()).tools] == ["list_issues"]
        with pytest.raises(McpUnavailable):
            await reader.search_issues("remote", limit=5)
        with pytest.raises(McpUnavailable):
            await McpIssueWriter(connection).create_issue(
                IssueDraft("Valid title", "b", ()), idempotency_key="key-12345678"
            )
        assert len((await reader.list_issues(limit=50)).items) == 8
    finally:
        await connection.aclose()


@pytest.mark.parametrize(
    ("draft", "key"),
    [
        (IssueDraft("Valid title", "body", ("admin",)), "key-12345678"),
        (IssueDraft("x", "body", ()), "key-12345678"),
        (IssueDraft("Valid title", "b" * 4001, ()), "key-12345678"),
        (IssueDraft("Valid title", "body", ("bug", "docs", "question", "follow-up")), "key-12345678"),
        (IssueDraft("Valid title", "body", ()), "bad key with spaces"),
    ],
)
async def test_malformed_write_is_rejected_before_any_call(
    conn: McpTrackerConnection, monkeypatch: pytest.MonkeyPatch, draft: IssueDraft, key: str
) -> None:
    calls: list[str] = []
    original = conn.call

    async def spy(tool: str, arguments: dict[str, Any], *, access: ToolAccess) -> Any:
        calls.append(tool)
        return await original(tool, arguments, access=access)

    monkeypatch.setattr(conn, "call", spy)
    with pytest.raises(InvalidToolArguments):
        await McpIssueWriter(conn).create_issue(draft, idempotency_key=key)
    assert calls == []
    assert await _count(conn) == 8


@pytest.mark.parametrize(("query", "limit"), [("", 5), ("x" * 201, 5), ("remote", 0), ("remote", 51)])
async def test_malformed_read_is_rejected_before_any_call(conn: McpTrackerConnection, query: str, limit: int) -> None:
    with pytest.raises(InvalidToolArguments):
        await McpIssueReader(conn).search_issues(query, limit=limit)


async def test_create_issue_through_the_writer_is_idempotent(conn: McpTrackerConnection) -> None:
    writer = McpIssueWriter(conn)
    draft = IssueDraft("Follow up on report", "Next steps.", ("follow-up",))
    first = await writer.create_issue(draft, idempotency_key="proposal-0001")
    again = await writer.create_issue(draft, idempotency_key="proposal-0001")
    assert first == again
    assert first.id == "LT-9"
    assert await _count(conn) == 9


async def test_timeout_maps_to_mcp_timeout_and_session_survives(tmp_path: Path) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", fault="slow", timeout_s=0.5))
    try:
        reader = McpIssueReader(connection)
        with pytest.raises(McpTimeout):
            await reader.list_issues(limit=5)
        page = await reader.search_issues("anything", limit=5)  # same session still works
        assert page.items == ()
    finally:
        await connection.aclose()


async def test_oversized_output_is_truncated_to_whole_items(tmp_path: Path) -> None:
    data_dir = tmp_path / "big"
    store = IssueStore(data_dir)
    for n in range(40):
        store.create_issue(
            title=f"Big issue number {n}",
            body="x" * 3000,
            labels=["docs"],
            idempotency_key=f"seed-{n:08d}",
            now=datetime(2026, 10, 26, tzinfo=UTC),
        )
    connection = McpTrackerConnection(tracker_config(data_dir, max_output_bytes=20_000))
    try:
        page = await McpIssueReader(connection).list_issues(limit=50)
        assert page.truncated
        assert 0 < len(page.items) < 10
        assert [i.id for i in page.items] == [f"LT-{48 - k}" for k in range(len(page.items))]  # a prefix, newest first
        small = await McpIssueReader(connection).list_issues(limit=1)
        assert not small.truncated
    finally:
        await connection.aclose()


@pytest.mark.parametrize("fault", ["invalid_output", "tool_error"])
async def test_invalid_or_failed_tool_output_is_invalid_tool_output(tmp_path: Path, fault: str) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", fault=fault))
    try:
        with pytest.raises(InvalidToolOutput):
            await McpIssueReader(connection).list_issues(limit=5)
    finally:
        await connection.aclose()


async def test_unexpected_write_result_is_invalid_tool_output(tmp_path: Path) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", fault="invalid_output"))
    try:
        with pytest.raises(InvalidToolOutput):
            await McpIssueWriter(connection).create_issue(
                IssueDraft("Valid title", "b", ()), idempotency_key="key-12345678"
            )
    finally:
        await connection.aclose()


async def test_server_missing_an_allowlisted_tool_is_refused(tmp_path: Path) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", fault="missing_tool"))
    try:
        await connection.start()
        assert not connection.connected
        assert not (await McpIssueReader(connection).status()).connected
        with pytest.raises(McpUnavailable):
            await McpIssueReader(connection).list_issues(limit=5)
    finally:
        await connection.aclose()


async def test_unlaunchable_server_reports_unavailable_without_raising(tmp_path: Path) -> None:
    connection = McpTrackerConnection(tracker_config(tmp_path / "t", command=str(tmp_path / "no-such-python")))
    try:
        await connection.start()  # must not raise
        assert not connection.connected
        with pytest.raises(McpUnavailable):
            await McpIssueReader(connection).list_issues(limit=5)
    finally:
        await connection.aclose()


async def test_failed_connects_are_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.client.stdio import stdio_client as real_stdio_client

    spawns: list[str] = []

    def counting(params: Any, errlog: Any) -> Any:
        spawns.append(params.command)
        return real_stdio_client(params, errlog=errlog)

    monkeypatch.setattr("agentops.infrastructure.mcp.tracker.stdio_client", counting)
    now = [100.0]
    connection = McpTrackerConnection(
        tracker_config(tmp_path / "t", fault="missing_tool", reconnect_interval_s=30.0), clock=lambda: now[0]
    )
    try:
        assert not await connection.probe()
        assert not await connection.probe()  # within the interval: no new spawn
        assert len(spawns) == 1
        now[0] += 31
        assert not await connection.probe()
        assert len(spawns) == 2
    finally:
        await connection.aclose()


def _kill_server_in(data_dir: Path) -> int:
    """SIGKILL the tracker subprocess whose working directory is `data_dir`."""
    pids = subprocess.run(["pgrep", "-f", "local_tracker_mcp"], capture_output=True, text=True, check=False).stdout
    killed = 0
    for pid in pids.split():
        cwd = subprocess.run(
            ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True, check=False
        ).stdout
        if str(data_dir) in cwd:
            os.kill(int(pid), signal.SIGKILL)
            killed += 1
    return killed


async def test_reconnects_after_the_server_process_dies(conn: McpTrackerConnection) -> None:
    reader = McpIssueReader(conn)
    assert len((await reader.list_issues(limit=2)).items) == 2
    assert await asyncio.to_thread(_kill_server_in, conn.config.data_dir) == 1
    with pytest.raises(McpUnavailable):  # the in-flight session is gone
        for _ in range(50):
            await reader.list_issues(limit=2)
    assert len((await reader.list_issues(limit=2)).items) == 2  # next call reconnects


async def test_subprocess_gets_a_minimal_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secretsecretsecret")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSecret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    config = tracker_config(tmp_path / "t", fault="env")
    params = server_parameters(config)
    effective = get_default_environment() | (params.env or {})
    assert set(params.env or {}) == {
        "LOCAL_TRACKER_DATA_DIR",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
    }
    assert not {"GROQ_API_KEY", "GEMINI_API_KEY", "DATABASE_URL"} & set(effective)

    (tmp_path / "t").mkdir(parents=True)
    connection = McpTrackerConnection(config)
    try:
        await connection.start()
        seen = set((tmp_path / "t" / "env.txt").read_text().split())
    finally:
        await connection.aclose()
    assert not {"GROQ_API_KEY", "GEMINI_API_KEY", "DATABASE_URL"} & seen
    assert "LOCAL_TRACKER_DATA_DIR" in seen


async def test_logs_have_tool_metadata_but_no_arguments_or_output(tmp_path: Path) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    connection = McpTrackerConnection(tracker_config(tmp_path / "t"))
    try:
        await McpIssueReader(connection).search_issues("equipment budget", limit=5)
        await McpIssueWriter(connection).create_issue(
            IssueDraft("Secret title here", "Secret body text", ("bug",)), idempotency_key="key-12345678"
        )
    finally:
        await connection.aclose()
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
    output = stream.getvalue()
    calls = [json.loads(line) for line in output.splitlines() if '"mcp.tool_call"' in line]
    assert [(c["tool"], c["outcome"]) for c in calls] == [("search_issues", "ok"), ("create_issue", "ok")]
    assert all(c["output_bytes"] > 0 and "duration_ms" in c for c in calls)
    for secret in ("equipment budget", "Secret title", "Secret body", "SYSTEM: ignore", "monitors"):
        assert secret not in output
