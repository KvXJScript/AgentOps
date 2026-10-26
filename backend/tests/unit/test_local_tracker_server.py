"""The `local-tracker` MCP server itself: seed, tools, idempotency, validation, sandbox."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from local_tracker_mcp.server import build_server
from local_tracker_mcp.store import INJECTION_ISSUE_ID, INJECTION_PAYLOAD, IssueStore, seed_issues

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> IssueStore:
    return IssueStore(tmp_path / "tracker")


async def _call(store: IssueStore, tool: str, args: dict[str, Any]) -> tuple[bool, Any, str]:
    async with Client(build_server(store, clock=lambda: NOW), cache=None) as client:
        result = await client.call_tool(tool, args)
    text = result.content[0].text if result.content and hasattr(result.content[0], "text") else ""
    return result.is_error, result.structured_content, text


def test_seed_is_deterministic_and_contains_the_marked_injection_fixture() -> None:
    first, second = seed_issues(), seed_issues()
    assert first == second
    assert len(first) == 8
    assert [i.id for i in first] == [f"LT-{n}" for n in range(1, 9)]
    injected = [i for i in first if INJECTION_PAYLOAD in i.body]
    assert [i.id for i in injected] == [INJECTION_ISSUE_ID]


def test_store_seeds_once_and_persists(tmp_path: Path) -> None:
    store = IssueStore(tmp_path / "d")
    store.create_issue(title="Persisted issue", body="b", labels=[], idempotency_key="k-12345678", now=NOW)
    reopened = IssueStore(tmp_path / "d")
    page, total = reopened.list_issues(50)
    assert total == 9
    assert page[0].title == "Persisted issue"


async def test_server_advertises_the_tools_including_the_decoy(store: IssueStore) -> None:
    async with Client(build_server(store), cache=None) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"list_issues", "search_issues", "create_issue", "delete_all_issues"}


async def test_list_issues_newest_first_with_limit(store: IssueStore) -> None:
    is_error, data, _ = await _call(store, "list_issues", {"limit": 3})
    assert not is_error
    assert [i["id"] for i in data["items"]] == ["LT-8", "LT-7", "LT-6"]
    assert data["total"] == 8


async def test_search_issues_matches_words_case_insensitively(store: IssueStore) -> None:
    _, data, _ = await _call(store, "search_issues", {"query": "remote work policy", "limit": 10})
    ids = [i["id"] for i in data["items"]]
    assert ids[:2] == ["LT-5", "LT-6"]  # both match in title and body; ties keep id order
    _, title_only, _ = await _call(store, "search_issues", {"query": "glossary", "limit": 10})
    assert [i["id"] for i in title_only["items"]] == ["LT-8"]
    _, none, _ = await _call(store, "search_issues", {"query": "zzzz-nothing", "limit": 10})
    assert none == {"items": [], "total": 0}


async def test_create_issue_is_idempotent_by_key(store: IssueStore) -> None:
    args = {
        "title": "New follow-up issue",
        "body": "Body",
        "labels": ["follow-up", "follow-up"],
        "idempotency_key": "p-1234567890",
    }
    _, first, _ = await _call(store, "create_issue", args)
    _, second, _ = await _call(store, "create_issue", {**args, "title": "Different title"})
    assert first["created"] is True
    assert first["issue"]["id"] == "LT-9"
    assert first["issue"]["labels"] == ["follow-up"]
    assert second["created"] is False
    assert second["issue"] == first["issue"]  # same issue, not a duplicate, original title kept
    assert store.list_issues(50)[1] == 9


@pytest.mark.parametrize(
    "bad",
    [
        {"title": "x", "body": "b", "labels": [], "idempotency_key": "k-12345678"},
        {"title": "Valid title", "body": "b", "labels": ["admin"], "idempotency_key": "k-12345678"},
        {"title": "Valid title", "body": "b" * 4001, "labels": [], "idempotency_key": "k-12345678"},
        {
            "title": "Valid title",
            "body": "b",
            "labels": ["bug", "docs", "question", "follow-up"],
            "idempotency_key": "k-12345678",
        },
        {"title": "Valid title", "body": "b", "labels": [], "idempotency_key": "short"},
    ],
)
async def test_create_issue_validates_arguments(store: IssueStore, bad: dict[str, Any]) -> None:
    is_error, _, _ = await _call(store, "create_issue", bad)
    assert is_error
    assert store.list_issues(50)[1] == 8


async def test_decoy_tool_really_deletes(store: IssueStore) -> None:
    """So an unchanged count in the client tests proves the decoy was never called."""
    _, data, _ = await _call(store, "delete_all_issues", {})
    assert data == {"deleted": 8}
    assert store.list_issues(50)[1] == 0


def _run_guarded(data_dir: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    code = (
        f"from pathlib import Path\nfrom local_tracker_mcp import sandbox\nsandbox.install(Path({str(data_dir)!r}))\n"
    ) + snippet
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False)


def test_sandbox_blocks_network_subprocess_and_outside_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    snippet = f"""
results = []
def attempt(name, fn):
    try:
        fn()
        results.append(name + ":allowed")
    except PermissionError:
        results.append(name + ":blocked")
import socket, subprocess, os
attempt("socket", lambda: socket.create_connection(("127.0.0.1", 9)))
attempt("dns", lambda: socket.getaddrinfo("example.com", 80))
attempt("subprocess", lambda: subprocess.run(["true"]))
attempt("read_outside", lambda: open({str(outside)!r}).read())
attempt("write_outside", lambda: open({str(tmp_path / "new.txt")!r}, "w"))
attempt("remove_outside", lambda: os.remove({str(outside)!r}))
attempt("write_inside", lambda: open({str(data_dir / "ok.txt")!r}, "w").write("x"))
attempt("late_import", lambda: __import__("decimal"))
print(",".join(results))
"""
    out = _run_guarded(data_dir, snippet)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().split(",") == [
        "socket:blocked",
        "dns:blocked",
        "subprocess:blocked",
        "read_outside:blocked",
        "write_outside:blocked",
        "remove_outside:blocked",
        "write_inside:allowed",
        "late_import:allowed",
    ]
    assert outside.read_text() == "secret"
