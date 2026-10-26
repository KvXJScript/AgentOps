import type { ErrorCode } from "./types";

const RETRYABLE: ReadonlySet<string> = new Set([
  "mcp_unavailable",
  "mcp_timeout",
  "invalid_tool_output",
  "llm_rate_limited",
  "llm_timeout",
  "llm_unavailable",
  "backend_unreachable",
  "network_error",
  "stream_interrupted",
]);

export function isRetryableCode(code: string): boolean {
  return RETRYABLE.has(code);
}

export interface ApiErrorInit {
  code: ErrorCode | string;
  message: string;
  status?: number | null;
  requestId?: string | null;
  retryable?: boolean;
  retryAfterS?: number | null;
  details?: unknown;
}

/** Normalised error for anything that goes wrong talking to the API. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly retryAfterS: number | null;
  readonly details: unknown;

  constructor(init: ApiErrorInit) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code;
    this.status = init.status ?? null;
    this.requestId = init.requestId ?? null;
    this.retryable = init.retryable ?? isRetryableCode(init.code);
    this.retryAfterS = init.retryAfterS ?? null;
    this.details = init.details;
  }
}

export interface ErrorDescription {
  title: string;
  description: string;
  retryable: boolean;
}

/** Human, end-user wording for each error code. */
export function describeError(err: {
  code: string;
  retryable?: boolean;
  retryAfterS?: number | null;
  message?: string;
}): ErrorDescription {
  const retryable = err.retryable ?? isRetryableCode(err.code);
  switch (err.code) {
    case "llm_not_configured":
      return {
        title: "No model is configured on the server",
        description:
          "The backend has no language model provider or credential set up. Configure one and reload.",
        retryable: false,
      };
    case "llm_auth_failed":
      return {
        title: "The model provider rejected the server's credential",
        description: "Check the API key configured on the backend.",
        retryable: false,
      };
    case "llm_rate_limited": {
      const s = err.retryAfterS != null ? Math.max(1, Math.ceil(err.retryAfterS)) : null;
      return {
        title:
          s != null
            ? `Free-tier rate limit reached, try again in ${s}s`
            : "Free-tier rate limit reached, try again shortly",
        description: "The model provider is throttling requests.",
        retryable: true,
      };
    }
    case "llm_timeout":
      return {
        title: "The model took too long to respond",
        description: "The request timed out. Trying again usually helps.",
        retryable: true,
      };
    case "llm_unavailable":
      return {
        title: "The model provider is unavailable",
        description: "The provider returned an error or could not be reached.",
        retryable: true,
      };
    case "validation_error":
      return {
        title: "The request was not accepted",
        description: err.message || "Some input was invalid.",
        retryable: false,
      };
    case "not_found":
      return {
        title: "Not found",
        description: err.message || "This conversation no longer exists.",
        retryable: false,
      };
    case "backend_unreachable":
      return {
        title: "Can't reach the AgentOps backend",
        description: "The API server is not responding. Is it running?",
        retryable: true,
      };
    case "network_error":
      return {
        title: "Network error",
        description: "Check your connection and try again.",
        retryable: true,
      };
    case "stream_interrupted":
      return {
        title: "The response was interrupted",
        description: "The connection closed before the answer finished.",
        retryable: true,
      };
    case "cancelled":
      return {
        title: "Stopped before the response finished",
        description: "The response was cancelled before it completed.",
        retryable: false,
      };
    // Phase 2 — documents and RAG
    case "unsupported_file_type":
      return {
        title: "This file type isn't supported",
        description: "Upload Markdown (.md), plain text (.txt) or PDF files.",
        retryable: false,
      };
    case "file_too_large":
      return {
        title: "This file is too large",
        description: "Files can be up to 10 MB. Split large documents and upload the parts.",
        retryable: false,
      };
    case "empty_document":
      return {
        title: "No readable text in this file",
        description: "The file is empty or contains only images. Scanned PDFs need OCR before upload.",
        retryable: false,
      };
    case "corrupt_document":
      return {
        title: "This file couldn't be read",
        description: "It may be damaged or password-protected. Export it again and re-upload.",
        retryable: false,
      };
    case "document_not_ready":
      return {
        title: "This document isn't ready yet",
        description: err.message || "Only documents with status ready can be used for this action.",
        retryable: false,
      };
    case "embedding_failed":
      return {
        title: "Indexing failed",
        description: "The text was extracted but could not be embedded for search. Retrying usually helps.",
        retryable: false,
      };
    // Phase 3 — report runs
    case "report_timeout":
      return {
        title: "The report took too long",
        description: "The run exceeded the server's time limit and was stopped. Try a narrower question.",
        retryable: true,
      };
    case "interrupted":
      return {
        title: "The report was interrupted",
        description: "The server restarted while this report was being written. Start it again.",
        retryable: true,
      };
    case "invalid_model_output":
      return {
        title: "The model's answer couldn't be used",
        description: "The model returned malformed output twice in a row. Trying again usually helps.",
        retryable: true,
      };
    case "retrieval_not_configured":
      return {
        title: "Document search isn't available",
        description: "The server has no document retriever configured, so document-grounded chat and reports are unavailable. Turn off “Use my documents” to chat without them.",
        retryable: false,
      };
    // Phase 4 — MCP tracker and approvals
    case "mcp_unavailable":
      return {
        title: "The local tracker isn't available",
        description:
          "The backend couldn't reach its local-tracker MCP server (a local demo process). Check that the backend can start it, then try again.",
        retryable: true,
      };
    case "mcp_timeout":
      return {
        title: "The local tracker didn't answer in time",
        description: "The MCP tool call timed out. Trying again usually helps.",
        retryable: true,
      };
    case "invalid_tool_output":
      return {
        title: "The local tracker returned something unexpected",
        description: "The tool's reply didn't match the expected shape, so it was discarded.",
        retryable: true,
      };
    case "report_not_ready":
      return {
        title: "This report isn't finished",
        description: "A follow-up issue can only be proposed from a report that completed successfully.",
        retryable: false,
      };
    case "proposal_not_pending":
      return {
        title: "This proposal was already decided",
        description:
          "It is no longer waiting for review (it was approved, rejected, expired or is running). Reload to see its current state.",
        retryable: false,
      };
    case "payload_mismatch":
      return {
        title: "The proposal changed since you opened it",
        description: "What you reviewed is not what would run, so nothing was executed. Reload and review it again.",
        retryable: false,
      };
    case "proposal_expired":
      return {
        title: "This proposal has expired",
        description:
          "Proposals can only be approved within 15 minutes of being drafted. Nothing was executed; propose it again from the report.",
        retryable: false,
      };
    case "internal_error":
      return {
        title: "Something went wrong on the server",
        description: "An unexpected error occurred. Details are in the server logs.",
        retryable: false,
      };
    default:
      return {
        title: "Something went wrong",
        description: err.message || "An unexpected error occurred.",
        retryable,
      };
  }
}

/** Short label used next to persisted incomplete/failed assistant messages. */
export function describeMessageErrorCode(code: string | null): string {
  if (!code) return "Unknown error";
  if (code === "llm_rate_limited") return "Provider rate limit reached";
  if (code === "cancelled") return "Stopped";
  return describeError({ code }).title;
}

/**
 * Why a stored document is `failed` (its `error_code`), in one line, plus
 * whether `POST /documents/{id}/retry` can plausibly help.
 */
export function describeDocumentFailure(code: string | null): { label: string; hint: string; canRetry: boolean } {
  switch (code) {
    case "embedding_failed":
      return { label: "Indexing failed", hint: "The search index couldn't be built. Retry to try again.", canRetry: true };
    case "empty_document":
      return { label: "No readable text", hint: "Nothing to index — scanned PDFs need OCR first.", canRetry: false };
    case "corrupt_document":
      return { label: "Couldn't read file", hint: "The file is damaged or password-protected.", canRetry: false };
    case "internal_error":
      return { label: "Processing failed", hint: "An unexpected server error occurred. Retry may help.", canRetry: true };
    case null:
    case "":
      return { label: "Processing failed", hint: "No reason was recorded. Retry may help.", canRetry: true };
    default:
      return { label: describeError({ code }).title, hint: "Retry may help.", canRetry: true };
  }
}
