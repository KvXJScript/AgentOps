from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Provider = Literal["groq", "gemini", "fake"]

DEFAULT_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-3.8-flash",
    "fake": "fake-echo",
}

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# backend/.data/local-tracker (gitignored); config.py lives in backend/src/agentops/.
DEFAULT_TRACKER_DATA_DIR = str(Path(__file__).resolve().parents[2] / ".data" / "local-tracker")
TRACKER_TOOLS = ("list_issues", "search_issues", "create_issue")

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
    llm_max_output_tokens: int = Field(default=2048, ge=1, le=32_768)

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    chat_history_messages: int = Field(default=20, ge=1, le=200)

    # --- Documents and RAG (Phase 2) ---
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_pdf_pages: int = Field(default=500, ge=1, le=5_000)
    max_document_chars: int = Field(default=1_000_000, ge=1_000, le=10_000_000)
    # The default embedding model truncates input at 128 tokens (~500 chars), so bigger chunks
    # would be embedded only by their beginning.
    chunk_max_chars: int = Field(default=500, ge=100, le=8_000)
    chunk_overlap_chars: int = Field(default=80, ge=0, le=2_000)
    embedding_provider: Literal["fastembed", "fake"] = "fastembed"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_cache_dir: str | None = None
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    rag_top_k: int = Field(default=6, ge=1, le=20)
    retrieval_min_score: float = Field(default=0.35, ge=-1.0, le=1.0)
    rag_context_max_chars: int = Field(default=6_000, ge=500, le=100_000)

    report_timeout_s: float = Field(default=120.0, gt=0, le=900)
    report_top_k: int = Field(default=8, ge=1, le=20)
    report_max_evidence_chars: int = Field(default=12_000, ge=1_000, le=100_000)
    report_max_concurrent_runs: int = Field(default=2, ge=1, le=10)

    # --- MCP local tracker + approval boundary (Phase 4) ---
    mcp_tracker_enabled: bool = True
    mcp_tracker_data_dir: str = DEFAULT_TRACKER_DATA_DIR
    # Exact tool names; may only narrow TRACKER_TOOLS. Comma-separated in the environment.
    mcp_tracker_allowed_tools: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(TRACKER_TOOLS))
    mcp_timeout_s: float = Field(default=10.0, gt=0, le=120)
    mcp_max_output_bytes: int = Field(default=256_000, ge=1_024, le=10_000_000)
    proposal_ttl_s: int = Field(default=900, ge=60, le=86_400)

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

    @field_validator("embedding_provider", mode="before")
    @classmethod
    def _lower_embedding_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("embedding_model", mode="before")
    @classmethod
    def _blank_embedding_model_is_default(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or DEFAULT_EMBEDDING_MODEL
        return value

    @field_validator("embedding_cache_dir", mode="before")
    @classmethod
    def _blank_cache_dir_is_default(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _check_chunking(self) -> Settings:
        if self.chunk_overlap_chars >= self.chunk_max_chars // 2:
            raise ValueError("CHUNK_OVERLAP_CHARS must be less than half of CHUNK_MAX_CHARS")
        return self

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg_url(cls, value: str) -> str:
        scheme, sep, rest = value.partition("://")
        if not sep or scheme not in {"postgresql", "postgres", "postgresql+asyncpg"}:
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql+asyncpg://user:pass@host:port/db)")
        return f"postgresql+asyncpg://{rest}"

    @field_validator("mcp_tracker_allowed_tools", mode="before")
    @classmethod
    def _split_tools(cls, value: object) -> object:
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value

    @field_validator("mcp_tracker_allowed_tools")
    @classmethod
    def _known_tools_only(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(TRACKER_TOOLS))
        if unknown:
            raise ValueError(f"MCP_TRACKER_ALLOWED_TOOLS may only list {', '.join(TRACKER_TOOLS)}; got {unknown}")
        return list(dict.fromkeys(value))

    @field_validator("mcp_tracker_data_dir", mode="before")
    @classmethod
    def _blank_data_dir_is_default(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or DEFAULT_TRACKER_DATA_DIR
        return value

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
