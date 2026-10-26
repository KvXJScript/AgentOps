"""Deterministic, offline embedder for tests and keyless demos (`EMBEDDING_PROVIDER=fake`).

A hashed bag of words: each lower-cased word adds ±1 to one of `dimensions`
buckets, then the vector is L2-normalised. Texts sharing words therefore have a
higher cosine similarity, which is enough to test ranking and thresholds.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

FAKE_EMBEDDING_MODEL = "fake-hash-bow"

_WORD = re.compile(r"\w+", re.UNICODE)


class FakeEmbedder:
    def __init__(self, *, dimensions: int = 384, model: str = FAKE_EMBEDDING_MODEL) -> None:
        self._dimensions = dimensions
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self._dimensions] += 1.0 if (value >> 32) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Cosine similarity is undefined for the zero vector; use a fixed unit vector instead.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]
