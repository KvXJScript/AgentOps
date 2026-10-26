# API contract — Phase 2 additions (DRAFT, not yet implemented)

Merged into `docs/api-contract.md` when Phase 2 starts. Same conventions and error envelope as v1.

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
