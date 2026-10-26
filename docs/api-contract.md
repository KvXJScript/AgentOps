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
