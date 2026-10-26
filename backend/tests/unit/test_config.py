from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentops.config import DEFAULT_MODELS
from agentops.infrastructure.llm.factory import build_chat_model
from agentops.infrastructure.llm.resilience import ResilientChatModel
from tests.conftest import make_settings

SECRET = "gsk_supersecretvalue1234567890"


def test_defaults_select_groq_with_its_default_model() -> None:
    settings = make_settings()

    assert settings.llm.provider == "groq"
    assert settings.llm.model == DEFAULT_MODELS["groq"]
    assert settings.llm.is_configured is False


def test_blank_model_means_provider_default() -> None:
    settings = make_settings(llm_provider="gemini", llm_model="  ", gemini_api_key="k")

    assert settings.llm.model == DEFAULT_MODELS["gemini"]
    assert settings.llm.is_configured


def test_each_provider_reads_only_its_own_key() -> None:
    settings = make_settings(llm_provider="groq", gemini_api_key="only-gemini")

    assert settings.llm.api_key is None
    assert not settings.llm.is_configured


def test_blank_key_counts_as_missing() -> None:
    assert make_settings(groq_api_key="   ").llm.is_configured is False


def test_env_vars_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "GROQ")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = make_settings()

    assert settings.llm.model == "openai/gpt-oss-120b"
    assert settings.llm.api_key is not None
    assert settings.llm.api_key.get_secret_value() == SECRET
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_secrets_are_masked_in_repr() -> None:
    settings = make_settings(groq_api_key=SECRET)

    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.llm)


def test_unknown_provider_fails_fast() -> None:
    with pytest.raises(ValidationError):
        make_settings(llm_provider="openai")


def test_fake_provider_only_accepts_fake_model() -> None:
    with pytest.raises(ValidationError, match="fake-echo"):
        make_settings(llm_provider="fake", llm_model="gpt-4")
    assert make_settings(llm_provider="fake").llm.model == "fake-echo"


def test_invalid_model_id_fails_without_leaking_key() -> None:
    with pytest.raises(ValidationError) as info:
        make_settings(groq_api_key=SECRET, llm_model="bad model; rm -rf")
    assert SECRET not in str(info.value)


@pytest.mark.parametrize(
    ("field", "value"), [("llm_timeout_s", 0), ("llm_max_retries", 9), ("chat_history_messages", 0)]
)
def test_numeric_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_factory_missing_key_gives_no_model_and_no_fallback() -> None:
    assert build_chat_model(make_settings(llm_provider="groq", gemini_api_key="x")) is None
    assert build_chat_model(make_settings(llm_provider="gemini", groq_api_key="x")) is None


@pytest.mark.parametrize(("provider", "key_field"), [("groq", "groq_api_key"), ("gemini", "gemini_api_key")])
def test_factory_builds_wrapped_real_provider(provider: str, key_field: str) -> None:
    model = build_chat_model(make_settings(llm_provider=provider, **{key_field: "k"}))

    assert isinstance(model, ResilientChatModel)
    assert (model.provider, model.model) == (provider, DEFAULT_MODELS[provider])


def test_factory_fake_is_explicit_and_labelled() -> None:
    model = build_chat_model(make_settings(llm_provider="fake"))

    assert model is not None
    assert (model.provider, model.model) == ("fake", "fake-echo")


def test_database_url_must_be_postgres() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        make_settings(database_url="sqlite:///app.db")
    assert make_settings(database_url="postgresql://u:p@h:1/d").database_url == "postgresql+asyncpg://u:p@h:1/d"
