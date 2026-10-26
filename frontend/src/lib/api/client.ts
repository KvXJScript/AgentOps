import { ApiError } from "./errors";
import { isAbortError, readSSE } from "./sse";
import type {
  ChatStreamEvent,
  Conversation,
  ConversationWithMessages,
  Message,
  ReadyResponse,
  Usage,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  /** Defaults to the same-origin Next.js proxy. */
  baseUrl?: string;
  fetch?: FetchLike;
}

export function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function parseRetryAfterHeader(res: Response): number | null {
  const h = res.headers.get("retry-after");
  if (!h) return null;
  const n = Number(h);
  if (Number.isFinite(n)) return n;
  const date = Date.parse(h);
  return Number.isNaN(date) ? null : Math.max(0, (date - Date.now()) / 1000);
}

/** Convert a non-2xx response into an ApiError using the contract envelope. */
export async function errorFromResponse(res: Response, sentRequestId?: string): Promise<ApiError> {
  const headerId = res.headers.get("x-request-id");
  let body: unknown = null;
  try {
    const text = await res.text();
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }

  if (isRecord(body) && isRecord(body.error) && typeof body.error.code === "string") {
    const e = body.error;
    const retryAfter =
      typeof e.retry_after_s === "number" ? e.retry_after_s : parseRetryAfterHeader(res);
    return new ApiError({
      code: e.code as string,
      message: typeof e.message === "string" ? e.message : res.statusText,
      status: res.status,
      requestId: (typeof e.request_id === "string" ? e.request_id : null) ?? headerId ?? sentRequestId,
      retryAfterS: retryAfter,
      details: e.details,
    });
  }

  // No envelope (e.g. a proxy or gateway error page): infer from status.
  const code =
    res.status === 429
      ? "llm_rate_limited"
      : res.status === 502 || res.status === 503 || res.status === 504
        ? "backend_unreachable"
        : res.status === 404
          ? "not_found"
          : "unknown_error";
  return new ApiError({
    code,
    message: `Request failed with status ${res.status}`,
    status: res.status,
    requestId: headerId ?? sentRequestId,
    retryAfterS: parseRetryAfterHeader(res),
  });
}

export interface SendMessageOptions {
  signal?: AbortSignal;
  onEvent?: (event: ChatStreamEvent) => void;
}

export type SendMessageResult =
  | { outcome: "done"; message: Message; usage: Usage; userMessage: Message | null; requestId: string }
  | { outcome: "aborted"; userMessage: Message | null; assistantMessageId: string | null; requestId: string };

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;

  constructor(opts: ApiClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "/api/v1").replace(/\/+$/, "");
    this.fetchImpl = opts.fetch ?? ((input, init) => fetch(input, init));
  }

  private async request(
    path: string,
    init: RequestInit & { json?: unknown } = {},
  ): Promise<{ res: Response; requestId: string }> {
    const requestId = newRequestId();
    const headers = new Headers(init.headers);
    headers.set("X-Request-ID", requestId);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    let body = init.body;
    if (init.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(init.json);
    }
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers, body });
    } catch (err) {
      if (isAbortError(err)) throw err;
      throw new ApiError({
        code: "network_error",
        message: err instanceof Error ? err.message : "Network request failed",
        requestId,
      });
    }
    if (!res.ok) throw await errorFromResponse(res, requestId);
    return { res, requestId };
  }

  private async json<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
    const { res, requestId } = await this.request(path, init);
    try {
      return (await res.json()) as T;
    } catch {
      throw new ApiError({
        code: "unknown_error",
        message: "The server returned an invalid response",
        status: res.status,
        requestId: res.headers.get("x-request-id") ?? requestId,
      });
    }
  }

  async listConversations(signal?: AbortSignal): Promise<Conversation[]> {
    const data = await this.json<{ items: Conversation[] }>("/conversations", { signal });
    return data.items;
  }

  createConversation(title?: string): Promise<Conversation> {
    return this.json<Conversation>("/conversations", {
      method: "POST",
      json: title === undefined ? {} : { title },
    });
  }

  getConversation(id: string, signal?: AbortSignal): Promise<ConversationWithMessages> {
    return this.json<ConversationWithMessages>(`/conversations/${encodeURIComponent(id)}`, { signal });
  }

  async deleteConversation(id: string): Promise<void> {
    await this.request(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  /**
   * `/ready` answers 200 (ready) or 503 (not_ready) with the same body shape,
   * so a 503 with a valid body is a result, not an error.
   */
  async getReady(signal?: AbortSignal): Promise<ReadyResponse> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}/ready`, {
        signal,
        headers: { Accept: "application/json", "X-Request-ID": newRequestId() },
        cache: "no-store",
      });
    } catch (err) {
      if (isAbortError(err)) throw err;
      throw new ApiError({ code: "network_error", message: "Network request failed" });
    }
    if (res.ok || res.status === 503) {
      const body: unknown = await res.clone().json().catch(() => null);
      if (isRecord(body) && (body.status === "ready" || body.status === "not_ready") && isRecord(body.checks)) {
        return body as unknown as ReadyResponse;
      }
    }
    throw await errorFromResponse(res);
  }

  /**
   * POST a message and consume the SSE stream. Resolves on `done` or abort;
   * rejects with ApiError on a pre-stream error, an `error` event, or a stream
   * that ends without a terminal event.
   */
  async sendMessage(
    conversationId: string,
    content: string,
    opts: SendMessageOptions = {},
  ): Promise<SendMessageResult> {
    const { signal, onEvent } = opts;
    let userMessage: Message | null = null;
    let assistantMessageId: string | null = null;
    let requestId = "";

    try {
      const r = await this.request(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
        method: "POST",
        json: { content },
        headers: { Accept: "text/event-stream" },
        signal,
      });
      requestId = r.res.headers.get("x-request-id") ?? r.requestId;
      const { res } = r;
      if (!res.body) {
        throw new ApiError({ code: "stream_interrupted", message: "Empty response body", requestId });
      }

      for await (const raw of readSSE(res.body, signal)) {
        const event = parseChatEvent(raw.event, raw.data);
        if (!event) continue;
        onEvent?.(event);
        switch (event.type) {
          case "message.created":
            userMessage = event.user_message;
            assistantMessageId = event.assistant_message_id;
            break;
          case "done":
            return { outcome: "done", message: event.message, usage: event.usage, userMessage, requestId };
          case "error":
            throw new ApiError({
              code: event.code,
              message: event.message,
              retryable: event.retryable,
              requestId: event.request_id ?? requestId,
              retryAfterS: event.retry_after_s ?? null,
              details: { message_id: event.message_id },
            });
          default:
            break;
        }
      }
      if (signal?.aborted) return { outcome: "aborted", userMessage, assistantMessageId, requestId };
      throw new ApiError({
        code: "stream_interrupted",
        message: "The stream ended before the response was complete",
        requestId,
      });
    } catch (err) {
      if (isAbortError(err) || (signal?.aborted && !(err instanceof ApiError))) {
        return { outcome: "aborted", userMessage, assistantMessageId, requestId };
      }
      if (err instanceof ApiError) throw err;
      throw new ApiError({
        code: "stream_interrupted",
        message: err instanceof Error ? err.message : "The stream failed",
        requestId: requestId || null,
      });
    }
  }
}

/** Validate and narrow one SSE event from the chat stream. Unknown events → null. */
export function parseChatEvent(event: string, data: string): ChatStreamEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  if (!isRecord(payload)) return null;
  switch (event) {
    case "message.created":
      if (isRecord(payload.user_message) && typeof payload.assistant_message_id === "string") {
        return {
          type: "message.created",
          user_message: payload.user_message as unknown as Message,
          assistant_message_id: payload.assistant_message_id,
        };
      }
      return null;
    case "token":
      return typeof payload.delta === "string" ? { type: "token", delta: payload.delta } : null;
    case "done":
      if (!isRecord(payload.message)) return null;
      return {
        type: "done",
        message: payload.message as unknown as Message,
        usage: isRecord(payload.usage)
          ? (payload.usage as unknown as Usage)
          : { input_tokens: null, output_tokens: null },
      };
    case "error":
      return {
        type: "error",
        code: typeof payload.code === "string" ? payload.code : "unknown_error",
        message: typeof payload.message === "string" ? payload.message : "The response failed",
        retryable: payload.retryable === true,
        request_id: typeof payload.request_id === "string" ? payload.request_id : null,
        message_id: typeof payload.message_id === "string" ? payload.message_id : null,
        ...(typeof payload.retry_after_s === "number" ? { retry_after_s: payload.retry_after_s } : {}),
      };
    default:
      return null;
  }
}

export const api = new ApiClient();
