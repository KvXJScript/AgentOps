from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request

from agentops.application.chat_service import ChatService
from agentops.domain.ports import ChatModel


async def _no_cleanup() -> None:
    return None


@dataclass(slots=True)
class AppDependencies:
    """Everything the HTTP layer needs, built once per process in the lifespan."""

    chat_service: ChatService
    chat_model: ChatModel | None
    check_database: Callable[[], Awaitable[bool]]
    aclose: Callable[[], Awaitable[None]] = field(default=_no_cleanup)


def get_dependencies(request: Request) -> AppDependencies:
    deps: AppDependencies = request.app.state.dependencies
    return deps


def get_chat_service(deps: Annotated[AppDependencies, Depends(get_dependencies)]) -> ChatService:
    return deps.chat_service


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
DependenciesDep = Annotated[AppDependencies, Depends(get_dependencies)]
