# ADR 0002: PostgreSQL with pgvector as the only datastore

Status: accepted · Date: 2026-10-26

## Context

The app stores conversations and messages, uploaded files, chunks with embeddings, retrieval traces, citation
snapshots, report runs with stage timelines, and action proposals with safety-relevant state transitions. It runs
locally with `docker compose up -d db` and in CI as a single service container. Correctness depends on a few
atomic operations: one pending proposal per report, a `pending → executing` claim that only one approver can win,
and chunks plus the `ready` status written together.

## Decision

Use one PostgreSQL 17 instance with the `vector` extension (`pgvector/pgvector:pg17`) for everything, with schema
managed by Alembic (`backend/alembic/versions/0001`–`0004`).

- Embeddings live in `document_chunks.embedding vector(384)` next to the chunk text, offsets and
  `embedding_model`, with an HNSW `vector_cosine_ops` index (m=16, ef_construction=64).
- Data-scope rules are applied in the same SQL as the similarity search (`PgVectorRetriever.search`): the document
  must be `ready`, the chunk must have the current `embedding_model`, and the document must be in `document_ids`
  when that is given. `SET LOCAL hnsw.iterative_scan = strict_order` makes filtered queries still return `top_k`.
- Original bytes are kept in `document_sources` (bytea), so failed ingestions can be retried.
- Flexible, read-mostly structures are JSONB: `messages.citations`, `message_retrievals.chunks`,
  `report_runs.stages`/`report`, `action_proposals.arguments`/`related_issues`.
- Safety invariants are database constraints or conditional updates: a partial unique index on
  `action_proposals(source_id) WHERE status = 'pending'`, the check `status <> 'executed' OR result_issue_id IS NOT NULL`,
  and `claim_for_execution` as a single `UPDATE … WHERE status='pending' AND payload_hash=… AND expires_at > now`.

## Alternatives considered

- **A dedicated vector DB (Qdrant, Chroma, Pinecone).** That is a second system to run and back up, and the
  scope filter, document status and citation snapshot would live in two places that can disagree.
- **Object storage for uploads.** It isn't needed at a 10 MiB upload cap for a single user.
- **SQLite + sqlite-vec.** It is simpler to run, but its concurrency semantics are weaker for the approval race,
  and it has no HNSW with filtered iterative scan.

## Consequences

- Deleting a document is one `DELETE` that cascades to sources and chunks. Citation snapshots stay because they
  are copies, not foreign keys.
- Integration tests (`make test-integration`, required in CI with `REQUIRE_DB=1`) exercise the real pgvector
  search, scope rules and the approval race (`test_concurrent_approvals_against_postgres_execute_once`).
- The vector dimension is fixed by migration. `build_embedder` refuses a model with a different size, so changing
  the model needs a migration and re-ingestion.
- Storing file bytes in Postgres doesn't scale to large corpora. The `DocumentRepository.get_content` port is
  where an object-store adapter would plug in.
