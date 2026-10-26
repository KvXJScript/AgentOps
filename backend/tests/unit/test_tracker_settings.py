from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentops.config import DEFAULT_TRACKER_DATA_DIR
from agentops.tracker_wiring import tracker_config
from tests.conftest import make_settings


def test_defaults() -> None:
    settings = make_settings()
    assert settings.mcp_tracker_allowed_tools == ["list_issues", "search_issues", "create_issue"]
    assert settings.mcp_timeout_s == 10.0
    assert settings.proposal_ttl_s == 900
    assert Path(DEFAULT_TRACKER_DATA_DIR).parts[-2:] == (".data", "local-tracker")
    assert Path(DEFAULT_TRACKER_DATA_DIR).parent.parent.name == "backend"
    config = tracker_config(settings)
    assert config.command == sys.executable
    assert config.args == ("-m", "local_tracker_mcp")


def test_allowlist_from_env_can_only_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRACKER_ALLOWED_TOOLS", " list_issues , search_issues,list_issues ")
    assert make_settings().mcp_tracker_allowed_tools == ["list_issues", "search_issues"]
    monkeypatch.setenv("MCP_TRACKER_ALLOWED_TOOLS", "list_issues,delete_all_issues")
    with pytest.raises(ValidationError, match="MCP_TRACKER_ALLOWED_TOOLS"):
        make_settings()


def test_timeout_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TIMEOUT_S", "0")
    with pytest.raises(ValidationError):
        make_settings()
