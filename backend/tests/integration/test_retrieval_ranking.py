"""`PgVectorRetriever` ranking adjustments (identifier boost, deprecated penalty) against real PostgreSQL.

`FakeEmbedder` similarities are fixed by the texts, so each test first pins the plain vector order, then checks
the adjusted one. Boost/penalty values here are larger than the defaults: these tests check the SQL mechanics
(candidate queries, scope filters, reported score), not the calibration, which `make eval` measures.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentops.domain.documents import Chunk, Document, EmbeddedChunk
from agentops.domain.ranking import RankingPolicy, contains_identifier, identifier_pattern, query_identifiers
from agentops.domain.retrieval import ChunkLocation
from agentops.infrastructure.db.documents import PgVectorRetriever, SqlDocumentRepository
from agentops.infrastructure.embeddings.fake import FakeEmbedder
from tests.integration.test_documents_repository import T0, _document

pytestmark = pytest.mark.integration

ALERT = "Alert PAGER-7731 means replication lag on the replica."
CURRENT_SEV1 = "SEV1 incidents must be acknowledged within 15 minutes by on-call."
OLD_SEV1 = "SEV1 incidents must be acknowledged within 30 minutes by on-call."
NOTES = (
    "What does the alert dashboard mean?",
    "What does an alert mean for the on-call engineer and the team?",
    "An alert does mean the on-call engineer must look.",
)
PAGER_QUERY = "What does alert PAGER-7731 mean?"
SEV1_QUERY = "How fast must SEV1 incidents be acknowledged within 30 minutes?"


async def _store(
    docs: SqlDocumentRepository,
    embedder: FakeEmbedder,
    document: Document,
    texts: tuple[str, ...],
    *,
    deprecated: bool = False,
) -> None:
    await docs.add(document, document.checksum_sha256.encode())
    vectors = await embedder.embed_documents(texts)
    chunks = [
        EmbeddedChunk(
            chunk=Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                ordinal=i,
                text=text,
                location=ChunkLocation(char_start=i * 100, char_end=i * 100 + len(text)),
            ),
            embedding=vector,
        )
        for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    at = T0 + timedelta(seconds=1)
    await docs.save_chunks(document.id, chunks, embedding_model=embedder.model, at=at, deprecated=deprecated)


@pytest.fixture
async def corpus(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Document]:
    docs, embedder = SqlDocumentRepository(session_factory), FakeEmbedder()
    documents = {name: _document(title=name) for name in ("Runbook", "Legacy", "Notes")}
    await _store(docs, embedder, documents["Runbook"], (ALERT, CURRENT_SEV1))
    await _store(docs, embedder, documents["Legacy"], (OLD_SEV1,), deprecated=True)
    await _store(docs, embedder, documents["Notes"], NOTES)
    return documents


def _retriever(sessions: async_sessionmaker[AsyncSession], **policy: float) -> PgVectorRetriever:
    return PgVectorRetriever(sessions, FakeEmbedder(), ranking=RankingPolicy(**policy))


async def test_identifier_boost_brings_an_exact_match_from_outside_the_vector_top_k(
    corpus: dict[str, Document], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    plain = await _retriever(session_factory).search(PAGER_QUERY, top_k=2)
    boosted = await _retriever(session_factory, identifier_boost=0.3).search(PAGER_QUERY, top_k=2)

    assert [r.text for r in plain] == [NOTES[0], NOTES[1]]
    assert [r.text for r in boosted] == [ALERT, NOTES[0]]
    assert [r.rank for r in boosted] == [1, 2]
    assert boosted[0].score == pytest.approx(0.408, abs=0.01)  # still the cosine similarity, not boosted
    assert boosted[0].score < boosted[1].score


async def test_identifier_boost_respects_scope_and_ignores_absent_or_missing_identifiers(
    corpus: dict[str, Document], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    plain = _retriever(session_factory)
    boosted = _retriever(session_factory, identifier_boost=0.3)
    scope = [corpus["Notes"].id, corpus["Legacy"].id]

    scoped = await boosted.search(PAGER_QUERY, top_k=6, document_ids=scope)
    assert ALERT not in {r.text for r in scoped}
    assert {r.document_id for r in scoped} <= set(scope)
    assert await boosted.search(PAGER_QUERY, top_k=6, document_ids=[]) == []
    for query in ("What does alert PAGER-9999 mean?", "What does the alert mean?"):  # absent / no identifier
        assert await boosted.search(query, top_k=6) == await plain.search(query, top_k=6)


async def test_deprecated_penalty_demotes_without_changing_scores(
    corpus: dict[str, Document], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    plain = await _retriever(session_factory).search(SEV1_QUERY, top_k=3)
    demoted = await _retriever(session_factory, deprecated_penalty=0.2).search(SEV1_QUERY, top_k=3)
    top_one = await _retriever(session_factory, deprecated_penalty=0.2).search(SEV1_QUERY, top_k=1)

    assert [r.text for r in plain[:2]] == [OLD_SEV1, CURRENT_SEV1]
    assert [r.text for r in demoted[:2]] == [CURRENT_SEV1, OLD_SEV1]
    assert {r.chunk_id: r.score for r in demoted} == {r.chunk_id: r.score for r in plain}
    assert [r.text for r in top_one] == [CURRENT_SEV1]  # the non-deprecated candidate query fills the top-k
    only_legacy = await _retriever(session_factory, deprecated_penalty=0.2).search(
        SEV1_QUERY, top_k=3, document_ids=[corpus["Legacy"].id]
    )
    assert [r.text for r in only_legacy] == [OLD_SEV1]  # demoted, never dropped


async def test_deprecated_flag_round_trips(
    corpus: dict[str, Document], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    docs = SqlDocumentRepository(session_factory)
    legacy, runbook = await docs.get(corpus["Legacy"].id), await docs.get(corpus["Runbook"].id)

    assert legacy is not None
    assert legacy.deprecated is True
    assert runbook is not None
    assert runbook.deprecated is False


async def test_eval_index_ranks_exactly_like_pgvector_retriever(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The eval suite's in-memory mirror (`evals/index.py`) and the SQL path agree on every retrieval case."""
    from evals.__main__ import load_suite
    from evals.index import CorpusIndex, default_ranking

    embedder = FakeEmbedder()
    index = await CorpusIndex.build(embedder)
    docs = SqlDocumentRepository(session_factory)
    for title in sorted({c.document_title for c in index.chunks}):
        chunks = [c for c in index.chunks if c.document_title == title]
        document = replace(_document(title=title), id=chunks[0].document_id)
        await docs.add(document, title.encode())
        embedded = [
            EmbeddedChunk(
                chunk=Chunk(
                    id=c.chunk_id, document_id=c.document_id, ordinal=c.ordinal, text=c.text, location=c.location
                ),
                embedding=c.embedding,
            )
            for c in chunks
        ]
        await docs.save_chunks(
            document.id, embedded, embedding_model=embedder.model, at=T0, deprecated=chunks[0].deprecated
        )
    policy = default_ranking()
    retriever = PgVectorRetriever(session_factory, embedder, ranking=policy)
    queries = [c["query"] for c in load_suite()["cases"] if c["category"] == "retrieval"]
    by_id = {c.chunk_id: c for c in index.chunks}

    def adjusted(query: str, chunk_id: uuid.UUID, score: float) -> float:
        identifiers = query_identifiers(query)
        match = bool(identifiers) and contains_identifier(by_id[chunk_id].text, identifier_pattern(identifiers))
        return round(policy.rank_score(score, deprecated=by_id[chunk_id].deprecated, identifier_match=match), 5)

    assert any(c.deprecated for c in index.chunks)
    assert any(query_identifiers(q) for q in queries)
    for query in queries:
        expected = [
            (adjusted(query, c.chunk_id, s), c.chunk_id) for c, s in (await index.ranking(query, policy=policy))
        ]
        actual = [(adjusted(query, r.chunk_id, r.score), r.chunk_id) for r in await retriever.search(query, top_k=6)]
        # Same order of adjusted scores; ids must match wherever that score is not tied (the fake embedder gives
        # many exact ties, which float32 vs float64 arithmetic may break differently).
        assert [k for k, _ in actual] == [k for k, _ in expected[:6]], query
        tied = {k for k, _ in expected if sum(k == other for other, _ in expected) > 1}
        assert [i for k, i in actual if k not in tied] == [i for k, i in expected[:6] if k not in tied], query
