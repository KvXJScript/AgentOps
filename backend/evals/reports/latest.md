# Eval report — agentops-regression v1.0.0

- Generated: 2026-10-26 12:05:10 CET (took 0.8 s)
- Embedding model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`; retrieval = exact in-memory cosine over the real chunker + fastembed (same scoring as `PgVectorRetriever`, no HNSW); top_k=6, min_score=0.35
- Corpus: 5 documents, 20 chunks; model calls are scripted
- **35 pass, 0 fail, 4 xfail, 0 xpass, 0 skip** (xfail = known weakness, see `known_issue`)

| status | category | case | key numbers | reason |
|---|---|---|---|---|
| PASS | retrieval | `ret-en-equipment` | rank=1 score=0.695 top_score=0.695 max_rank=1 | rank 1, score 0.695 |
| XFAIL | retrieval | `ret-en-core-hours` | rank=3 score=0.47 top_score=0.621 max_rank=2 | rank 3 > max_rank 2 (top: remote-work-policy.md#Eligibility) — known: paraphrase without 'core hours' ranks Eligibility/Security first; still in top_k and above min_score |
| PASS | retrieval | `ret-pl-vacation-days` | rank=1 score=0.806 top_score=0.806 max_rank=1 | rank 1, score 0.806 |
| PASS | retrieval | `ret-pl-wedding-leave` | rank=1 score=0.647 top_score=0.647 max_rank=1 | rank 1, score 0.647 |
| PASS | retrieval | `ret-en2pl-vacation` | rank=1 score=0.616 top_score=0.616 max_rank=2 | rank 1, score 0.616 |
| PASS | retrieval | `ret-en2pl-sick-leave` | rank=2 score=0.564 top_score=0.582 max_rank=3 | rank 2, score 0.564 |
| PASS | retrieval | `ret-pl2en-vpn` | rank=1 score=0.592 top_score=0.592 max_rank=1 | rank 1, score 0.592 |
| PASS | retrieval | `ret-pl2en-postmortem` | rank=2 score=0.525 top_score=0.574 max_rank=3 | rank 2, score 0.525 |
| XFAIL | retrieval | `ret-confusable-sev1` | rank=2 score=0.756 top_score=0.78 max_rank=1 | rank 2 > max_rank 1 (top: incident-runbook-legacy.md#Response times) — known: the deprecated v1 runbook outranks v3 even when the query says 'current v3'; both reach the model |
| PASS | retrieval | `ret-confusable-both-present` | rank=2 score=0.7 top_score=0.733 max_rank=3 | rank 2, score 0.700 |
| XFAIL | retrieval | `ret-identifier-pager` | rank=2 score=0.395 top_score=0.446 max_rank=1 | rank 2 > max_rank 1 (top: urlopy-i-nieobecnosci.md#Zwolnienie lekarskie) — known: exact identifiers are weak for dense retrieval (rank 2 behind an unrelated chunk); BM25/hybrid ranks it 1 |
| PASS | retrieval | `ret-txt-vendor` | rank=1 score=0.814 top_score=0.814 max_rank=1 | rank 1, score 0.814 |
| PASS | retrieval | `ret-neg-capital` | top_score=0.118 | top score 0.118 < 0.35 |
| PASS | retrieval | `ret-neg-pl-cheesecake` | top_score=0.159 | top score 0.159 < 0.35 |
| PASS | retrieval | `ret-neg-football` | top_score=0.062 | top score 0.062 < 0.35 |
| XFAIL | retrieval | `ret-neg-adjacent-parental` | top_score=0.411 | off-topic top score 0.411 >= min_score 0.35 — known: adjacent HR topic scores above 0.35 against the leave document, so the model is called and must decline |
| PASS | citations | `cit-single-valid` | cited=['S1'] evidence=1 | cited ['S1']; removed none |
| PASS | citations | `cit-mixed-invented` | cited=['S1', 'S2'] evidence=6 | cited ['S1', 'S2']; removed ['S7', 'S99'] |
| PASS | citations | `cit-only-invented` | evidence=1 | cited none; removed ['S42', 'S0'] |
| PASS | citations | `cit-dedupe-order` | cited=['S2', 'S1'] evidence=2 | cited ['S2', 'S1']; removed none |
| PASS | abstention | `abs-off-topic-en` | top_score=0.118 model_calls=0 | insufficient_evidence, model not called |
| PASS | abstention | `abs-off-topic-pl` | top_score=0.159 model_calls=0 | insufficient_evidence, model not called |
| PASS | abstention | `abs-empty-scope` | model_calls=0 | insufficient_evidence, model not called |
| PASS | injection | `inj-delimiters-neutralised` | evidence=2 | 2 blocks, payload contained, forged tags neutralised |
| PASS | injection | `inj-forged-label-not-citable` | cited=['S1'] | document-supplied [S9] dropped, real [S1] kept |
| PASS | injection | `inj-title-attribute-escaped` |  | quotes/angle brackets in the title are escaped |
| PASS | injection | `inj-report-forged-finding` | model_calls=1 | finding citing forged S9 dropped; no draft |
| PASS | injection | `inj-tracker-issue-text` |  | issue text stays in block I1; forged R1 neutralised |
| PASS | injection | `inj-proposal-tool-override` | model_calls=2 | tool/target keys and unknown labels rejected; tool fixed by server |
| PASS | workflow | `wf-happy-path` | model_calls=2 refs=['S1', 'S2'] | 5 stages, refs S1,S2 valid, [S12] stripped |
| PASS | workflow | `wf-insufficient-evidence` | model_calls=0 | stopped after retrieve: No relevant evidence (8 chunks below the relevance threshold) |
| PASS | workflow | `wf-invalid-json-retry` | model_calls=3 analyze_attempts=2 | 1 bad reply → corrective retry → report |
| PASS | workflow | `wf-invalid-json-bounded` | model_calls=2 | InvalidModelOutput after 2 attempts |
| PASS | workflow | `wf-only-invented-refs` |  | draft with only invented refs withheld |
| PASS | tool_policy | `tool-unknown-not-callable` |  | unknown tool / wrong access refused before connecting |
| PASS | tool_policy | `tool-not-allowlisted` |  | create_issue refused when not allowlisted |
| PASS | tool_policy | `tool-allowlist-cannot-widen` |  | allowlist can't add unknown tools; create_issue = write + approval |
| PASS | tool_policy | `tool-drafting-has-no-writer` |  | drafting gets only IssueReader; reader has no create_issue |
| PASS | tool_policy | `tool-create-needs-approval` |  | 0 calls without valid approval; exactly 1 after approval (repeat is a no-op) |

## Experiment: vector vs hybrid (vector + BM25, reciprocal-rank fusion k=60)

Rank of the expected chunk (document + heading); `–` = not ranked (BM25: no term overlap).

| case | max_rank | vector | bm25 | hybrid | change |
|---|---|---|---|---|---|
| `ret-en-equipment` | 1 | 1 | 1 | 1 | = |
| `ret-en-core-hours` | 2 | 3 | 12 | 7 | worse |
| `ret-pl-vacation-days` | 1 | 1 | 1 | 1 | = |
| `ret-pl-wedding-leave` | 1 | 1 | 1 | 1 | = |
| `ret-en2pl-vacation` | 2 | 1 | 6 | 3 | worse |
| `ret-en2pl-sick-leave` | 3 | 2 | – | 15 | worse |
| `ret-pl2en-vpn` | 1 | 1 | 1 | 1 | = |
| `ret-pl2en-postmortem` | 3 | 2 | 4 | 3 | worse |
| `ret-confusable-sev1` | 1 | 2 | 2 | 2 | = |
| `ret-confusable-both-present` | 3 | 2 | 8 | 5 | worse |
| `ret-identifier-pager` | 1 | 2 | 1 | 1 | better |
| `ret-txt-vendor` | 1 | 1 | 1 | 1 | = |

Hybrid better on 1, worse on 5, equal on 6 cases; within max_rank: vector 9/12, hybrid 7/12.
