"""Retrieval-augmented generation building blocks, independent of the chat transport.

`RagService.gather` turns a question into an `EvidenceContext` (retrieved chunks,
the labelled evidence that fits the prompt budget, and the rendered evidence
block). `finalize_answer` resolves the model's `[S1]` markers against that
context only. Chat uses both; the report workflow can reuse them as well.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from agentops.application.grounding import (
    UNTRUSTED_EVIDENCE_RULES,
    LabelledEvidence,
    format_evidence,
    label_evidence,
    resolve_citations,
)
from agentops.domain.models import Grounding
from agentops.domain.retrieval import Citation, RetrievalTrace, RetrievedChunk, Retriever

logger = logging.getLogger(__name__)

EXCERPT_MAX_CHARS = 300

INSUFFICIENT_EVIDENCE_ANSWER = (
    "I couldn't find enough evidence in your documents to answer that. "
    "Try rephrasing the question, choosing other documents, or uploading one that covers the topic."
)

GROUNDED_INSTRUCTIONS = (
    "Answer the user's latest question using only the evidence below. "
    + UNTRUSTED_EVIDENCE_RULES
    + " Put the label of every piece of evidence you rely on right after the statement it supports. "
    "If the evidence does not answer the question, say that your documents don't cover it instead of guessing "
    "or using outside knowledge. You have no tools and cannot take actions; never claim otherwise."
)

_ANY_MARKER = re.compile(r"[ \t]?\[(S\d{1,3})\]")


@dataclass(frozen=True, slots=True)
class RagConfig:
    top_k: int = 6
    min_score: float = 0.35
    context_max_chars: int = 6000


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    retrieved: tuple[RetrievedChunk, ...]  # everything retrieval returned, best first
    evidence: tuple[LabelledEvidence, ...]  # labelled, above min_score and within the prompt budget
    rendered: str  # the <evidence> blocks for `evidence`
    top_k: int
    min_score: float
    embedding_model: str

    @property
    def sufficient(self) -> bool:
        return bool(self.evidence)

    def trace(self, cited: Sequence[LabelledEvidence] = ()) -> RetrievalTrace:
        return RetrievalTrace(
            chunks=tuple(_with_excerpt(c) for c in self.retrieved),
            cited_chunk_ids=tuple(item.chunk.chunk_id for item in cited),
            top_k=self.top_k,
            min_score=self.min_score,
            embedding_model=self.embedding_model,
        )


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    content: str  # model answer with unresolvable markers removed
    citations: tuple[Citation, ...]
    cited: tuple[LabelledEvidence, ...]
    grounding: Grounding
    invalid_markers: tuple[str, ...]


class RagService:
    def __init__(self, retriever: Retriever, *, embedding_model: str, config: RagConfig) -> None:
        self._retriever = retriever
        self._embedding_model = embedding_model
        self._config = config

    @property
    def config(self) -> RagConfig:
        return self._config

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    async def search(
        self, query: str, *, top_k: int | None = None, document_ids: Sequence[UUID] | None = None
    ) -> list[RetrievedChunk]:
        return await self._retriever.search(query, top_k=top_k or self._config.top_k, document_ids=document_ids)

    async def gather(self, query: str, *, document_ids: Sequence[UUID] | None = None) -> EvidenceContext:
        config = self._config
        retrieved = await self.search(query, document_ids=document_ids)
        relevant = [c for c in retrieved if c.score >= config.min_score]
        rendered, included = format_evidence(label_evidence(relevant), max_chars=config.context_max_chars)
        logger.info(
            "rag.retrieved",
            extra={
                "retrieved": len(retrieved),
                "above_min_score": len(relevant),
                "included": len(included),
                "top_score": round(max(c.score for c in retrieved), 4) if retrieved else None,
                "min_score": config.min_score,
                "chunk_ids": [str(c.chunk_id) for c in retrieved],
                "scores": [round(c.score, 4) for c in retrieved],
                "scoped": document_ids is not None,
            },
        )
        return EvidenceContext(
            retrieved=tuple(retrieved),
            evidence=tuple(included),
            rendered=rendered,
            top_k=config.top_k,
            min_score=config.min_score,
            embedding_model=self._embedding_model,
        )


def grounded_system_prompt(base_prompt: str, context: EvidenceContext) -> str:
    return f"{base_prompt}\n\n{GROUNDED_INSTRUCTIONS}\n\n{context.rendered}"


def finalize_answer(answer: str, evidence: Sequence[LabelledEvidence]) -> GroundedAnswer:
    """Resolve markers against this request's evidence only; drop the rest from the text."""
    resolved = resolve_citations(answer, evidence)
    invalid = set(resolved.invalid_markers)
    content = _ANY_MARKER.sub(lambda m: "" if m.group(1) in invalid else m.group(0), answer) if invalid else answer
    if invalid:
        logger.info("rag.invalid_markers_dropped", extra={"count": len(invalid), "markers": sorted(invalid)})
    return GroundedAnswer(
        content=content,
        citations=tuple(to_citation(item) for item in resolved.cited),
        cited=tuple(resolved.cited),
        grounding=Grounding.GROUNDED if resolved.cited else Grounding.INSUFFICIENT_EVIDENCE,
        invalid_markers=tuple(resolved.invalid_markers),
    )


def strip_markers(text: str) -> str:
    """Remove every `[Sn]` marker (e.g. from earlier turns, whose labels mean nothing now)."""
    return _ANY_MARKER.sub("", text)


def to_citation(item: LabelledEvidence) -> Citation:
    chunk = item.chunk
    return Citation(
        marker=item.label,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        excerpt=excerpt(chunk.text),
        location=chunk.location,
    )


def excerpt(text: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    cut = collapsed[: max_chars - 1]
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _with_excerpt(chunk: RetrievedChunk) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        rank=chunk.rank,
        score=chunk.score,
        text=excerpt(chunk.text),
        location=chunk.location,
    )
