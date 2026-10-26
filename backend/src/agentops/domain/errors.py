from __future__ import annotations


class DomainError(Exception):
    """Base for errors the API layer knows how to translate."""

    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or type(self).message
        super().__init__(self.message)


class ConversationNotFound(DomainError):
    code = "not_found"
    message = "Conversation not found"


class LLMError(DomainError):
    """Provider failure. `message` is always safe to show to end users."""

    retryable: bool = False

    def __init__(self, message: str | None = None, *, provider_status: int | None = None) -> None:
        super().__init__(message)
        # Kept for logs only; never sent to clients.
        self.provider_status = provider_status


class LLMNotConfigured(LLMError):
    code = "llm_not_configured"
    message = "No language model is configured on the server."


class LLMAuthFailed(LLMError):
    code = "llm_auth_failed"
    message = "The model provider rejected the server's credentials."


class LLMRateLimited(LLMError):
    code = "llm_rate_limited"
    message = "The model provider is rate limiting requests. Please try again shortly."
    retryable = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_s: float | None = None,
        provider_status: int | None = None,
    ) -> None:
        super().__init__(message, provider_status=provider_status)
        self.retry_after_s = retry_after_s


class LLMTimeout(LLMError):
    code = "llm_timeout"
    message = "The model provider took too long to respond."
    retryable = True


class LLMUnavailable(LLMError):
    code = "llm_unavailable"
    message = "The model provider is currently unavailable."
    retryable = True


class LLMRequestRejected(LLMError):
    """Provider refused the request itself (unknown model, bad parameters).

    A server-side configuration problem, so it surfaces as `internal_error`
    and is never retried.
    """

    code = "internal_error"
    message = "The request to the model provider was rejected."
