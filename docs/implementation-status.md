# Implementation status

## Backend

**Phase 1: chat with SSE streaming, persisted history and a replaceable LLM provider. Done.**

Implements `docs/api-contract.md` v1 in full. Layers: `api → application → domain ← infrastructure`
(`backend/src/agentops/`). Timeouts and retries are applied in exactly one place,
`infrastructure/llm/resilience.py` (`ResilientChatModel`). It wraps every adapter and uses an inactivity timeout
for the first and each later chunk, bounded exponential backoff with jitter, and honours Retry-After up to 10 s.
It never retries auth or request errors, and never retries once a token has been streamed. SDK-internal retries
are switched off (`max_retries=0` for openai, `HttpRetryOptions(attempts=1)` for google-genai).

Providers (`LLM_PROVIDER`, blank `LLM_MODEL` = provider default):

| provider | SDK | key var | default model |
|---|---|---|---|
| `groq` (default) | `openai` against `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| `gemini` | `google-genai` (`aio.models.generate_content_stream`) | `GEMINI_API_KEY` | `gemini-3.8-flash` |
| `fake` | built-in stub, explicit only | none | `fake-echo` |

**Why Groq is the default:** the Gemini API terms allow only Paid Services when an API client is offered to users
in the EEA, Switzerland or the UK. On the free tier, submitted content may also be used to improve products and
may be reviewed by humans. For a publicly demoable app that is the wrong default. Groq's free tier has neither
restriction for this use, and its OpenAI-compatible API lets the same adapter target other compatible hosts.
Gemini is still available by setting `LLM_PROVIDER=gemini`.

A missing key for the selected provider does not stop the app from starting. `/ready` then reports
`llm: not_configured` and the message endpoint returns `503 llm_not_configured`. There is no fallback to another
provider or to the fake.

Fake stub scenarios for UI work: send `!fake:auth`, `!fake:rate_limited`, `!fake:timeout`, `!fake:unavailable`
or `!fake:midstream` (fails after two tokens, giving an `incomplete` message).

Commands (run from `backend/`): `make lint`, `make typecheck`, `make test` (unit, no Docker/keys),
`make test-integration` (needs `docker compose up -d db`; uses a separate `agentops_test` database),
`make migrate`, `make dev` (uvicorn reload on :8000).

Notes / open points:
- Contract gap: an assistant reply cut short by a **client disconnect** is stored as `incomplete` (or `failed` if
  no tokens arrived) with `error_code: null`, because no contract code fits. Proposal: add `cancelled`.
- Contract gap: provider 4xx errors other than auth or rate limits (for example an unknown model) surface as
  `internal_error`. They are server misconfiguration and not retryable.
- Additive: SSE `error` events and the JSON envelope carry `retry_after_s` when the provider supplied it.
- `DATABASE_URL` must be PostgreSQL and is checked at startup. Process env overrides `backend/.env`.
- Messages also store `input_tokens`/`output_tokens` (not exposed in the API) for later cost tracking.
- Embeddings are not part of this phase. Planned: local fastembed `paraphrase-multilingual-MiniLM-L12-v2`
  (384 dims). The `vector` extension is already enabled by migration 0001.

## Frontend

**Phase 1: chat UI over the v1 contract. Done** (verified against the dev mock backend; not yet against the real backend).

Stack: Next.js 16.4 (App Router, Turbopack), React 19, TypeScript strict, Tailwind v4, shadcn/ui (radix-nova),
react-markdown + remark-gfm, next-themes, Vitest 4. Node ≥ 20.9 (ESLint 9 / Vitest 4 chosen for Node 20.18).

- **Proxy**: `frontend/src/app/api/v1/[...path]/route.ts` → `src/lib/server/proxy.ts`. Forwards to server-only
  `BACKEND_URL` (default `http://localhost:8000`): method, body, query, `Content-Type`, `Accept`, `X-Request-ID`
  (generated if absent), status. No cookies/auth headers are forwarded. The upstream body stream is returned as is;
  SSE responses get `Cache-Control: no-cache, no-transform` (which makes `next start`'s gzip skip them) and
  `X-Accel-Buffering: no`. Client aborts are passed upstream via `request.signal`. Unreachable backend → `502`
  envelope with frontend-only code `backend_unreachable`.
- **Client**: `src/lib/api/` — contract types, `ApiClient`, SSE parser (`sse.ts`, WHATWG-style: CR/LF/CRLF, split
  chunks, multi-line data, UTF-8 split across chunks, abort cancels the reader), error→copy mapping (`errors.ts`).
  `retry_after_s` is read from the envelope, the SSE `error` event, or the `Retry-After` header.
- **UI**: sidebar (conversations + disabled Documents/Reports/Approvals slots, status popover from `/ready` with
  "Demo stub model" badge for `fake*`, theme switch), chat with streaming markdown (raw HTML skipped, images not
  loaded), Stop (AbortController), stick-to-bottom scroll, 4000-char composer, error callouts with retry and copyable
  `request_id`, persisted `incomplete`/`failed`/`cancelled` messages marked. Conversation creation is lazy (on first send).
- **Dev mock** (not in the app build): `npm run mock:backend` — in-memory contract mock with `/rate`, `/fail`,
  `/timeout`, `/noconf`, `/long` triggers.

Commands (from `frontend/`): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.

Open points / contract notes:
- `backend_unreachable` (502, proxy-generated) is not in the contract's code table. Proposal: list it as a
  gateway-only code.
- Retry after an in-stream error re-sends the text as a new user message (the first one is already persisted);
  there is no "regenerate" endpoint. Fine for Phase 1, worth one later.
- After Stop the UI shows a local "Stopped by you" message; the backend's persisted version (`cancelled`) appears
  on the next load of the conversation.
