from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentops.api.dependencies import DependenciesDep
from agentops.api.schemas import HealthOut, LLMInfo, ReadyChecks, ReadyOut

router = APIRouter(tags=["health"])

DB_CHECK_TIMEOUT_S = 2.0


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut()


@router.get("/ready", response_model=ReadyOut, responses={503: {"model": ReadyOut}})
async def ready(deps: DependenciesDep) -> JSONResponse:
    try:
        database_ok = await asyncio.wait_for(deps.check_database(), DB_CHECK_TIMEOUT_S)
    except TimeoutError:
        database_ok = False
    model = deps.chat_model
    body = ReadyOut(
        status="ready" if database_ok and model is not None else "not_ready",
        checks=ReadyChecks(
            database="ok" if database_ok else "error",
            llm="configured" if model is not None else "not_configured",
        ),
        llm=LLMInfo(provider=model.provider, model=model.model) if model is not None else None,
    )
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True), status_code=200 if body.status == "ready" else 503
    )
