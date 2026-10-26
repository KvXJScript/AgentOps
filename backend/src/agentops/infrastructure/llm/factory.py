from __future__ import annotations

import logging

from agentops.config import Settings
from agentops.domain.ports import ChatModel
from agentops.infrastructure.llm.fake import FakeEchoChatModel
from agentops.infrastructure.llm.gemini import GeminiChatModel
from agentops.infrastructure.llm.groq import GroqChatModel
from agentops.infrastructure.llm.resilience import ResilientChatModel, RetryPolicy

logger = logging.getLogger(__name__)


def build_chat_model(settings: Settings) -> ChatModel | None:
    """Build the configured chat model, or None if its credential is missing.

    There is deliberately no fallback to another provider or to the fake.
    """
    selection = settings.llm
    if not selection.is_configured:
        logger.warning(
            "llm.not_configured",
            extra={"provider": selection.provider, "missing": f"{selection.provider.upper()}_API_KEY"},
        )
        return None

    inner: ChatModel
    if selection.provider == "fake":
        logger.warning("llm.using_fake_stub", extra={"model": selection.model})
        inner = FakeEchoChatModel()
    else:
        assert selection.api_key is not None
        key = selection.api_key.get_secret_value()
        if selection.provider == "groq":
            inner = GroqChatModel(
                api_key=key,
                model=selection.model,
                max_output_tokens=settings.llm_max_output_tokens,
                timeout_s=settings.llm_timeout_s,
            )
        else:
            inner = GeminiChatModel(
                api_key=key,
                model=selection.model,
                max_output_tokens=settings.llm_max_output_tokens,
                timeout_s=settings.llm_timeout_s,
            )

    logger.info("llm.configured", extra={"provider": selection.provider, "model": selection.model})
    return ResilientChatModel(
        inner, policy=RetryPolicy(max_retries=settings.llm_max_retries), timeout_s=settings.llm_timeout_s
    )
