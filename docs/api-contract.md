# API contract (v1)

Single source of truth shared by `backend/` and `frontend/`. Change it here first.

Base path: `/api/v1`. All JSON uses `snake_case`. Timestamps are ISO-8601 UTC strings.
Every response carries an `X-Request-ID` header (echoed if the client sent one, generated otherwise).

## Errors

Non-streaming errors always use this envelope:

```json
{ "error": { "code": "not_found", "message": "Conversation not found", "request_id": "…" } }
```

| code                  | HTTP | retryable | meaning                                            |
|-----------------------|------|-----------|----------------------------------------------------|
| `validation_error`    | 422  | no        | request body/params invalid (`details` may list fields) |
| `not_found`           | 404  | no        | resource does not exist                            |
| `llm_not_configured`  | 503  | no        | no provider/credential configured on the server    |
| `llm_auth_failed`     | 502  | no        | provider rejected the credential                   |
| `llm_rate_limited`    | 429  | yes       | provider quota / rate limit hit (`retry_after_s` optional) |
| `llm_timeout`         | 504  | yes       | provider call exceeded the timeout                 |
| `llm_unavailable`     | 502  | yes       | provider outage / 5xx / network error              |
| `cancelled`           | —    | no        | stored on an assistant message when the client disconnected mid-stream (never an HTTP response) |
| `internal_error`      | 500  | no        | unexpected; details only in server logs (also: provider rejected the request, e.g. unknown model) |

`llm_rate_limited` errors (envelope and SSE `error` event) include `retry_after_s: number | null` when the provider sent one.

Messages are safe for end users: never include secrets, stack traces, or raw provider payloads.

## Health

- `GET /api/v1/health` → `200 {"status":"ok"}` (liveness, no dependencies)
- `GET /api/v1/ready` → `200` or `503`
  `{"status":"ready"|"not_ready","checks":{"database":"ok"|"error","llm":"configured"|"not_configured"},"llm":{"provider":"groq","model":"…"}}`
  (`llm` object omitted when not configured; never contains credentials)

## Conversations

```ts
Conversation = { id: string /* uuid */, title: string, created_at: string, updated_at: string }
Message = {
  id: string, conversation_id: string,
  role: "user" | "assistant",
  content: string,
  status: "complete" | "incomplete" | "failed",   // assistant only can be incomplete/failed
  error_code: string | null,                      // one of the error codes above when failed/incomplete
  model: string | null,                           // assistant only
  created_at: string
}
```

- `POST /api/v1/conversations` body `{"title"?: string (1..200)}` → `201 Conversation` (default title "New conversation"; the first user message later sets the title to its first ~60 chars if still default)
- `GET /api/v1/conversations` → `200 {"items": Conversation[]}` newest `updated_at` first, max 100
- `GET /api/v1/conversations/{id}` → `200 Conversation & {"messages": Message[]}` oldest first
- `DELETE /api/v1/conversations/{id}` → `204`

## Sending a message (SSE stream)

`POST /api/v1/conversations/{id}/messages` body `{"content": string}` (trimmed, 1..4000 chars).

Pre-stream failures (validation, not found, `llm_not_configured`) return the normal JSON error envelope with the HTTP status above.
Otherwise → `200`, `Content-Type: text/event-stream`, events in this order:

```
event: message.created
data: {"user_message": Message, "assistant_message_id": "uuid"}

event: token            (0..n times)
data: {"delta": "partial text"}

event: done             (terminal, success)
data: {"message": Message, "usage": {"input_tokens": int|null, "output_tokens": int|null}}

event: error            (terminal, failure — replaces done)
data: {"code": "llm_rate_limited", "message": "…", "retryable": true, "request_id": "…", "message_id": "uuid"}
```

Persistence rules: the user message is stored before streaming starts. The assistant message is stored when the stream ends:
`complete` on success; `incomplete` (partial content kept) if the provider failed after some tokens; `failed` (empty content) if it failed before any token. History sent to the model is the last N complete messages (N configurable).

---

# Phase 2 — Documents and RAG

## New error codes

| code                    | HTTP | meaning                                              |
|-------------------------|------|------------------------------------------------------|
| `unsupported_file_type` | 415  | extension/content not one of md, txt, pdf            |
| `file_too_large`        | 413  | over `MAX_UPLOAD_BYTES` (default 10 MB)              |
| `empty_document`        | 422  | no extractable text after normalization              |
| `corrupt_document`      | 422  | parser failed (e.g. broken/encrypted PDF)            |
| `document_not_ready`    | 409  | operation needs status `ready`                       |
| `embedding_failed`      | 500  | embedder error during ingestion (document → failed; upload still returns 201 with `status: failed`) |
| `retrieval_not_configured` | 503 | no retriever/embedder wired on the server (RAG chat, search, reports) |

## Documents

```ts
Document = {
  id: string, title: string, filename: string,
  media_type: "text/markdown" | "text/plain" | "application/pdf",
  size_bytes: number, checksum_sha256: string,
  status: "pending" | "processing" | "ready" | "failed",
  error_code: string | null,
  chunk_count: number, embedding_model: string | null,
  created_at: string, updated_at: string
}
ChunkLocation = { char_start: number, char_end: number, page: number | null, heading_path: string[] }
Chunk = { id: string, document_id: string, ordinal: number, text: string, location: ChunkLocation }
```

- `POST /api/v1/documents` multipart: `file` (required), `title` (optional, default = filename stem).
  Ingestion is synchronous: validate → extract → normalize → chunk → embed → persist → ready.
  - `201 Document` (status `ready`, or `failed` + `error_code` if a post-validation step failed — the source is kept so it can be retried).
  - Same checksum already `ready` → `200 Document` of the existing one with header `X-Duplicate: true` (no new chunks).
  - Same checksum `failed` → re-runs ingestion on that document (idempotent retry), `200`.
  - Validation failures (415/413/422) → error envelope, nothing persisted.
- `GET /api/v1/documents` → `{"items": Document[]}` newest first
- `GET /api/v1/documents/{id}` → `Document`
- `GET /api/v1/documents/{id}/chunks/{chunk_id}` → `Chunk` (used by citation popovers)
- `POST /api/v1/documents/{id}/retry` → `200 Document` (only from `failed`; else `409 document_not_ready`)
- `DELETE /api/v1/documents/{id}` → `204` (chunks + embeddings removed; existing citations keep their stored excerpt but show the source as deleted)

## Retrieval (inspection)

```ts
RetrievedChunk = {
  chunk_id: string, document_id: string, document_title: string,
  rank: number, score: number,            // cosine similarity, higher = better
  excerpt: string,                        // ≤ 300 chars
  location: ChunkLocation
}
```

- `POST /api/v1/retrieval/search` body `{"query": string (1..1000), "top_k"?: 1..20, "document_ids"?: string[]}` → `{"items": RetrievedChunk[], "embedding_model": string}`
  Scope rule (applied in SQL before anything reaches the model): only `ready`, non-deleted documents embedded with the current `EMBEDDING_MODEL`, intersected with `document_ids` when given (an empty list means no documents; omit the field for all).

## Chat with documents

`POST /api/v1/conversations/{id}/messages` body gains optional fields:
`{"content": string, "use_documents"?: boolean (default false), "document_ids"?: string[]}`

When `use_documents` is true, the stream gains one event right after `message.created`:

```
event: retrieval
data: {"chunks": RetrievedChunk[], "sufficient": boolean}
```

If `sufficient` is false (no chunk above `RETRIEVAL_MIN_SCORE`), the model is NOT called (one `token` event carries the fixed text, `model` is null); the assistant message is a fixed
"not enough evidence in your documents" answer with `status: "complete"`, `grounding: "insufficient_evidence"`.

`Message` gains:
```ts
grounding: "none" | "grounded" | "insufficient_evidence",   // "none" = plain chat
citations: Citation[]
Citation = { marker: string /* "S1" */, chunk_id: string, document_id: string, document_title: string,
             excerpt: string, location: ChunkLocation, document_deleted: boolean }
```

Citation rules: the server labels context chunks `S1..Sn` and keeps the label → chunk mapping server-side. The model may only
reference those labels as `[S1]`. On completion the server parses markers, keeps only labels from this request's mapping
(invented or document-supplied labels are dropped and counted in logs), and stores the resolved citations on the message.
If the model answers but cites nothing valid, `grounding` is still `"grounded"` only if at least one valid citation exists;
otherwise the message is stored with `grounding: "insufficient_evidence"` and the text is kept but flagged in the UI.
`done.message` carries the final citations.

- `GET /api/v1/messages/{id}/retrieval` → `{"chunks": RetrievedChunk[], "cited_chunk_ids": string[], "top_k": number,
  "min_score": number, "embedding_model": string}` (debug/inspection view; excerpts only, never full prompts)

---

# Phase 3 — Report workflow (LangGraph)

```ts
StageName = "validate" | "retrieve" | "analyze" | "draft" | "verify"
Stage = { name: StageName, status: "pending" | "running" | "done" | "skipped" | "failed",
          attempts: number, started_at: string | null, finished_at: string | null,
          detail: string | null }            // short, safe, e.g. "8 chunks retrieved", "1 invalid reference removed"
ReportSection = { heading: string, body: string }   // markdown; may contain [S1] markers
Report = { title: string, summary: string, sections: ReportSection[], references: Citation[] }  // Citation from Phase 2
ReportRun = {
  id: string, question: string, document_ids: string[] | null,
  status: "queued" | "running" | "succeeded" | "insufficient_evidence" | "failed",
  stages: Stage[],                           // always all five, in order
  report: Report | null,                     // set only when succeeded
  error_code: string | null, model: string | null,
  created_at: string, updated_at: string
}
```

- `POST /api/v1/reports` body `{"question": string (10..1000 trimmed), "document_ids"?: string[] (deduplicated)}` → `202 ReportRun` (status `queued`).
  The run executes in-process in the background (no queue); the client polls.
- `GET /api/v1/reports` → `{"items": ReportRun[]}` newest first, max 50 (`report` omitted in list items)
- `GET /api/v1/reports/{id}` → `ReportRun` (UI polls ~1 s while `queued`/`running`)

New error codes stored on runs: `report_timeout` (whole run exceeded `REPORT_TIMEOUT_S`), `interrupted` (server restarted while
the run was active — set at startup), `invalid_model_output` (model output could not be parsed after the bounded retry),
plus the `llm_*` codes. `insufficient_evidence` is a terminal status, not an error.

Reference rule: `report.references` are resolved server-side from the run's retrieved evidence exactly like chat citations;
markers in section bodies that don't resolve are removed from the text and counted in the `verify` stage detail.

---

# Phase 4 — MCP integration (local tracker) and approval boundary

One MCP server, **`local-tracker`**: a small deterministic issue tracker that ships in this repo and runs as a stdio
subprocess of the backend. It is a **local demo server, not a real external service**; the UI and README must say so.
Allowlist (exact names; any other tool the server advertises is ignored and can never be called):

| tool            | access | approval |
|-----------------|--------|----------|
| `list_issues`   | read   | no       |
| `search_issues` | read   | no       |
| `create_issue`  | write  | **always** — only executed by the approval endpoint, never by the model or any other code path |

```ts
Issue = { id: string /* "LT-12" */, title: string, body: string, labels: string[], status: "open" | "closed", created_at: string }
ToolInfo = { name: string, access: "read" | "write", requires_approval: boolean, description: string }
ActionProposal = {
  id: string,
  tool: "create_issue", target: "local-tracker",
  arguments: { title: string /* 5..120 */, body: string /* ≤ 4000 */, labels: ("bug"|"docs"|"follow-up"|"question")[] /* ≤ 3 */ },
  summary: string,                     // one human sentence of what will happen
  payload_hash: string,                // sha256 hex of canonical JSON {tool, target, arguments}
  status: "pending" | "executing" | "executed" | "rejected" | "expired" | "failed",
  source: { type: "report", id: string },
  related_issues: Issue[],             // read via search_issues while drafting; untrusted, display only
  created_at: string, expires_at: string /* +15 min */, decided_at: string | null,
  result: { issue_id: string } | null, error_code: string | null
}
```

- `GET /api/v1/integrations/tracker` → `{"server": {"name": "local-tracker", "label": "Local demo MCP server", "transport": "stdio", "status": "connected" | "unavailable"}, "tools": ToolInfo[]}`
- `GET /api/v1/integrations/tracker/issues?query=&limit=` (query ≤ 200 chars, limit 1..50, default 20) → `{"items": Issue[], "truncated": boolean}` — read-only, via `list_issues`/`search_issues`.
- `POST /api/v1/reports/{id}/proposals` → `201 ActionProposal` (`pending`). Report must be `succeeded` (`409 report_not_ready` otherwise).
  The model drafts title/body/labels from the report (+ related issues as untrusted evidence) into a strict schema; the
  **tool and target are fixed by the server**, never chosen by the model. One `pending` proposal per report: a second call
  returns the existing one with `200`.
- `GET /api/v1/proposals?status=` → `{"items": ActionProposal[]}` newest first · `GET /api/v1/proposals/{id}` → `ActionProposal`
- `POST /api/v1/proposals/{id}/approve` body `{"payload_hash": string}` → `200 ActionProposal` (`executed`, or `failed` + `error_code`).
  Server re-validates arguments, checks expiry and hash, then atomically moves `pending → executing` before calling the tool
  with `idempotency_key = proposal.id`. Approving an already-`executed` proposal with the same hash returns it unchanged (`200`, no second call).
- `POST /api/v1/proposals/{id}/reject` → `200 ActionProposal` (`rejected`; only from `pending`).

New error codes: `mcp_unavailable` (502), `mcp_timeout` (504), `invalid_tool_output` (502), `report_not_ready` (409),
`proposal_not_pending` (409), `payload_mismatch` (409), `proposal_expired` (410). Pending proposals past `expires_at`
read back as `expired`.

Clarifications:
- `payload_hash` = sha256 hex of UTF-8 `json.dumps({"tool", "target", "arguments"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
  The client never recomputes it; it sends back the hash it displayed.
- `POST /reports/{id}/proposals`: `invalid_model_output` → 502; `llm_*` → their v1 statuses.
- Approve of an `executed` proposal with a different hash → `409 payload_mismatch` (no tool call). Approve/reject of an expired
  proposal → `410 proposal_expired`; of any other non-pending one → `409 proposal_not_pending`.
- `?status=expired` includes pending proposals past `expires_at`; `?status=pending` excludes them.
- `summary` is generated server-side from the validated arguments, never written by the model.
