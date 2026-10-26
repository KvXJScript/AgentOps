# Security review (Phase 5)

Scope: a single-user portfolio app meant to run locally. This is a review of the basics, not a claim that the app is
secure enough for production. Results below were checked on 2026-10-26 unless marked otherwise.

## Trust model

- **No user accounts.** Whoever can reach the frontend can use every feature, including approving proposals. This is
  deliberate for a local demo and is the biggest residual risk: **do not expose the app on a public network without
  putting authentication in front of it** (e.g. a reverse proxy with SSO or basic auth).
- The browser only talks to the Next.js server. The Next.js server forwards to the FastAPI backend, which binds to
  `127.0.0.1` by default. Provider keys live only in the backend environment.
- Untrusted inputs: uploaded documents, MCP tool output (issue bodies), model output. None of them can grant permissions:
  there is no tool the model can call. The only write tool (`create_issue`) is reachable only from the approval use case,
  and an AST test fails if any other code references it.

## Checks performed

| Area | Result |
|---|---|
| Secrets in git history | `gitleaks` over all commits: no leaks after allowlisting two test files that contain deliberately fake keys (`.gitleaks.toml`). Runs in CI as the `secrets` job. |
| Secrets in working tree | `gitleaks dir`: 0 findings. `.env` / `.env.*` are gitignored; both `.env.example` files contain placeholders only. |
| Client bundle | `grep` of `.next/static` for the backend URL, `localhost:8000`, `gsk_`, `AIza`, `api_key`: 0 files. No `NEXT_PUBLIC_*` variables, no `dangerouslySetInnerHTML`. |
| Python dependencies | `pip-audit` (production deps from `uv.lock`): no known vulnerabilities. |
| npm dependencies | `npm audit --omit=dev`: 0. Full tree: 2 moderate in `postcss-selector-parser` via `@tailwindcss/typography`, a build-time dependency that never reaches the browser. Left as is and not force-upgraded. |
| CORS | Allowlist from `CORS_ORIGINS`, default `http://localhost:3000`. A preflight from a foreign origin returns 400. Methods: GET/POST/DELETE. |
| CSRF on approve | A cross-site "simple" request (`text/plain`) gets 422 because FastAPI only parses JSON bodies with a JSON content type. Approval also requires the proposal's `payload_hash`, which a foreign page cannot read. |
| Security headers | CSP (`default-src 'self'`, `connect-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP. |
| Input limits | Uploads: md/txt/pdf with content sniffing, 10 MiB read in chunks, page and character caps. Proxy body cap 25 MiB. Chat message 4000 chars, report question 1000, tracker query 200. Model output capped by `LLM_MAX_OUTPUT_TOKENS`. |
| Timeouts | LLM: per-chunk inactivity timeout plus bounded retries in one place (`ResilientChatModel`). Report run: `REPORT_TIMEOUT_S`. MCP: `MCP_TIMEOUT_S` per call, output size cap. |
| Logging | Structured JSON with request and run IDs. A redaction filter covers key, token and secret-like fields and values. Message content, document text, prompts and tool output are not logged; a grep of a live server log for document text found 0 matches. |
| MCP subprocess | Minimal environment (no provider keys, no `DATABASE_URL`). An in-process audit hook blocks network, subprocesses and file access outside its data dir. This is defence in depth, **not** an OS sandbox. |

## Residual risks

1. **No authentication** (see trust model). Anyone who can reach the UI can upload, delete and approve.
2. **No rate limiting.** A client can use up the free-tier model quota. It costs no money, but it can cause denial of service.
3. **CSP needs `'unsafe-inline'` for scripts** so pages can stay statically prerendered. A nonce-based CSP would need dynamic rendering.
4. **Free-tier provider terms apply.** Groq logs requests for up to 30 days. Gemini's free tier may use content for product improvement and cannot be used for apps offered to users in the EEA, UK or Switzerland. Do not upload sensitive documents.
5. **No antivirus scanning of uploads.** PDFs are parsed with `pypdf` in-process. A malicious PDF could still hit a parser bug.
6. **Prompt injection is contained, not prevented.** Injected text cannot gain capabilities, but it can still skew an answer or a drafted proposal. That is why proposals are shown verbatim for human review.
7. **A `create_issue` timeout is recorded as `failed`** even if the issue was created. A retry with the same idempotency key would be safe, but it is not implemented.
8. **The proxy has no upstream timeout** for long streams. A hung backend leaves the request open until the client gives up.

If a real credential is ever committed, treat it as compromised: revoke and rotate it at the provider. Deleting it from
the latest commit is not enough, because the repository is public.
