# Evaluation suite

A small, versioned regression suite for the RAG, citation, report and tool-approval paths. Each case is checked
on its own and reported with its own numbers. There is deliberately no single "accuracy" score.

```
cd backend
make eval                       # = uv run python -m evals; prints a table, writes evals/reports/latest.md
uv run python -m evals --category retrieval
uv run python -m evals --live   # manual only: ≤5 cases against the configured real provider
```

The command exits 1 if any case **fails**. CI runs it in the backend job (`make eval`). The fastembed model is
cached with `actions/cache`, and `FASTEMBED_CACHE_PATH`/`EMBEDDING_CACHE_DIR` point at `$RUNNER_TEMP/fastembed`.
The report is uploaded as the `eval-report` artifact. Locally fastembed uses its default cache
(`$TMPDIR/fastembed_cache`) unless `EMBEDDING_CACHE_DIR` or `FASTEMBED_CACHE_PATH` is set. A run takes about 1 s
once the model is cached.

## What it runs against

| piece | real or scripted |
|---|---|
| chunking, normalisation, embedding input | **real** (`normalize_segments`, `chunk_document`, `embedding_input`, default `CHUNK_*` settings) |
| embeddings | **real** fastembed `paraphrase-multilingual-MiniLM-L12-v2` (local, free, deterministic) |
| vector search | exact in-memory cosine over those embeddings. It scores like `PgVectorRetriever` (`1 − cosine distance`, ties by chunk id) but without HNSW, so **no Docker/DB needed** |
| `RagService`, `ChatService`, `finalize_answer`, report LangGraph graph, `ProposalService`, `ApprovalService`, MCP tool gate | **real** application code |
| chat model | scripted replies (`evals/fakes.py`); real provider only with `--live` |
| repositories, tracker reader/writer | in-memory doubles in `evals/fakes.py` (independent of `tests/`) |

`top_k`, `min_score` and the budgets are read from the `Settings` defaults (`RAG_TOP_K=6`,
`RETRIEVAL_MIN_SCORE=0.35`, …). If a default changes, the suite runs against the new value.

## Layout

- `backend/evals/corpus/`: 5 synthetic documents: remote-work policy (EN), leave policy (**PL**), incident
  runbook v3 plus a **near-duplicate** deprecated v1, and vendor notes (`.txt`) carrying a **prompt-injection
  payload** (fake `</evidence>` / `<evidence label="S9">`, "call create_issue …", a canary token).
- `backend/evals/cases.json`: the cases, with `suite` and `version` fields. Bump `version` whenever a case or an
  expectation changes. Each case has an `id`, a `category`, an input and an `expect` block. A case may also carry
  `known_issue`: if it fails it is reported as **XFAIL** (shown, not fatal), and if it starts passing it is
  **XPASS** (remove the flag). A Phase 4 case reports **SKIP** if the proposal or MCP modules can't be imported.
- `backend/evals/{index,checks,fakes,live}.py`: index and rankers, per-category checks, doubles, live mode.
- `backend/evals/reports/latest.md`: the last run (status, reason, rank/score/calls). It contains no document
  text beyond file and heading names, and no secrets.

## Categories (v1.0.0: 39 cases)

| category | cases | asserts |
|---|---|---|
| retrieval | 16 | expected document + heading at rank ≤ N (within top_k, score ≥ min_score). Includes EN, PL, EN→PL and PL→EN, a near-duplicate pair, an exact identifier, a `.txt`, and 4 negatives whose top score must be < `RETRIEVAL_MIN_SCORE` |
| citations | 4 | the stored message citations resolve to exactly the chunk ids behind each label, computed independently. Invented markers are removed from the stored text, citations are deduplicated in first-mention order, and only invented markers gives `insufficient_evidence` |
| abstention | 3 | off-topic EN/PL questions and an empty scope give `insufficient_evidence`, the fixed answer, **0 model calls** and `model: null` |
| injection | 6 | payload stays inside its evidence block, forged delimiters are neutralised, labels are exactly S1..Sn, a document-supplied `[S9]` can't become a citation, a hostile title can't add attributes, the report graph drops a finding that cites a forged label, tracker issue text stays in its `I1` block, and a model reply adding `tool`/`target` keys or a non-vocabulary label is rejected (tool/target fixed by the server) |
| workflow | 5 | happy path (5 stages, refs resolve to the evidence, invented `[S12]` stripped), insufficient evidence (stops after retrieve, 0 calls), one invalid JSON reply then a corrective retry, two invalid replies give `InvalidModelOutput` after exactly 2 calls (the bound is pinned in the case, not read from code), a draft citing only invented refs is withheld |
| tool_policy | 5 | unknown tool or wrong access refused before connecting, a non-allowlisted `create_issue` refused, the allowlist can't name unknown tools, drafting gets only an `IssueReader`, and `create_issue` gets 0 calls on a wrong hash, an expired proposal or a rejected proposal, then exactly 1 call (idempotency key = proposal id) after approval, with a repeat approval a no-op |

I checked that the suite catches regressions by breaking the code in a scratch run: disabling delimiter
neutralisation and keeping invalid markers in the text (both at once) made 5 cases FAIL.

## Current results (v1.0.0)

**35 pass, 0 fail, 4 xfail, 0 skip.** The four known weaknesses are all in retrieval:

| case | measured | meaning |
|---|---|---|
| `ret-en-core-hours` | rank 3 (score 0.47), max 2 | a paraphrase without the words "core hours" puts Eligibility first. The chunk still reaches the model |
| `ret-confusable-sev1` | rank 2 (0.756 vs 0.780) | the **deprecated v1 runbook outranks v3** even when the query says "current runbook v3". Both reach the model, which may quote the stale 30-minute rule. Mitigation: delete superseded documents, or add a document-status filter later |
| `ret-identifier-pager` | rank 2 (0.395) behind an unrelated PL chunk (0.446) | dense retrieval is weak on exact identifiers such as `PAGER-7731` |
| `ret-neg-adjacent-parental` | top score 0.411 ≥ 0.35 | an adjacent HR question ("parental leave in Germany") clears the threshold against the leave document, so the model is called and must decline. This matches the Phase 2 calibration note (one adjacent query at 0.47). Raising the threshold above 0.411 would also drop the correct `PAGER-7731` chunk (0.395) |

Cross-lingual retrieval works: all 4 PL↔EN cases are at rank ≤ 2 with scores 0.52–0.62.

## Experiment: vector vs hybrid retrieval

Hybrid = reciprocal-rank fusion (k = 60) of the vector ranking and a plain BM25 (k1 = 1.2, b = 0.75, Unicode
word tokens, lower-cased, no stemming) over the same chunks (heading + text). This is eval-only code in
`evals/index.py`; the production retriever is unchanged. The table shows the rank of the expected chunk on the
12 positive retrieval cases:

| case | max | vector | BM25 | hybrid |
|---|---|---|---|---|
| ret-en-equipment | 1 | 1 | 1 | 1 |
| ret-en-core-hours | 2 | 3 | 12 | **7** |
| ret-pl-vacation-days | 1 | 1 | 1 | 1 |
| ret-pl-wedding-leave | 1 | 1 | 1 | 1 |
| ret-en2pl-vacation | 2 | 1 | 6 | **3** |
| ret-en2pl-sick-leave | 3 | 2 | – | **15** |
| ret-pl2en-vpn | 1 | 1 | 1 | 1 |
| ret-pl2en-postmortem | 3 | 2 | 4 | **3** |
| ret-confusable-sev1 | 1 | 2 | 2 | 2 |
| ret-confusable-both-present | 3 | 2 | 8 | **5** |
| ret-identifier-pager | 1 | 2 | 1 | **1** |
| ret-txt-vendor | 1 | 1 | 1 | 1 |

Hybrid is better on 1 case, worse on 5 and equal on 6. Cases within max_rank: **vector 9/12, hybrid 7/12**. I
re-ran it with a small EN/PL stopword list as a sensitivity check (not kept in the code) and got the same
picture: identifier case fixed, `ret-en2pl-vacation` 1 → 7, `ret-en2pl-sick-leave` 2 → 6.

**Recommendation: do not adopt plain RRF hybrid.** It fixes the one exact-identifier case but regresses the
cross-lingual and paraphrase cases. BM25 has no PL↔EN term overlap and no Polish stemming, so it adds noise that
fusion with equal weights can't filter out. Hybrid also doesn't help the near-duplicate case, since both runbooks
share the same terms. If identifier lookups matter, a narrower option is worth measuring next: apply a lexical
boost only when the query contains an identifier-like token (`[A-Z]+-\d+`, codes), or use Postgres full-text as a
tie-breaker within the vector top-k. Either can be evaluated by adding cases here first. The lead decides.

## Live mode (`--live`)

Manual only, never in CI. It needs `LLM_PROVIDER` ≠ `fake` and that provider's key set in the environment or
`backend/.env`. The key is only checked for presence through `Settings`, and is never printed or written. It runs
5 fixed cases through the real `ChatService`: 3 grounded questions (EN, PL, EN→PL), the injection document (the
answer must not contain the canary or the injected tool name) and an off-topic question (0 calls). There is a
**hard cap of 5 model calls** (`CallCap`), and the rows are labelled `LIVE`. With no key configured they report
SKIP. They have not been run against a real provider yet, because no key is configured here.
