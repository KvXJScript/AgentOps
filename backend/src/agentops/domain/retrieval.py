from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ChunkLocation:
    char_start: int
    char_end: int
    page: int | None = None
    heading_path: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk returned by retrieval, already filtered by the data-scope rules."""

    chunk_id: UUID
    document_id: UUID
    document_title: str
    rank: int  # 1-based
    score: float  # cosine similarity, higher is better
    text: str
    location: ChunkLocation


@dataclass(frozen=True, slots=True)
class Citation:
    """A resolved `[S1]`-style reference, snapshotted so it survives deletion of its document.

    `document_deleted` is computed when read back, never stored.
    """

    marker: str
    chunk_id: UUID
    document_id: UUID
    document_title: str
    excerpt: str
    location: ChunkLocation
    document_deleted: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    """What retrieval returned for one assistant message (inspection/debugging).

    `chunks[i].text` holds only an excerpt, never the full chunk.
    """

    chunks: tuple[RetrievedChunk, ...]
    cited_chunk_ids: tuple[UUID, ...]
    top_k: int
    min_score: float
    embedding_model: str


class Retriever(Protocol):
    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        """Return up to `top_k` chunks from ready documents, best first.

        Scope rules (ready, not deleted, within `document_ids` when given) are
        applied before results leave the retriever.
        """
        ...
