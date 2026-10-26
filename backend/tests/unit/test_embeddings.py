from __future__ import annotations

import math
import os

import pytest

from agentops.domain.documents import EmbeddingFailed
from agentops.infrastructure.embeddings import fastembed as fastembed_module
from agentops.infrastructure.embeddings.factory import build_embedder
from agentops.infrastructure.embeddings.fake import FakeEmbedder
from agentops.infrastructure.embeddings.fastembed import FastEmbedEmbedder, supported_dimensions
from tests.conftest import make_settings


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_fake_embedder_is_deterministic_normalised_and_ranks_by_overlap() -> None:
    embedder = FakeEmbedder()
    query = await embedder.embed_query("vacation days per year")
    [close, far, empty] = await embedder.embed_documents(["Employees get 26 vacation days per year", "Parking", "  "])

    assert query == await FakeEmbedder().embed_query("Vacation DAYS per year")
    assert len(query) == embedder.dimensions == 384
    assert math.isclose(sum(v * v for v in close), 1.0)
    assert math.isclose(sum(v * v for v in empty), 1.0)
    assert _cos(query, close) > 0.5 > _cos(query, far)


def test_default_model_is_known_with_384_dimensions() -> None:
    assert supported_dimensions("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") == 384
    assert supported_dimensions("nope/unknown") is None
    with pytest.raises(ValueError, match="not supported"):
        FastEmbedEmbedder("nope/unknown")


def test_factory_refuses_a_model_with_other_dimensions() -> None:
    settings = make_settings(embedding_model="BAAI/bge-base-en-v1.5")  # 768 dims

    with pytest.raises(ValueError, match="768-dim"):
        build_embedder(settings, required_dimensions=384)
    assert build_embedder(make_settings(embedding_provider="fake"), required_dimensions=384).model == "fake-hash-bow"


async def test_model_load_failure_is_embedding_failed_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def broken(**_: object) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("network unreachable")

    monkeypatch.setattr(fastembed_module, "TextEmbedding", broken)
    monkeypatch.setattr(fastembed_module, "supported_dimensions", lambda _: 384)
    embedder = FastEmbedEmbedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    for _ in range(2):
        with pytest.raises(EmbeddingFailed, match="could not be loaded"):
            await embedder.embed_query("hello")
    assert attempts == 2  # a failed load is not cached


@pytest.mark.skipif(os.environ.get("RUN_REAL_EMBEDDINGS") != "1", reason="downloads ~220 MB; set RUN_REAL_EMBEDDINGS=1")
async def test_real_fastembed_model() -> None:
    embedder = FastEmbedEmbedder(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        cache_dir=os.environ.get("REAL_EMBEDDINGS_CACHE_DIR"),
    )
    query = await embedder.embed_query("Ile dni urlopu mam w roku?")
    relevant, unrelated = await embedder.embed_documents(
        ["Employees get 26 days of paid vacation per year.", "The cafeteria serves vegetarian lunch."]
    )

    def cos(a: list[float], b: list[float]) -> float:
        return _cos(a, b) / math.sqrt(_cos(a, a) * _cos(b, b))

    assert len(query) == 384
    assert cos(query, relevant) > 0.35 > cos(query, unrelated)
