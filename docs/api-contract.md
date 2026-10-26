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
  `{"status":"ready"|"not_ready","checks":{"database":"ok"|"error","llm":"configured"|"not_configured"},"llm":{"provider":"gemini","model":"…"}}`
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
| `embedding_failed`      | 500  | embedder error during ingestion (document → failed)  |

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
- `POST /api/v1/documents/{id}/retry` → `200 Document` (only from `failed`; else `409 document_not_ready`-style conflict)
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
  Scope rule (applied in SQL before anything reaches the model): only `ready`, non-deleted documents, intersected with `document_ids` when given.

## Chat with documents

`POST /api/v1/conversations/{id}/messages` body gains optional fields:
`{"content": string, "use_documents"?: boolean (default false), "document_ids"?: string[]}`

When `use_documents` is true, the stream gains one event right after `message.created`:

```
event: retrieval
data: {"chunks": RetrievedChunk[], "sufficient": boolean}
```

If `sufficient` is false (no chunk above `RETRIEVAL_MIN_SCORE`), the model is NOT called; the assistant message is a fixed
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

- `POST /api/v1/reports` body `{"question": string (10..1000 trimmed), "document_ids"?: string[]}` → `202 ReportRun` (status `queued`).
  The run executes in-process in the background (no queue); the client polls.
- `GET /api/v1/reports` → `{"items": ReportRun[]}` newest first, max 50 (`report` omitted in list items)
- `GET /api/v1/reports/{id}` → `ReportRun` (UI polls ~1 s while `queued`/`running`)

New error codes stored on runs: `report_timeout` (whole run exceeded `REPORT_TIMEOUT_S`), `interrupted` (server restarted while
the run was active — set at startup), `invalid_model_output` (model output could not be parsed after the bounded retry),
plus the `llm_*` codes. `insufficient_evidence` is a terminal status, not an error.

Reference rule: `report.references` are resolved server-side from the run's retrieved evidence exactly like chat citations;
markers in section bodies that don't resolve are removed from the text and counted in the `verify` stage detail.
