# ADR 0007: Identifier boost and deprecated-document penalty on top of vector ranking

Status: accepted · Date: 2026-10-26 · Extends [ADR 0004](0004-local-multilingual-embeddings-and-measured-chunk-size.md)

## Context

The eval suite (`docs/evaluation.md`) tracked four retrieval weaknesses as `known_issue` cases. Two of them are
ranking problems with a clear cause:

- `ret-identifier-pager`: dense retrieval ranks the chunk that literally contains `PAGER-7731` 2nd (cosine 0.395)
  behind an unrelated Polish chunk (0.446).
- `ret-confusable-sev1`: the deprecated v1 runbook (0.780) outranks the current v3 (0.756), even when the query
  says "current runbook v3". The model then sees the stale 30-minute rule first.

Plain BM25 + RRF hybrid was measured earlier and rejected (better on 1 case, worse on 5): it fixed the identifier
case but added noise to every cross-lingual and paraphrase query.

## Decision

Keep vector similarity as the only score and add two narrow ranking adjustments (`agentops.domain.ranking`):

- **Identifier boost, `RETRIEVAL_IDENTIFIER_BOOST = 0.1`.** Applies only when the query contains an
  identifier-like token: ASCII letters/digits joined by `-` or `_`, starting with a letter, containing a digit,
  at least 4 characters (`PAGER-7731`, `ABC-123`, `INC-2024-001`, `ERR_42`; not `v3`, `SEV1`, `2018`, `e-mail`).
  A chunk containing one of them as a whole token (case-insensitive, PostgreSQL `~*`) ranks as if its similarity
  were 0.1 higher. A query without such tokens is ranked exactly as before.
- **Deprecated penalty, `RETRIEVAL_DEPRECATED_PENALTY = 0.1`.** Markdown documents may start with front matter
  (`---` / `status: deprecated` / `---`). Ingestion parses it into `documents.deprecated` (migration 0006) and
  removes the block before chunking. Chunks of a deprecated document rank as if their similarity were 0.1 lower.
  They are demoted, never filtered: a question only a deprecated document answers still finds it.
- **The reported `score` stays the cosine similarity.** The adjustments only reorder results, so
  `RETRIEVAL_MIN_SCORE` keeps its meaning: an identifier match can't push an irrelevant chunk over the evidence
  threshold, and a deprecated chunk isn't dropped for being deprecated. The retrieval inspector can therefore
  show a rank-2 chunk with a higher score than rank 1.
- **SQL keeps the scope rules and the HNSW path.** `PgVectorRetriever` runs (1) the unchanged vector top-k, (2)
  only if (1) holds a deprecated chunk, the vector top-k of non-deprecated chunks, and (3) only if the query has
  identifiers, the top-k matching chunks. Every query carries the same scope filters (ready, current embedding
  model, `document_ids`). This candidate set is exact for the adjusted order, so the eval index (which ranks the
  whole corpus with the same `RankingPolicy`) measures what production returns. An integration test checks that
  the two agree on every retrieval case against real PostgreSQL.
- Setting either value to `0` restores plain vector ranking for that signal.

0.1 is the smallest round value that fixes both cases with margin (the gaps are 0.051 and 0.024). On the suite
0.05 fixes only the deprecated case, and 0.1, 0.2 and 0.3 give identical ranks on every case, with the legacy
runbook still in the top-k where a case expects it.

## Alternatives considered

- **Plain hybrid BM25 + RRF.** Rejected before (vector 9/12 vs hybrid 7/12 within max rank).
- **Filtering deprecated documents out.** It loses audits and questions about old rules, and it is already possible
  by deleting the document or scoping the question.
- **A boosted `score`.** Simpler to display, but it would make `RETRIEVAL_MIN_SCORE` mean "similarity plus
  heuristics" and let a lexical match alone clear the threshold.
- **Treating `v3`/`SEV1` as identifiers.** They appear in both runbooks and across the corpus, so they are not
  discriminating evidence. The `ret-no-identifier-unchanged` case pins that they don't trigger the boost.
- **`supersedes:` links between documents.** No consumer yet. The key is accepted and ignored.
- **A trigram index for the identifier query.** Not needed at this scale. The regex filter is a sequential scan
  over the in-scope chunks and runs only for identifier queries.

## Consequences

- Eval: `ret-identifier-pager` and `ret-confusable-sev1` now pass, and two passing cases improve (rank 2 → 1). No
  case got worse, and no rank changed for a query without identifiers or deprecated candidates. Three guard cases
  pin this (an absent identifier, `v3`/`SEV1` not treated as identifiers, and an identifier only in an
  out-of-scope document).
- Not fixed: `ret-en-core-hours` (a paraphrase problem, with no identifier or status signal) and
  `ret-neg-adjacent-parental` (a threshold problem, since scores are unchanged).
- Documents ingested before migration 0006 count as current until they are re-ingested.
- A document is marked deprecated only by its own front matter. There is no API to toggle it yet.
