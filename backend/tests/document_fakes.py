"""In-memory doubles for the Phase 2 ports (documents, retrieval)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from agentops.domain.documents import (
    Chunk,
    Document,
    DocumentNotFound,
    DocumentStatus,
    EmbeddedChunk,
    Embedder,
)
from agentops.domain.retrieval import RetrievedChunk


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self.documents: dict[UUID, Document] = {}
        self.contents: dict[UUID, bytes] = {}
        self.chunks: dict[UUID, list[EmbeddedChunk]] = {}
        self.chunk_models: dict[UUID, str] = {}

    async def add(self, document: Document, content: bytes) -> bool:
        if any(d.checksum_sha256 == document.checksum_sha256 for d in self.documents.values()):
            return False
        self.documents[document.id] = document
        self.contents[document.id] = content
        return True

    async def get(self, document_id: UUID) -> Document | None:
        return self.documents.get(document_id)

    async def get_by_checksum(self, checksum_sha256: str) -> Document | None:
        return next((d for d in self.documents.values() if d.checksum_sha256 == checksum_sha256), None)

    async def list_recent(self, limit: int) -> list[Document]:
        return sorted(self.documents.values(), key=lambda d: d.created_at, reverse=True)[:limit]

    async def get_content(self, document_id: UUID) -> bytes | None:
        return self.contents.get(document_id)

    async def mark(self, document_id: UUID, status: DocumentStatus, *, error_code: str | None, at: datetime) -> None:
        document = self.documents.get(document_id)
        if document is None:
            return
        changes: dict[str, object] = {"status": status, "error_code": error_code, "updated_at": at}
        if status is not DocumentStatus.READY:
            changes |= {"chunk_count": 0, "embedding_model": None}
            self.chunks.pop(document_id, None)
        self.documents[document_id] = replace(document, **changes)  # type: ignore[arg-type]

    async def save_chunks(
        self,
        document_id: UUID,
        chunks: Sequence[EmbeddedChunk],
        *,
        embedding_model: str,
        at: datetime,
        deprecated: bool = False,
    ) -> Document:
        document = self.documents.get(document_id)
        if document is None:
            raise DocumentNotFound()
        self.chunks[document_id] = list(chunks)
        self.chunk_models[document_id] = embedding_model
        ready = replace(
            document,
            status=DocumentStatus.READY,
            error_code=None,
            chunk_count=len(chunks),
            embedding_model=embedding_model,
            updated_at=at,
            deprecated=deprecated,
        )
        self.documents[document_id] = ready
        return ready

    async def get_chunk(self, document_id: UUID, chunk_id: UUID) -> Chunk | None:
        return next((c.chunk for c in self.chunks.get(document_id, []) if c.chunk.id == chunk_id), None)

    async def delete(self, document_id: UUID) -> bool:
        if self.documents.pop(document_id, None) is None:
            return False
        self.contents.pop(document_id, None)
        self.chunks.pop(document_id, None)
        return True

    def alive_chunk_ids(self) -> set[UUID]:
        return {c.chunk.id for chunks in self.chunks.values() for c in chunks}


class InMemoryRetriever:
    """Brute-force cosine search with the same scope rules as `PgVectorRetriever`."""

    def __init__(self, repository: InMemoryDocumentRepository, embedder: Embedder) -> None:
        self._repo = repository
        self._embedder = embedder
        self.calls: list[tuple[str, int, list[UUID] | None]] = []

    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        self.calls.append((query, top_k, list(document_ids) if document_ids is not None else None))
        vector = await self._embedder.embed_query(query)
        scored: list[tuple[float, Document, EmbeddedChunk]] = []
        for document_id, chunks in self._repo.chunks.items():
            document = self._repo.documents.get(document_id)
            if document is None or document.status is not DocumentStatus.READY:
                continue
            if document_ids is not None and document_id not in document_ids:
                continue
            if self._repo.chunk_models.get(document_id) != self._embedder.model:
                continue
            scored.extend((_cosine(vector, c.embedding), document, c) for c in chunks)
        scored.sort(key=lambda item: (-item[0], str(item[2].chunk.id)))
        return [
            RetrievedChunk(
                chunk_id=c.chunk.id,
                document_id=d.id,
                document_title=d.title,
                rank=rank,
                score=score,
                text=c.chunk.text,
                location=c.chunk.location,
            )
            for rank, (score, d, c) in enumerate(scored[:top_k], start=1)
        ]


class StaticRetriever:
    """Returns a fixed result list (for RAG tests that don't care about embeddings)."""

    def __init__(self, results: list[RetrievedChunk]) -> None:
        self.results = results
        self.calls = 0

    async def search(
        self, query: str, *, top_k: int, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        self.calls += 1
        return self.results[:top_k]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def make_pdf(pages: Sequence[str]) -> bytes:
    """A minimal valid PDF with one Helvetica text line per page (ASCII only)."""
    count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_encrypted_pdf() -> bytes:
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(["secret text"]))))
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FlakyEmbedder:
    """A `FakeEmbedder` that raises `EmbeddingFailed` for the next `failures` calls."""

    def __init__(self, failures: int = 0, *, dimensions: int = 384) -> None:
        from agentops.infrastructure.embeddings.fake import FakeEmbedder

        self._inner = FakeEmbedder(dimensions=dimensions)
        self.failures = failures
        self.wrong_dimensions = False

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._maybe_fail()
        vectors = await self._inner.embed_documents(texts)
        return [v[:-1] for v in vectors] if self.wrong_dimensions else vectors

    async def embed_query(self, text: str) -> list[float]:
        self._maybe_fail()
        return await self._inner.embed_query(text)

    def _maybe_fail(self) -> None:
        from agentops.domain.documents import EmbeddingFailed

        if self.failures > 0:
            self.failures -= 1
            raise EmbeddingFailed()
