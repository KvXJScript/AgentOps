from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentops.config import DEFAULT_EMBEDDING_MODEL
from agentops.domain.models import ChatTurn, Role, TextDelta
from agentops.infrastructure.llm.fake import FakeEchoChatModel
from tests.conftest import make_settings


def test_phase2_defaults() -> None:
    settings = make_settings()

    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert (settings.chunk_max_chars, settings.chunk_overlap_chars) == (500, 80)
    assert (settings.embedding_provider, settings.embedding_model) == ("fastembed", DEFAULT_EMBEDDING_MODEL)
    assert (settings.rag_top_k, settings.retrieval_min_score, settings.rag_context_max_chars) == (6, 0.35, 6000)
    assert settings.embedding_cache_dir is None
    assert (settings.retrieval_identifier_boost, settings.retrieval_deprecated_penalty) == (0.1, 0.1)


def test_env_overrides_and_blank_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", " FAKE ")
    monkeypatch.setenv("EMBEDDING_MODEL", "  ")
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", "")
    monkeypatch.setenv("RAG_TOP_K", "3")

    settings = make_settings()

    assert settings.embedding_provider == "fake"
    assert settings.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert settings.embedding_cache_dir is None
    assert settings.rag_top_k == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"chunk_max_chars": 200, "chunk_overlap_chars": 100},
        {"rag_top_k": 21},
        {"retrieval_min_score": 1.5},
        {"retrieval_identifier_boost": -0.1},
        {"retrieval_deprecated_penalty": 1.5},
        {"embedding_provider": "openai"},
    ],
)
def test_invalid_values_fail_fast(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


async def test_fake_echo_model_cites_the_first_evidence_label() -> None:
    model = FakeEchoChatModel(delay_s=0)
    history = [ChatTurn(role=Role.USER, content="vacation?")]

    plain = "".join([c.text async for c in model.stream(history, system_prompt="sys") if isinstance(c, TextDelta)])
    grounded = "".join(
        [
            c.text
            async for c in model.stream(history, system_prompt='rules <evidence label="S1" source="x">t</evidence>')
            if isinstance(c, TextDelta)
        ]
    )

    assert "[S" not in plain
    assert grounded.endswith("(see [S1])")
