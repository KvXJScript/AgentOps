"""Structural guarantee: only the approval use case can reach `create_issue`.

Scans the source (AST, not text) so a new caller anywhere in the app fails this test.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agentops.application.proposals.approval import ApprovalService
from agentops.application.proposals.drafting import ProposalService
from agentops.application.tracker import TrackerService
from agentops.infrastructure.mcp.tracker import McpIssueReader

SRC = Path(__file__).resolve().parents[2] / "src" / "agentops"

# Where the write capability may be named at all.
WRITER_ALLOWED = {
    "domain/tracker.py",  # the port definition
    "infrastructure/mcp/tracker.py",  # the adapter
    "tracker_wiring.py",  # constructs it and hands it to ApprovalService only
    "application/proposals/approval.py",  # the one consumer
}
# Where the tool name may appear as a string or attribute.
TOOL_NAME_ALLOWED = {
    "domain/tracker.py",
    "infrastructure/mcp/tracker.py",
    "config.py",  # the default allowlist
    "api/routes/proposals.py",  # Literal["create_issue"] in the response schema
}


def _modules() -> dict[str, ast.Module]:
    return {str(p.relative_to(SRC)): ast.parse(p.read_text()) for p in SRC.rglob("*.py")}


def _names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
    return found


def test_only_the_approval_service_and_its_wiring_touch_the_writer() -> None:
    offenders = [
        path
        for path, tree in _modules().items()
        if {"IssueWriter", "McpIssueWriter"} & _names(tree) and path not in WRITER_ALLOWED
    ]
    assert offenders == []


def test_create_issue_is_only_called_by_the_approval_service() -> None:
    callers = {
        path
        for path, tree in _modules().items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_issue"
    }
    assert callers == {"application/proposals/approval.py"}
    mentions = {
        path
        for path, tree in _modules().items()
        if any(isinstance(n, ast.Constant) and n.value == "create_issue" for n in ast.walk(tree))
    }
    assert mentions <= TOOL_NAME_ALLOWED


def test_wiring_gives_the_writer_only_to_the_approval_service() -> None:
    tree = ast.parse((SRC / "tracker_wiring.py").read_text())
    uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and any(
            isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "McpIssueWriter"
            for a in node.args
        )
    ]
    assert len(uses) == 1
    assert isinstance(uses[0].func, ast.Name)
    assert uses[0].func.id == "ApprovalService"


def test_read_side_services_cannot_receive_a_writer() -> None:
    for service in (ProposalService, TrackerService):
        params = inspect.signature(service.__init__).parameters
        assert not any("writer" in name for name in params)
        assert all("IssueWriter" not in str(p.annotation) for p in params.values())
    assert "writer" in inspect.signature(ApprovalService.__init__).parameters
    assert not hasattr(McpIssueReader, "create_issue")
