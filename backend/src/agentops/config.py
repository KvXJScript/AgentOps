from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Provider = Literal["groq", "gemini", "fake"]

DEFAULT_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-3.8-flash",
    "fake": "fake-echo",
}

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True, slots=True)
class LLMSelection:
    """The resolved provider choice. `api_key` is None when the provider's key is missing."""

    provider: Provider
    model: str
    api_key: SecretStr | None

    @property
    def requires_key(self) -> bool:
        return self.provider != "fake"

    @property
    def is_configured(self) -> bool:
        return not self.requires_key or self.api_key is not None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops"

    llm_provider: Provider = "groq"
    llm_model: str | None = None
    llm_timeout_s: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=32_768)

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    chat_history_messages: int = Field(default=20, ge=1, le=200)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("llm_provider", "log_level", mode="before")
    @classmethod
    def _normalise_case(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value.upper() if value.lower() in {"debug", "info", "warning", "error"} else value.lower()
        return value

    @field_validator("llm_model", mode="before")
    @classmethod
    def _blank_model_is_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("gemini_api_key", "groq_api_key", mode="before")
    @classmethod
    def _blank_key_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg_url(cls, value: str) -> str:
        scheme, sep, rest = value.partition("://")
        if not sep or scheme not in {"postgresql", "postgres", "postgresql+asyncpg"}:
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql+asyncpg://user:pass@host:port/db)")
        return f"postgresql+asyncpg://{rest}"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _check_model(self) -> Settings:
        if self.llm_model is not None:
            if not _MODEL_ID.match(self.llm_model):
                raise ValueError("LLM_MODEL contains unsupported characters")
            if self.llm_provider == "fake" and self.llm_model != DEFAULT_MODELS["fake"]:
                raise ValueError("LLM_PROVIDER=fake only supports LLM_MODEL=fake-echo (or leave it blank)")
        return self

    @property
    def llm(self) -> LLMSelection:
        keys = {"groq": self.groq_api_key, "gemini": self.gemini_api_key, "fake": None}
        return LLMSelection(
            provider=self.llm_provider,
            model=self.llm_model or DEFAULT_MODELS[self.llm_provider],
            api_key=keys[self.llm_provider],
        )
