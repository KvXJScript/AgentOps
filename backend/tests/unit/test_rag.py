from __future__ import annotations

import uuid

from agentops.application.rag import (
    RagConfig,
    RagService,
    excerpt,
    finalize_answer,
    grounded_system_prompt,
    strip_markers,
)
from agentops.domain.models import Grounding
from agentops.domain.retrieval import ChunkLocation, RetrievedChunk
from tests.document_fakes import StaticRetriever


def _chunk(text: str, score: float, rank: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=f"Doc {rank}",
        rank=rank,
        score=score,
        text=text,
        location=ChunkLocation(char_start=0, char_end=len(text), heading_path=("H",)),
    )


async def test_gather_labels_only_chunks_above_min_score_within_budget() -> None:
    chunks = [_chunk("a" * 100, 0.9, 1), _chunk("b" * 100, 0.8, 2), _chunk("c" * 100, 0.7, 3), _chunk("d", 0.1, 4)]
    retriever = StaticRetriever(chunks)
    rag = RagService(retriever, embedding_model="m", config=RagConfig(top_k=4, min_score=0.5, context_max_chars=300))

    context = await rag.gather("q")

    assert context.retrieved == tuple(chunks)
    assert [(e.label, e.chunk.rank) for e in context.evidence] == [("S1", 1), ("S2", 2)]  # third exceeds budget
    assert context.sufficient
    assert "c" * 100 not in context.rendered
    trace = context.trace(context.evidence[:1])
    assert trace.cited_chunk_ids == (chunks[0].chunk_id,)
    assert [c.rank for c in trace.chunks] == [1, 2, 3, 4]
    assert (trace.top_k, trace.min_score, trace.embedding_model) == (4, 0.5, "m")
    assert '<evidence label="S2"' in grounded_system_prompt("BASE", context)


async def test_gather_is_insufficient_when_nothing_clears_the_threshold() -> None:
    rag = RagService(StaticRetriever([_chunk("x", 0.34, 1)]), embedding_model="m", config=RagConfig(min_score=0.35))

    context = await rag.gather("q")

    assert not context.sufficient
    assert context.evidence == ()
    assert context.trace().chunks[0].score == 0.34


async def test_search_uses_configured_top_k_by_default() -> None:
    retriever = StaticRetriever([_chunk(str(i), 0.9, i) for i in range(1, 10)])
    rag = RagService(retriever, embedding_model="m", config=RagConfig(top_k=3))

    assert len(await rag.search("q")) == 3
    assert len(await rag.search("q", top_k=5)) == 5


async def test_finalize_answer_keeps_valid_and_drops_invalid_markers() -> None:
    rag = RagService(
        StaticRetriever([_chunk("alpha", 0.9, 1), _chunk("beta", 0.8, 2)]), embedding_model="m", config=RagConfig()
    )
    context = await rag.gather("q")

    answer = finalize_answer("Beta [S2], alpha [S1] [S1], made up [S3] and [S12].", context.evidence)

    assert answer.content == "Beta [S2], alpha [S1] [S1], made up and."
    assert [c.marker for c in answer.citations] == ["S2", "S1"]
    assert answer.citations[0].chunk_id == context.evidence[1].chunk.chunk_id
    assert answer.grounding is Grounding.GROUNDED
    assert answer.invalid_markers == ("S3", "S12")


def test_excerpt_and_strip_markers() -> None:
    long = "word " * 200

    assert len(excerpt(long)) <= 300
    assert excerpt(long).endswith("…")
    assert excerpt("  short\n\ntext ") == "short text"
    assert strip_markers("A [S1] b [S22].") == "A b."


def test_finalize_answer_canonicalises_lenticular_markers() -> None:
    from agentops.application.grounding import label_evidence
    from agentops.application.rag import finalize_answer

    evidence = label_evidence([_chunk("Allowance: 1,200 EUR", 0.8, 1)])

    result = finalize_answer("The allowance is 1,200 EUR【S1】.", evidence)

    assert result.grounding is Grounding.GROUNDED
    assert [c.marker for c in result.citations] == ["S1"]
    assert result.content == "The allowance is 1,200 EUR[S1]."
