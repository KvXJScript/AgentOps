from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog
from pydantic import SecretStr

from agentops.infrastructure.llm._http import parse_retry_after
from agentops.infrastructure.logging import REDACTED, configure_logging, redact


@pytest.fixture
def json_logs() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    yield stream
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_redacts_sensitive_keys_but_keeps_usage_counters() -> None:
    event = redact(
        None,
        "info",
        {
            "event": "x",
            "api_key": "abc",
            "GROQ_API_KEY": "abc",
            "authorization": "Bearer abc",
            "access_token": "abc",
            "content": "user text",
            "input_tokens": 12,
            "output_tokens": 3,
            "nested": {"password": "p", "ok": 1},
            "key_obj": SecretStr("s"),
        },
    )

    assert event["api_key"] == event["GROQ_API_KEY"] == event["authorization"] == REDACTED
    assert event["access_token"] == event["content"] == REDACTED
    assert event["nested"] == {"password": REDACTED, "ok": 1}
    assert event["key_obj"] == REDACTED
    assert (event["input_tokens"], event["output_tokens"]) == (12, 3)


@pytest.mark.parametrize(
    "secret",
    ["gsk_abcdefghijklmnop1234", "sk-proj-abcdefghijkl", "AIzaSyA1234567890abcdefghijklmnop", "Bearer abc.def.ghi123"],
)
def test_redacts_secret_looking_values_anywhere(secret: str) -> None:
    event = redact(None, "info", {"event": f"failed with {secret}", "detail": [f"x {secret}"]})

    assert secret not in json.dumps(event)


def test_stdlib_logs_are_json_with_request_id_and_redaction(json_logs: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(request_id="req-9")

    logging.getLogger("agentops.test").info(
        "llm.call", extra={"model": "m", "api_key": "gsk_abcdefghijklmnop1234", "output_tokens": 5}
    )

    (line,) = _lines(json_logs)
    assert line["event"] == "llm.call"
    assert line["request_id"] == "req-9"
    assert line["api_key"] == REDACTED
    assert line["output_tokens"] == 5
    assert line["level"] == "info"
    assert "timestamp" in line


def test_structlog_logger_is_redacted_too(json_logs: io.StringIO) -> None:
    structlog.get_logger("x").info("boom", token="gsk_abcdefghijklmnop1234")

    (line,) = _lines(json_logs)
    assert line["token"] == REDACTED


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("5", 5.0), ("1.5", 1.5), ("-3", 0.0), ("nonsense", None), ("99999", 3600.0)],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Mon, 26 Oct 2026 12:00:30 GMT", now=now) == 30.0
