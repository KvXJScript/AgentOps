/** Types mirroring docs/api-contract.md (v1). Keep in sync with the contract. */

export type ContractErrorCode =
  | "validation_error"
  | "not_found"
  | "llm_not_configured"
  | "llm_auth_failed"
  | "llm_rate_limited"
  | "llm_timeout"
  | "llm_unavailable"
  /** Stored on an assistant message when the client disconnected mid-stream; never an HTTP error. */
  | "cancelled"
  | "internal_error";

/**
 * Codes produced by the frontend itself (never sent by the backend):
 * - `backend_unreachable`: the Next.js proxy could not reach BACKEND_URL
 * - `network_error`: the browser could not reach the Next.js server
 * - `stream_interrupted`: the SSE stream ended without a `done`/`error` event
 * - `unknown_error`: a response we could not interpret
 */
export type ClientErrorCode =
  | "backend_unreachable"
  | "network_error"
  | "stream_interrupted"
  | "unknown_error";

export type ErrorCode = ContractErrorCode | ClientErrorCode;

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string;
    details?: unknown;
    retry_after_s?: number;
  };
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export type MessageRole = "user" | "assistant";
export type MessageStatus = "complete" | "incomplete" | "failed";

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  error_code: string | null;
  model: string | null;
  created_at: string;
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

export interface ReadyResponse {
  status: "ready" | "not_ready";
  checks: {
    database: "ok" | "error";
    llm: "configured" | "not_configured";
  };
  llm?: { provider: string; model: string };
}

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
}

export type ChatStreamEvent =
  | {
      type: "message.created";
      user_message: Message;
      assistant_message_id: string;
    }
  | { type: "token"; delta: string }
  | { type: "done"; message: Message; usage: Usage }
  | {
      type: "error";
      code: string;
      message: string;
      retryable: boolean;
      request_id: string | null;
      message_id: string | null;
      retry_after_s?: number;
    };

export const MAX_MESSAGE_LENGTH = 4000;
