"""A misbehaving MCP server for client tests (run as a script; never shipped).

Advertises the same tool names as `local-tracker` but behaves according to `FAULT` (passed as argv[1]):
- `slow`: `list_issues` sleeps 3 s (timeout tests)
- `invalid_output`: `list_issues` returns a structured result that breaks the Issue schema
- `missing_tool`: does not advertise `search_issues` (allowlist verification on connect)
- `tool_error`: `list_issues` raises, so the result has `is_error: true`
- `env`: writes the names of its environment variables to `env.txt` in its working directory
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer


def build(fault: str) -> MCPServer:
    server = MCPServer("local-tracker", log_level="WARNING")

    @server.tool()
    def list_issues(limit: int = 20) -> dict[str, Any]:
        if fault == "slow":
            time.sleep(3)
        if fault == "tool_error":
            raise RuntimeError("tracker exploded")
        if fault == "invalid_output":
            return {"items": [{"id": "not-an-id", "title": 42, "status": "deleted"}], "total": "lots"}
        return {"items": [], "total": 0}

    if fault != "missing_tool":

        @server.tool()
        def search_issues(query: str, limit: int = 20) -> dict[str, Any]:
            return {"items": [], "total": 0}

    @server.tool()
    def create_issue(title: str, body: str, labels: list[str], idempotency_key: str) -> dict[str, Any]:
        return {"unexpected": True}

    return server


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "env":
        Path("env.txt").write_text("\n".join(sorted(os.environ)))
    build(sys.argv[1] if len(sys.argv) > 1 else "").run("stdio")
