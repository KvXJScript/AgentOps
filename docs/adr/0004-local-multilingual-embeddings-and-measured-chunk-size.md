# ADR 0004: Local multilingual embeddings, with chunk size and threshold set from measurement

Status: accepted · Date: 2026-10-26

## Context

Documents and questions are in English and Polish, and cross-lingual questions (PL → EN and EN → PL) must work.
The app should run without a paid embedding API, keep document text on the machine for indexing, and be
deterministic in CI.

## Decision

- Embed with fastembed (ONNX, CPU) using `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (384 dims, `EMBEDDING_MODEL`). The model loads lazily and runs in a worker thread (`asyncio.to_thread`), so it
  never blocks the event loop. `FakeEmbedder` exists for unit tests only.
- `CHUNK_MAX_CHARS = 500` (overlap `CHUNK_OVERLAP_CHARS = 80`). The model truncates input at 128 tokens, and about
  250 tokens per 1000 chars were measured for both EN and PL, so anything much past 500 chars would not be
  embedded at all. The nearest heading is prepended to the embedding input only, not to the stored text.
- `RETRIEVAL_MIN_SCORE = 0.35`, calibrated on a small set. Relevant pairs scored 0.49–0.78; off-topic queries
  mostly scored 0.02–0.24, with one adjacent-topic query at 0.47.
- Each chunk stores `embedding_model`, and retrieval compares only chunks of the current model.

## Alternatives considered

- **Hosted embeddings (OpenAI, Gemini).** Better English quality, but a second key, a per-token cost, document
  text sent off-machine, and non-deterministic CI.
- **Larger local models (e5, bge-m3).** Better recall, but a larger download and slower CPU ingestion. Worth
  revisiting with the eval suite.
- **~1000-char chunks.** They were rejected by the token measurement above, not by taste.
- **Hybrid BM25 + vector (RRF).** It was measured in `evals/index.py` and rejected: vector 9/12 vs hybrid 7/12
  cases within the max rank. It fixed the exact-identifier case but regressed the cross-lingual and paraphrase
  cases (see `docs/evaluation.md`).

## Consequences

- No key is needed for documents. The first use downloads about 220 MB; CI caches it, and `make eval` uses the
  real model.
- Measured weaknesses are tracked as `known_issue` eval cases: exact identifiers (`PAGER-7731` at rank 2), a
  deprecated near-duplicate outranking the current document, and an adjacent question clearing 0.35 at 0.411.
- Short chunks mean answers that span sections depend on `RAG_TOP_K`.
- Changing the model needs a migration (the vector size is fixed) and re-ingestion. Old chunks silently stop
  matching rather than mixing vector spaces.

## Addendum (2026-10-26)

The exact-identifier and deprecated near-duplicate weaknesses are now handled by two narrow ranking adjustments on
top of the vector score (identifier boost and deprecated-document penalty). The reported score and
`RETRIEVAL_MIN_SCORE` are unchanged. See [ADR 0007](0007-identifier-boost-and-deprecated-penalty.md). Hybrid
BM25 stays rejected.
