"""Structured JSON logging with request-id propagation and secret redaction.

Both structlog loggers and plain `logging` loggers (used by the application
layer, with `extra={...}`) go through the same processor chain.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO

import structlog
from pydantic import SecretStr

REDACTED = "[REDACTED]"

# Field names that must never be logged verbatim. `token$` (not `tokens`) keeps
# usage counters such as `input_tokens` visible.
_SENSITIVE_KEY = re.compile(
    r"(secret|passw(or)?d|authorization|cookie|credential|api[-_]?key|access[-_]?key|private[-_]?key"
    r"|token$|^content$|^prompt$|^messages$|^history$|^system_prompt$)",
    re.IGNORECASE,
)
# Values that look like credentials, wherever they appear.
_SENSITIVE_VALUE = re.compile(
    r"(gsk_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,})"
)


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(REDACTED, value)
    if isinstance(value, Mapping):
        return {k: REDACTED if _is_sensitive_key(k) else _scrub(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and bool(_SENSITIVE_KEY.search(key))


def redact(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key.startswith("_"):
            continue
        event_dict[key] = REDACTED if _is_sensitive_key(key) else _scrub(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact,
    ]
    structlog.configure(
        processors=[structlog.stdlib.filter_by_level, *shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, structlog.processors.JSONRenderer()],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # SDK HTTP clients log request URLs/headers at DEBUG; keep them quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "google_genai", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))
