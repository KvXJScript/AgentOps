# AgentOps

**Ask questions about your own documents and get answers you can check. AgentOps turns those answers into a cited
report and a proposed follow-up action, and nothing is written anywhere until a person approves the exact payload.**

A full-stack AI portfolio project: retrieval-augmented generation with citations resolved on the server, a bounded
LangGraph workflow, and an MCP tool integration behind a human approval step. It runs entirely on free model tiers
and local embeddings.

> **No hosted demo.** The recording and screenshots below are the main way to review the UI. They were captured in
> a real browser from the running app (FastAPI, Postgres/pgvector, local embeddings, the MCP subprocess and the live
> `openai/gpt-oss-120b` model on Groq), using synthetic documents. Nothing in them is mocked.

<p align="center">
  <img src="docs/screenshots/demo.gif" alt="44-second recording: upload documents, ask with citations, run a LangGraph report, propose a follow-up issue, review the grounding check and approve it" width="100%">
  <br><em>Real-time recording (44 s): upload → cited answer → report workflow → proposal with grounding check → approval → tracker.</em>
</p>

### Review in 5 minutes

1. **Watch the recording above.** The whole flow runs on the live model.
2. **Citations you can trust:** [ADR 0003](docs/adr/0003-server-side-citation-resolution.md) explains why the model
   never decides which source a citation points to.
3. **No side effect without a human:** [ADR 0005](docs/adr/0005-approval-bound-to-payload-hash-and-writer-isolation.md)
   covers why only the approval endpoint can write.
4. **Measured, not guessed:** the [eval suite](docs/evaluation.md) has per-case results, a rejected hybrid-search
   experiment and an [archived live run](backend/evals/reports/live-2026-10-26.md).
5. **Honest limits:** [Known limitations](#known-limitations) below, and [`docs/security.md`](docs/security.md).

<p align="center">
  <img src="docs/screenshots/rag-answer-with-citations.png" alt="Chat answer grounded in uploaded documents, with numbered citation chips and a sources row" width="100%">
  <br><em>An answer grounded in uploaded documents. Each numbered chip resolves on the server to a real chunk.</em>
</p>

| | |
|---|---|
| ![Retrieval inspector showing ranked chunks with similarity scores, cited vs not cited, including an ignored prompt-injection chunk](docs/screenshots/retrieval-inspector.png) | ![Report page with the five-stage LangGraph timeline and a cited report](docs/screenshots/report-workflow.png) |
| *Retrieval inspector: rank, cosine score, cited or not. Chunk #6 holds a prompt-injection payload; it was retrieved but ignored and never cited.* | *Report workflow: validate → retrieve → analyze → draft → verify, with per-stage timing and verified references.* |
| ![Approval review screen listing the exact tool, target, arguments and payload hash](docs/screenshots/approval-review.png) | ![Grounding check panel: 4 claims checked, 2 supported, 2 flagged as weak matches with their cited sources](docs/screenshots/approval-grounding-check.png) |
| *Approval review: the model drafted a `create_issue` call. Tool and target are fixed by the server; nothing has run.* | *Grounding check on the drafted issue: claims whose citation doesn't back them are flagged for the reviewer.* |

![Tracker page showing the local demo MCP server, its allowlisted tools and issues rendered as untrusted plain text](docs/screenshots/tracker-mcp.png)
*The local demo MCP server (clearly labelled), its three allowlisted tools, and issues shown as untrusted tool output.
LT-6 is a seeded prompt-injection fixture.*

## What it does

1. **Upload** Markdown, text or PDF. Ingestion validates, extracts, chunks and embeds locally, then marks the document
   ready. Re-uploading the same file is detected by checksum.
2. **Ask with "Use my documents".** Relevant chunks are retrieved and labelled `S1..Sn` on the server. The model may
   only cite those labels, and invented or document-supplied markers are dropped. If nothing relevant is found, the app
   says so **without calling the model**.
3. **Write a report.** A five-node LangGraph workflow extracts findings and drafts sections, then verifies every
   reference against the evidence it retrieved. If no valid reference survives, it reports insufficient evidence
   instead of returning an ungrounded report.
4. **Propose a follow-up.** From a finished report, the model drafts a `create_issue` call for the local tracker. The
   proposal is stored as structured data with a SHA-256 payload hash and a 15-minute expiry.
5. **Approve or reject.** Only the approval endpoint can execute the write tool. It re-validates the arguments,
   compares the hash, atomically claims the proposal, and calls the tool with an idempotency key. Approving twice
   creates one issue.

## Architecture

Detailed diagrams, module responsibilities and failure boundaries are in
[`docs/architecture.md`](docs/architecture.md). The design decisions are recorded in [`docs/adr/`](docs/adr/).

```mermaid
flowchart LR
  subgraph Browser
    UI[Next.js pages]
  end
  subgraph Next[Next.js server]
    Proxy[API proxy route<br/>no secrets in the browser]
  end
  subgraph Backend[FastAPI backend]
    API[api: routes, validation, SSE]
    App[application: chat, RAG, ingestion,<br/>LangGraph reports, proposals, approvals]
    Ports[domain ports]
    Infra[infrastructure adapters]
  end
  PG[(Postgres + pgvector)]
  Emb[fastembed<br/>local multilingual model]
  LLM[Groq or Gemini<br/>HTTPS]
  MCP[local-tracker<br/>MCP stdio subprocess]

  UI --> Proxy --> API --> App --> Ports
  Infra -. implements .-> Ports
  Infra --> PG
  Infra --> Emb
  Infra --> LLM
  Infra --> MCP
```

**RAG request**

```mermaid
sequenceDiagram
  participant U as Browser
  participant B as Backend
  participant V as pgvector
  participant M as LLM
  U->>B: question + use_documents
  B->>V: embed locally, cosine search (ready docs, scope)
  V-->>B: ranked chunks
  alt no chunk above RETRIEVAL_MIN_SCORE
    B-->>U: insufficient evidence (model not called)
  else evidence found
    B->>B: label S1..Sn, wrap as untrusted evidence
    B->>M: stream with grounded system prompt
    M-->>U: tokens (SSE)
    B->>B: resolve [Sn] against included evidence, drop invalid markers
    B-->>U: done + citations (stored with a retrieval trace)
  end
```

**Report → proposal → approval**

```mermaid
sequenceDiagram
  participant U as Browser
  participant G as LangGraph workflow
  participant P as Proposal service
  participant A as Approval service
  participant T as local-tracker (MCP)
  U->>G: start report (202, then poll)
  G->>G: validate, retrieve, analyze, draft, verify
  G-->>U: report with verified references
  U->>P: propose follow-up
  P->>T: search_issues (read-only)
  P->>P: model drafts title/body/labels, server fixes tool + target + hash + expiry
  P-->>U: pending proposal
  U->>A: approve with payload_hash
  A->>A: re-validate, compare hash, atomic pending to executing
  A->>T: create_issue (idempotency key = proposal id)
  T-->>U: issue created once
```

## Key technical decisions

- **Ports and adapters with one resilience layer.** Providers sit behind small `Protocol` ports. Timeouts and bounded
  retries (exponential backoff with jitter, `Retry-After` honoured, no retry after the first streamed token) live only
  in `ResilientChatModel`, so retries never multiply. [ADR 0001](docs/adr/0001-ports-and-adapters-with-one-resilience-layer.md)
- **Citations resolved on the server, not trusted from the model.** The label-to-chunk mapping never leaves the
  backend, and citations are snapshotted so they survive deletion of the source document.
  [ADR 0003](docs/adr/0003-server-side-citation-resolution.md)
- **One datastore.** Postgres holds relational data and vectors (pgvector HNSW). There is no separate vector DB, Redis
  or queue. [ADR 0002](docs/adr/0002-postgres-and-pgvector-as-the-only-store.md),
  [ADR 0006](docs/adr/0006-in-process-background-runs-without-a-queue.md)
- **Measured retrieval settings.** Chunks are 500 characters because the embedding model truncates at 128 tokens.
  `RETRIEVAL_MIN_SCORE=0.35` was taken from measured score distributions. A hybrid BM25 experiment was rejected
  because it made 5 eval cases worse and only 1 better. Two narrower ranking adjustments were adopted instead: an
  identifier boost that applies only when the query contains a token like `PAGER-7731`, and a penalty for documents
  marked `status: deprecated` in front matter. They fixed 2 xfails with 0 regressions and leave the reported score
  unchanged. [ADR 0004](docs/adr/0004-local-multilingual-embeddings-and-measured-chunk-size.md),
  [ADR 0007](docs/adr/0007-identifier-boost-and-deprecated-penalty.md), [`docs/evaluation.md`](docs/evaluation.md)
- **Approval as a structural boundary.** The write port (`IssueWriter`) is given only to `ApprovalService`. An AST
  test fails if any other code references it. [ADR 0005](docs/adr/0005-approval-bound-to-payload-hash-and-writer-isolation.md)
- **Grounding check on drafted proposals.** A live run produced an unsupported claim in a drafted issue. That case is
  now an eval, and a deterministic check flags unsupported claims and unverified numbers for the reviewer. The check
  is advisory and outside the payload hash. [ADR 0008](docs/adr/0008-deterministic-grounding-check-for-proposals.md)

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript strict, Tailwind v4, shadcn/ui, Vitest |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic, structlog, pytest, ruff, mypy strict |
| AI | LangGraph, MCP Python SDK 2.x, OpenAI-compatible client (Groq), `google-genai` (Gemini), fastembed |
| Data | PostgreSQL 17 + pgvector (Docker Compose) |
| CI | GitHub Actions: gitleaks history scan, lint, types, unit, integration (real Postgres), evals, frontend build |

## Models, cost and free-tier notes

| Purpose | Default | Notes |
|---|---|---|
| Chat | Groq `openai/gpt-oss-120b` | Free tier, key from console.groq.com. Verified working on 2026-10-26. `llama-3.3-70b-versatile`, the originally planned default, returned `model_not_found` for this account. |
| Chat (optional) | Gemini `gemini-3.8-flash` via `LLM_PROVIDER=gemini` | The Gemini API terms only allow **paid** services when an app is offered to users in the EEA, UK or Switzerland. On the free tier, content may be used to improve Google products. It is therefore not the default. |
| Embeddings | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dims) via fastembed | Runs locally: free, offline and without quotas. It is downloaded once (about 220 MB). |
| Dev stub | `LLM_PROVIDER=fake` | An explicit echo model for UI work without a key, labelled "Demo stub model" in the UI. |

Not verified: whether Groq's free plan ever requires a payment method (the docs only mention one for upgrading), and
exact free-tier rate limits for your account (check the provider console). **Provider models, limits and terms change
over time. Re-check them before relying on any of this.**

## Run locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node.js 20.

```bash
docker compose up -d db                       # Postgres 17 + pgvector on :5433
cp .env.example backend/.env                  # then set GROQ_API_KEY (or LLM_PROVIDER=fake)
cd backend && uv sync && make migrate && make dev     # API on :8000
cd frontend && npm install && npm run dev             # UI on :3000
```

Environment variables (names only; see `.env.example` for defaults and comments):

- **Backend:** `DATABASE_URL`, `LLM_PROVIDER`, `LLM_MODEL`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `LLM_TIMEOUT_S`,
  `LLM_MAX_RETRIES`, `LLM_MAX_OUTPUT_TOKENS`, `CHAT_HISTORY_MESSAGES`, `MAX_UPLOAD_BYTES`, `MAX_PDF_PAGES`,
  `MAX_DOCUMENT_CHARS`, `CHUNK_MAX_CHARS`, `CHUNK_OVERLAP_CHARS`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`,
  `EMBEDDING_CACHE_DIR`, `EMBEDDING_BATCH_SIZE`, `RAG_TOP_K`, `RETRIEVAL_MIN_SCORE`,
  `RETRIEVAL_IDENTIFIER_BOOST`, `RETRIEVAL_DEPRECATED_PENALTY`, `RAG_CONTEXT_MAX_CHARS`,
  `REPORT_TIMEOUT_S`, `REPORT_TOP_K`, `REPORT_MAX_EVIDENCE_CHARS`, `REPORT_MAX_CONCURRENT_RUNS`,
  `MCP_TRACKER_ENABLED`, `MCP_TRACKER_DATA_DIR`, `MCP_TRACKER_ALLOWED_TOOLS`, `MCP_TIMEOUT_S`,
  `MCP_CONNECT_TIMEOUT_S`, `MCP_MAX_OUTPUT_BYTES`, `PROPOSAL_TTL_S`, `LOG_LEVEL`, `CORS_ORIGINS`
- **Frontend (server-only):** `BACKEND_URL`

Each provider reads only its own key variable; there is no cross-provider fallback. Keys are only ever read by the
backend.

## Tests and evaluation

```bash
cd backend
make check                          # ruff, mypy strict, unit tests (no Docker, no keys)
REQUIRE_DB=1 make test-integration  # needs `docker compose up -d db`
make eval                           # versioned eval suite, writes evals/reports/latest.md

cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

Last local run (2026-10-26): **447 backend unit tests passed** (1 opt-in test skipped), **40 integration tests
passed**, **198 frontend tests passed**, production build OK.

The eval suite (v1.3.0, 47 cases) covers retrieval, citations, abstention, prompt injection, the report workflow,
tool/approval policy and proposal grounding. Result: **45 pass, 2 xfail**. The xfails are documented retrieval
weaknesses, such as a paraphrase ranking the right chunk 3rd instead of 2nd. Results are reported per case; there
is no single "accuracy" score. CI runs the suite with scripted models.

**Live model run.** `python -m evals --live` runs at most 5 calls against the configured provider, manually and never
in CI. The first run failed 4 of 5 cases: `gpt-oss-120b` sometimes writes citations as `【S1】`, not `[S1]`, so
correct answers were stored as "insufficient evidence". Marker variants are now normalised and guarded by a
regression case. The re-run passed 5/5, including PL↔EN citations and the injection case; it is
[archived here](backend/evals/reports/live-2026-10-26.md).

## Security

Full review: [`docs/security.md`](docs/security.md). Highlights:

- Provider keys stay on the backend. The browser talks only to the Next.js server. There are no `NEXT_PUBLIC_*`
  secrets, and a scan of the client bundle found no keys.
- Uploaded documents and tool output are treated as untrusted data. Evidence is wrapped in delimiters that document
  text cannot close. The model has no tools it can call itself.
- The MCP client uses an exact-name tool allowlist (a decoy `delete_all_issues` tool is never exposed), validates
  inputs, applies timeouts and caps output size. The subprocess gets a minimal environment.
- Structured logs carry request and run IDs and redact secrets. Document text, prompts and tool output are not
  logged.
- CI runs gitleaks over the full history. Dependency audits: no known Python vulnerabilities. In npm, 2 moderate
  issues in a build-time-only package.

## Real vs mocked

| Component | Status |
|---|---|
| LLM (Groq) | **Real.** Used for the recording, screenshots, a smoke test and the capped live eval. Automated tests and CI use fakes. |
| Embeddings, Postgres/pgvector | **Real**, locally. |
| MCP server `local-tracker` | **Real MCP over stdio, but a local demo server** shipped in this repo, not an external service. Issues live in a local JSON file. |
| Gemini adapter | Implemented and covered by mocked tests. Not exercised against the live API. |

## Known limitations

- **No authentication or rate limiting.** It is designed as a single-user local app; do not expose it publicly
  without putting auth in front.
- **Proposal grounding is a heuristic, not fact-checking.** A live run drafted "SEV2 follows a timed acknowledgment
  and triage", which the runbook doesn't say. Drafting now has to cite the report's excerpts as `[Sn]`, and a
  deterministic check flags every claim without a valid marker, with a number missing from what it cites, or with
  few words in common with it. The reviewer sees these flags and the approve dialog counts them
  ([ADR 0008](docs/adr/0008-deterministic-grounding-check-for-proposals.md)). The check still misses a wrong claim
  that reuses the excerpt's words or a number cited for the wrong entity, and it can flag a fair paraphrase.
  It only flags. The human decides.
- **A faithful citation to a deprecated source passes the grounding check.** In the recording, the drafted issue
  quoted the deprecated v1 runbook's "30 minutes / four hours" with a valid `[S5]`. The claim matches its source,
  but that source is outdated (v3 says 15 minutes / one hour). The check doesn't yet use the document's
  `deprecated` flag. Flagging claims that cite deprecated documents is the next fix.
- **Known retrieval weaknesses** (see the eval xfails): a paraphrase can rank the right chunk 3rd instead of 2nd,
  and an adjacent off-topic question ("parental leave in Germany", 0.411) clears `RETRIEVAL_MIN_SCORE`, so the
  model is called and has to decline. Stale versions are demoted only if their front matter says
  `status: deprecated`. An unmarked old copy can still outrank the current one.
- **Background runs live in-process.** Runs interrupted by a restart are marked `interrupted`, not resumed.
- **The CSP still allows `'unsafe-inline'` scripts** so pages can stay statically prerendered.

## Docs

[`docs/architecture.md`](docs/architecture.md) · [`docs/adr/`](docs/adr/) · [`docs/api-contract.md`](docs/api-contract.md) ·
[`docs/evaluation.md`](docs/evaluation.md) · [`docs/security.md`](docs/security.md) ·
[`docs/implementation-status.md`](docs/implementation-status.md)
