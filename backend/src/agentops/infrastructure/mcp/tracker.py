"""MCP client adapter for `local-tracker` (stdio subprocess).

Connection lifecycle — one long-lived session, lazily (re)connected:
- The lifespan calls `start()`, which tries to connect once and never raises: if the server cannot be
  launched the app still starts and reports `unavailable`.
- The session is owned by one background task (`_own`), so the SDK's anyio task group is entered and
  exited in the same task; request handlers only send requests over it (JSON-RPC multiplexes them).
- A transport failure (process died, pipe closed) drops the session; the next call reconnects. Failed
  connects are throttled (`reconnect_interval_s`) so a broken server is not re-spawned on every request.
- On connect the tool listing must contain every allowlisted tool, or the connection is refused.

Per call: exact-name allowlist + access check, argument validation before anything is sent, a per-call
timeout, an output size cap and strict Pydantic validation of the structured result. Logs carry the tool
name, outcome, duration and sizes only — never arguments or tool output.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TextIO

from mcp import Client, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from agentops.domain.errors import DomainError
from agentops.domain.tracker import (
    CREATE_ISSUE_TOOL,
    ISSUE_BODY_MAX,
    ISSUE_LABELS_MAX,
    ISSUE_TITLE_MAX,
    ISSUE_TITLE_MIN,
    LIMIT_MAX,
    QUERY_MAX_CHARS,
    TOOL_POLICIES,
    InvalidToolArguments,
    InvalidToolOutput,
    Issue,
    IssueDraft,
    IssuePage,
    McpTimeout,
    McpUnavailable,
    ToolAccess,
    TrackerStatus,
)

logger = logging.getLogger(__name__)

DATA_DIR_ENV = "LOCAL_TRACKER_DATA_DIR"
STDERR_LOG = "server.stderr.log"
# On top of the SDK's per-request timeout, in case the transport itself wedges.
_TIMEOUT_GRACE_S = 1.0


@dataclass(frozen=True, slots=True)
class McpTrackerConfig:
    command: str
    args: tuple[str, ...]
    data_dir: Path
    allowlist: frozenset[str]
    timeout_s: float = 10.0  # per tool call
    # Spawning the server and the MCP handshake; kept separate so a tight per-call
    # timeout doesn't also make a slow cold start look like an outage.
    connect_timeout_s: float = 20.0
    max_output_bytes: int = 256_000
    reconnect_interval_s: float = 5.0


def server_parameters(config: McpTrackerConfig) -> StdioServerParameters:
    """Spawn parameters with a minimal environment.

    The SDK merges `env` over its own safe default (HOME, LOGNAME, PATH, SHELL, TERM, USER); nothing
    else from the backend's environment — provider keys, DATABASE_URL — reaches the subprocess.
    """
    return StdioServerParameters(
        command=config.command,
        args=list(config.args),
        env={
            DATA_DIR_ENV: str(config.data_dir),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
        cwd=str(config.data_dir),
    )


# --- Strict result schemas (what we accept back from the server) ------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _IssueOut(_Strict):
    id: Annotated[str, StringConstraints(pattern=r"^LT-\d{1,9}$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    body: Annotated[str, StringConstraints(max_length=20_000)]
    labels: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=50)]], Field(max_length=20)]
    status: Literal["open", "closed"]
    created_at: AwareDatetime

    def to_domain(self) -> Issue:
        return Issue(
            id=self.id,
            title=self.title,
            body=self.body,
            labels=tuple(self.labels),
            status=self.status,
            created_at=self.created_at,
        )


class _IssueListOut(_Strict):
    items: Annotated[list[_IssueOut], Field(max_length=LIMIT_MAX)]
    total: Annotated[int, Field(ge=0)]


class _CreatedIssueOut(_Strict):
    issue: _IssueOut
    created: bool


# --- Argument schemas (validated before anything is sent) -------------------------------------------
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


Limit = Annotated[int, Field(ge=1, le=LIMIT_MAX)]


class _ListArgs(_Args):
    limit: Limit


class _SearchArgs(_Args):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_MAX_CHARS)]
    limit: Limit


class _CreateArgs(_Args):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=ISSUE_TITLE_MIN, max_length=ISSUE_TITLE_MAX)
    ]
    body: Annotated[str, StringConstraints(max_length=ISSUE_BODY_MAX)]
    labels: Annotated[list[Literal["bug", "docs", "follow-up", "question"]], Field(max_length=ISSUE_LABELS_MAX)]
    idempotency_key: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,128}$")]


def _validated_args(schema: type[_Args], **values: Any) -> dict[str, Any]:
    try:
        return schema.model_validate(values).model_dump()
    except ValidationError as exc:
        raise InvalidToolArguments() from exc


@dataclass(frozen=True, slots=True)
class _ToolOutput:
    data: Any
    size_bytes: int


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# --- Connection -------------------------------------------------------------------------------------
@dataclass(slots=True)
class _Session:
    client: Client
    stop: asyncio.Event
    task: asyncio.Task[None]


class McpTrackerConnection:
    """One supervised MCP session. Use `IssueReader`/`IssueWriter` facades, not this, from app code."""

    def __init__(
        self,
        config: McpTrackerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        unknown = config.allowlist - TOOL_POLICIES.keys()
        if unknown:
            raise ValueError(f"allowlist names tools AgentOps does not implement: {sorted(unknown)}")
        self._config = config
        self._clock = clock
        self._lock = asyncio.Lock()
        self._session: _Session | None = None
        self._retry_at = 0.0
        self._errlog: TextIO | None = None

    @property
    def config(self) -> McpTrackerConfig:
        return self._config

    @property
    def connected(self) -> bool:
        return self._session is not None and not self._session.task.done()

    async def start(self) -> None:
        """Best-effort connect at startup; never raises."""
        with contextlib.suppress(DomainError):
            await self._ensure()

    async def probe(self) -> bool:
        """Connected now (connecting lazily if allowed)?"""
        try:
            await self._ensure()
        except DomainError:
            return False
        return True

    async def aclose(self) -> None:
        async with self._lock:
            await self._drop()
            if self._errlog is not None:
                self._errlog.close()
                self._errlog = None

    async def call(self, tool: str, arguments: dict[str, Any], *, access: ToolAccess) -> _ToolOutput:
        policy = TOOL_POLICIES.get(tool)
        if policy is None or policy.access is not access:
            raise RuntimeError(f"tool {tool!r} cannot be called with {access.value} access")  # programming error
        if tool not in self._config.allowlist:
            raise McpUnavailable("This tracker tool is not enabled on the server.")
        session = await self._ensure()
        started = time.monotonic()
        outcome = "ok"
        size = 0
        try:
            try:
                async with asyncio.timeout(self._config.timeout_s + _TIMEOUT_GRACE_S):
                    result = await session.client.call_tool(
                        tool, arguments, read_timeout_seconds=self._config.timeout_s
                    )
            except TimeoutError as exc:
                outcome = "timeout"
                raise McpTimeout() from exc
            except MCPError as exc:
                if exc.code == REQUEST_TIMEOUT:
                    outcome = "timeout"
                    raise McpTimeout() from exc
                outcome = "connection_closed" if exc.code == CONNECTION_CLOSED else "protocol_error"
                if exc.code == CONNECTION_CLOSED:
                    self._forget(session)
                raise McpUnavailable() from exc
            except ValidationError as exc:  # the SDK rejected the result shape / output schema
                outcome = "invalid_output"
                raise InvalidToolOutput() from exc
            except Exception as exc:  # transport broke (pipe closed, process gone)
                outcome = "transport_error"
                self._forget(session)
                raise McpUnavailable() from exc
            if result.is_error:
                outcome = "tool_error"
                raise InvalidToolOutput("The local tracker reported an error for this request.")
            if result.structured_content is None:
                outcome = "invalid_output"
                raise InvalidToolOutput()
            size = _json_size(result.structured_content)
            return _ToolOutput(result.structured_content, size)
        finally:
            logger.info(
                "mcp.tool_call",
                extra={
                    "server": "local-tracker",
                    "tool": tool,
                    "outcome": outcome,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "output_bytes": size,
                },
            )

    # -- lifecycle internals --
    async def _ensure(self) -> _Session:
        async with self._lock:
            if self._session is not None and not self._session.task.done():
                return self._session
            await self._drop()
            if self._clock() < self._retry_at:
                raise McpUnavailable()
            try:
                self._session = await self._open()
            except McpUnavailable:
                self._retry_at = self._clock() + self._config.reconnect_interval_s
                raise
            self._retry_at = 0.0
            return self._session

    async def _open(self) -> _Session:
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[Client] = loop.create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._own(ready, stop), name="mcp-local-tracker")
        try:
            async with asyncio.timeout(self._config.connect_timeout_s):
                client = await asyncio.shield(ready)
        except BaseException as exc:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            reason = "timeout" if isinstance(exc, TimeoutError) else getattr(exc, "reason", type(exc).__name__)
            logger.warning(
                "mcp.connect_failed",
                extra={
                    "server": "local-tracker",
                    "reason": reason,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise McpUnavailable() from exc
        logger.info(
            "mcp.connected",
            extra={
                "server": "local-tracker",
                "tools": sorted(self._config.allowlist),
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return _Session(client=client, stop=stop, task=task)

    async def _own(self, ready: asyncio.Future[Client], stop: asyncio.Event) -> None:
        """Owns the SDK client for its whole life: enter, verify, wait for stop, exit — one task."""
        try:
            transport = stdio_client(server_parameters(self._config), errlog=self._stderr())
            async with Client(transport, cache=None, read_timeout_seconds=self._config.timeout_s) as client:
                advertised = await _tool_names(client)
                missing = self._config.allowlist - advertised
                if missing:
                    raise _ConnectRefused("missing_allowlisted_tools")
                ignored = len(advertised - self._config.allowlist)
                if ignored:
                    logger.info("mcp.tools_ignored", extra={"server": "local-tracker", "count": ignored})
                ready.set_result(client)
                await stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, _ConnectRefused) else _ConnectRefused(type(exc).__name__))
            else:
                logger.warning("mcp.session_ended", extra={"server": "local-tracker", "reason": type(exc).__name__})

    def _stderr(self) -> TextIO:
        # The server's own stderr goes to a file in its data dir, never into the app's logs.
        if self._errlog is None:
            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            self._errlog = (self._config.data_dir / STDERR_LOG).open("a", encoding="utf-8")
        return self._errlog

    def _forget(self, session: _Session) -> None:
        """Drop a broken session; the owner task exits and the next call reconnects."""
        session.stop.set()
        if self._session is session:
            self._session = None

    async def _drop(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        session.stop.set()
        try:
            async with asyncio.timeout(self._config.timeout_s):
                await asyncio.gather(session.task, return_exceptions=True)
        except TimeoutError:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)


class _ConnectRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _tool_names(client: Client) -> set[str]:
    names: set[str] = set()
    cursor: str | None = None
    for _ in range(20):
        page = await client.list_tools(cursor=cursor)
        names.update(tool.name for tool in page.tools)
        cursor = page.next_cursor
        if cursor is None:
            break
    return names


# --- Facades: the two ports -------------------------------------------------------------------------
class McpIssueReader:
    """`IssueReader` over the shared connection. Has no way to write."""

    def __init__(self, connection: McpTrackerConnection) -> None:
        self._conn = connection

    async def status(self) -> TrackerStatus:
        connected = await self._conn.probe()
        allowed = self._conn.config.allowlist
        return TrackerStatus(
            connected=connected, tools=tuple(p for name, p in TOOL_POLICIES.items() if name in allowed)
        )

    async def list_issues(self, *, limit: int) -> IssuePage:
        args = _validated_args(_ListArgs, limit=limit)
        return self._page(await self._conn.call("list_issues", args, access=ToolAccess.READ))

    async def search_issues(self, query: str, *, limit: int) -> IssuePage:
        args = _validated_args(_SearchArgs, query=query, limit=limit)
        return self._page(await self._conn.call("search_issues", args, access=ToolAccess.READ))

    def _page(self, output: _ToolOutput) -> IssuePage:
        data, truncated = _cap_items(output, self._conn.config.max_output_bytes)
        try:
            parsed = _IssueListOut.model_validate_json(json.dumps(data))
        except ValidationError as exc:
            raise InvalidToolOutput() from exc
        return IssuePage(items=tuple(i.to_domain() for i in parsed.items), truncated=truncated)


def _cap_items(output: _ToolOutput, cap: int) -> tuple[Any, bool]:
    """Within the cap → unchanged. Over it → keep the leading items that fit (`truncated`)."""
    if output.size_bytes <= cap:
        return output.data, False
    data = output.data
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise InvalidToolOutput()
    kept: list[Any] = []
    used = _json_size({**data, "items": []})
    for item in data["items"]:
        item_size = _json_size(item) + 1
        if used + item_size > cap:
            break
        kept.append(item)
        used += item_size
    return {**data, "items": kept}, True


class McpIssueWriter:
    """`IssueWriter`: the only object that can call `create_issue`. Built once, given to approval only."""

    def __init__(self, connection: McpTrackerConnection) -> None:
        self._conn = connection

    async def create_issue(self, draft: IssueDraft, *, idempotency_key: str) -> Issue:
        args = _validated_args(
            _CreateArgs,
            title=draft.title,
            body=draft.body,
            labels=list(draft.labels),
            idempotency_key=idempotency_key,
        )
        output = await self._conn.call(CREATE_ISSUE_TOOL, args, access=ToolAccess.WRITE)
        if output.size_bytes > self._conn.config.max_output_bytes:
            raise InvalidToolOutput()
        try:
            parsed = _CreatedIssueOut.model_validate_json(json.dumps(output.data))
        except ValidationError as exc:
            raise InvalidToolOutput() from exc
        return parsed.issue.to_domain()
