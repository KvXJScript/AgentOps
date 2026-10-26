from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentops.api.schemas import ErrorBody, ErrorEnvelope
from agentops.domain.errors import DomainError, LLMRateLimited

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "validation_error": 422,
    "not_found": 404,
    "llm_not_configured": 503,
    "llm_auth_failed": 502,
    "llm_rate_limited": 429,
    "llm_timeout": 504,
    "llm_unavailable": 502,
    "retrieval_not_configured": 503,
    "unsupported_file_type": 415,
    "file_too_large": 413,
    "empty_document": 422,
    "corrupt_document": 422,
    "document_not_ready": 409,
    "embedding_failed": 500,
    # Phase 3/4
    "invalid_model_output": 502,
    # Phase 4 — MCP tracker and approvals
    "mcp_unavailable": 502,
    "mcp_timeout": 504,
    "invalid_tool_output": 502,
    "report_not_ready": 409,
    "proposal_not_pending": 409,
    "payload_mismatch": 409,
    "proposal_expired": 410,
    "internal_error": 500,
}

INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def error_response(
    request_id: str,
    code: str,
    message: str,
    *,
    status: int | None = None,
    details: list[dict[str, Any]] | None = None,
    retry_after_s: float | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details, retry_after_s=retry_after_s)
    )
    headers = {"Retry-After": str(max(1, round(retry_after_s)))} if retry_after_s is not None else None
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True),
        status_code=status or STATUS_BY_CODE.get(code, 500),
        headers=headers,
    )


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    retry_after = exc.retry_after_s if isinstance(exc, LLMRateLimited) else None
    return error_response(request_id_of(request), exc.code, exc.message, retry_after_s=retry_after)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Drop `input`/`ctx`: they would echo user content back and into logs.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return error_response(request_id_of(request), "validation_error", "Request validation failed", details=details)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        return error_response(request_id_of(request), "not_found", "Resource not found")
    if exc.status_code >= 500:
        return error_response(request_id_of(request), "internal_error", INTERNAL_ERROR_MESSAGE)
    # Other 4xx (e.g. 405) are client mistakes; keep their status, use the closest contract code.
    return error_response(request_id_of(request), "validation_error", str(exc.detail), status=exc.status_code)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    # Unhandled exceptions are turned into `internal_error` by RequestContextMiddleware,
    # which sits inside Starlette's ServerErrorMiddleware and can still add X-Request-ID.
