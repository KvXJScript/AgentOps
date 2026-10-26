import type { ErrorCode } from "./types";

const RETRYABLE: ReadonlySet<string> = new Set([
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
