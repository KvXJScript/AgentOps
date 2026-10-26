"""An in-memory index over the fixture corpus, built with the production chunker and embedder.

Chunking and embedding go through exactly the code the ingestion pipeline uses (`normalize_segments`,
`chunk_document`, `embedding_input`, `FastEmbedEmbedder`). Search is an exact brute-force cosine scan, which is
what `PgVectorRetriever` computes (`1 - cosine_distance`, ties by chunk id) minus HNSW approximation, so the
suite runs without Docker. A simple BM25 and a reciprocal-rank-fusion ranker sit next to it for the
hybrid-retrieval experiment only; production retrieval is not touched.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from agentops.application.documents.chunking import ChunkingConfig, chunk_document
from agentops.application.documents.normalize import normalize_segments
from agentops.application.documents.pipeline import embedding_input
from agentops.config import Settings
from agentops.domain.documents import ChunkDraft, Embedder, ExtractedSegment
from agentops.domain.retrieval import ChunkLocation, RetrievedChunk

CORPUS_DIR = Path(__file__).parent / "corpus"
_NAMESPACE = uuid.UUID("6f1c2a52-3c1e-4f6c-9d2e-6a7e0e7a0b01")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def default_chunking() -> ChunkingConfig:
    fields = Settings.model_fields
    return ChunkingConfig(
        max_chars=int(fields["chunk_max_chars"].default), overlap_chars=int(fields["chunk_overlap_chars"].default)
    )


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str  # the corpus file name, as an upload would set it
    ordinal: int
    text: str
    location: ChunkLocation
    embedding: tuple[float, ...]
    tokens: tuple[str, ...]  # for BM25 (embedding input: heading + text)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def chunk_corpus(corpus_dir: Path) -> list[tuple[str, list[ChunkDraft]]]:
    """Every .md/.txt file, normalised and chunked exactly as ingestion does (Markdown is heading-aware)."""
    config = default_chunking()
    out = []
    for path in sorted(corpus_dir.iterdir()):
        if path.suffix not in {".md", ".txt"}:
            continue
        normalized = normalize_segments([ExtractedSegment(text=path.read_text(encoding="utf-8"))])
        out.append((path.name, chunk_document(normalized, markdown=path.suffix == ".md", config=config)))
    return out


class CorpusIndex:
    def __init__(self, chunks: list[IndexedChunk], embedder: Embedder) -> None:
        self.chunks = chunks
        self.embedder = embedder
        self._bm25 = _Bm25([c.tokens for c in chunks])

    @classmethod
    async def build(cls, embedder: Embedder, *, corpus_dir: Path = CORPUS_DIR) -> CorpusIndex:
        drafts_by_doc = chunk_corpus(corpus_dir)
        inputs = [embedding_input(d) for _, drafts in drafts_by_doc for d in drafts]
        vectors = iter(await embedder.embed_documents(inputs))
        chunks: list[IndexedChunk] = []
        for name, drafts in drafts_by_doc:
            document_id = uuid.uuid5(_NAMESPACE, name)
            for d in drafts:
                chunks.append(
                    IndexedChunk(
                        chunk_id=uuid.uuid5(document_id, str(d.ordinal)),
                        document_id=document_id,
                        document_title=name,
                        ordinal=d.ordinal,
                        text=d.text,
                        location=d.location,
                        embedding=tuple(next(vectors)),
                        tokens=tuple(tokenize(embedding_input(d))),
                    )
                )
        return cls(chunks, embedder)

    def document_id(self, title: str) -> UUID:
        return uuid.uuid5(_NAMESPACE, title)

    # --- rankers: each returns every in-scope chunk, best first, with its score ---
    async def vector_ranking(
        self, query: str, document_ids: Sequence[UUID] | None = None
    ) -> list[tuple[IndexedChunk, float]]:
        vector = await self.embedder.embed_query(query)
        scored = [(c, _cosine(vector, c.embedding)) for c in self._scope(document_ids)]
        scored.sort(key=lambda item: (-item[1], str(item[0].chunk_id)))
        return scored

    def bm25_ranking(self, query: str) -> list[tuple[IndexedChunk, float]]:
        scores = self._bm25.scores(tokenize(query))
        scored = list(zip(self.chunks, scores, strict=True))
        scored.sort(key=lambda item: (-item[1], str(item[0].chunk_id)))
        return scored

    async def hybrid_ranking(self, query: str, *, k: int = 60) -> list[tuple[IndexedChunk, float]]:
        """Reciprocal-rank fusion of the vector and BM25 rankings (BM25 zero-score chunks contribute nothing)."""
        fused: dict[UUID, float] = {}
        by_id = {c.chunk_id: c for c in self.chunks}
        for rank, (chunk, _) in enumerate(await self.vector_ranking(query), start=1):
            fused[chunk.chunk_id] = fused.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
        for rank, (chunk, score) in enumerate(self.bm25_ranking(query), start=1):
            if score > 0:
                fused[chunk.chunk_id] = fused.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
        ordered = sorted(fused.items(), key=lambda item: (-item[1], str(item[0])))
        return [(by_id[chunk_id], score) for chunk_id, score in ordered]

    def _scope(self, document_ids: Sequence[UUID] | None) -> list[IndexedChunk]:
        if document_ids is None:
            return self.chunks
        allowed = set(document_ids)
        return [c for c in self.chunks if c.document_id in allowed]


def to_retrieved(ranking: Sequence[tuple[IndexedChunk, float]], top_k: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            rank=rank,
            score=score,
            text=c.text,
            location=c.location,
        )
        for rank, (c, score) in enumerate(ranking[:top_k], start=1)
    ]


class IndexRetriever:
    """The `Retriever` port over the corpus index (vector only, like production)."""

    def __init__(self, index: CorpusIndex) -> None:
        self._index = index
        self.calls = 0

    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        self.calls += 1
        return to_retrieved(await self._index.vector_ranking(query, document_ids), top_k)


class _Bm25:
    def __init__(self, docs: list[tuple[str, ...]], *, k1: float = 1.2, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._tf = [Counter(d) for d in docs]
        self._len = [len(d) for d in docs]
        self._avg = sum(self._len) / len(docs) if docs else 0.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, length in zip(self._tf, self._len, strict=True):
            score = 0.0
            for term in query:
                f = tf.get(term, 0)
                if f:
                    norm = f + self._k1 * (1 - self._b + self._b * length / self._avg)
                    score += self._idf[term] * f * (self._k1 + 1) / norm
            out.append(score)
        return out
