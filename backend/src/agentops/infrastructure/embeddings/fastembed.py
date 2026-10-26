"""Local CPU embeddings with fastembed (ONNX). No API key; the model is downloaded on first use.

The model loads lazily on the first embedding call (the default multilingual MiniLM is
~220 MB). Inference runs in a worker thread so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence

from fastembed import TextEmbedding

from agentops.domain.documents import EmbeddingFailed

logger = logging.getLogger(__name__)


def supported_dimensions(model_name: str) -> int | None:
    for description in TextEmbedding.list_supported_models():
        if description["model"].lower() == model_name.lower():
            return int(description["dim"])
    return None


class FastEmbedEmbedder:
    def __init__(self, model_name: str, *, cache_dir: str | None = None, batch_size: int = 32) -> None:
        dimensions = supported_dimensions(model_name)
        if dimensions is None:
            raise ValueError(f"EMBEDDING_MODEL {model_name!r} is not supported by fastembed")
        self._model_name = model_name
        self._dimensions = dimensions
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed, list(texts))

    async def embed_query(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._embed, [text]))[0]

    def _load(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                logger.info("embeddings.model_loading", extra={"embedding_model": self._model_name})
                try:
                    self._model = TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir)
                except Exception as exc:
                    # Not cached: the next call tries again (e.g. once the network is back).
                    logger.error(
                        "embeddings.model_load_failed",
                        extra={"embedding_model": self._model_name, "error_type": type(exc).__name__},
                    )
                    raise EmbeddingFailed(
                        "The embedding model could not be loaded. Check the server's network access "
                        "(the model is downloaded on first use) and try again."
                    ) from exc
                logger.info("embeddings.model_loaded", extra={"embedding_model": self._model_name})
            return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            return [vector.tolist() for vector in model.embed(texts, batch_size=self._batch_size)]
        except Exception as exc:
            logger.error(
                "embeddings.inference_failed",
                extra={"embedding_model": self._model_name, "error_type": type(exc).__name__, "count": len(texts)},
            )
            raise EmbeddingFailed() from exc
