import { ApiError } from "./errors";
import { isAbortError, readSSE } from "./sse";
import type {
  ActionProposal,
  ChatStreamEvent,
  Chunk,
  Conversation,
  ConversationWithMessages,
  Document,
  IssueList,
  Message,
  MessageRetrieval,
  ProposalStatus,
  ReadyResponse,
  ReportRun,
  RetrievedChunk,
  TrackerInfo,
  UploadResult,
  Usage,
} from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** Minimal XMLHttpRequest surface used for uploads (fetch has no upload progress). */
export type XhrFactory = () => XMLHttpRequest;

export interface ApiClientOptions {
  /** Defaults to the same-origin Next.js proxy. */
  baseUrl?: string;
  fetch?: FetchLike;
  /** Used for uploads with progress. Defaults to the browser XHR; absent → fetch (no progress). */
  xhr?: XhrFactory | null;
}

export interface UploadOptions {
  title?: string;
  signal?: AbortSignal;
  /** Upload byte progress (XHR only). */
  onProgress?: (p: { loaded: number; total: number }) => void;
  /** All bytes sent; the server is now extracting/chunking/embedding (ingestion is synchronous). */
  onUploaded?: () => void;
}

function parseRawHeaders(raw: string): Headers {
  const h = new Headers();
  for (const line of raw.trim().split(/[\r\n]+/)) {
    const i = line.indexOf(":");
    if (i > 0) h.append(line.slice(0, i).trim(), line.slice(i + 1).trim());
  }
  return h;
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
  /** Phase 2: ground the answer in the user's documents. */
  useDocuments?: boolean;
  /** Phase 2: restrict retrieval to these documents (only with `useDocuments`). */
  documentIds?: string[];
}

export type SendMessageResult =
  | { outcome: "done"; message: Message; usage: Usage; userMessage: Message | null; requestId: string }
  | { outcome: "aborted"; userMessage: Message | null; assistantMessageId: string | null; requestId: string };

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private readonly xhrFactory: XhrFactory | null;

  constructor(opts: ApiClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? "/api/v1").replace(/\/+$/, "");
    this.fetchImpl = opts.fetch ?? ((input, init) => fetch(input, init));
    this.xhrFactory =
      opts.xhr !== undefined
        ? opts.xhr
        : typeof XMLHttpRequest !== "undefined"
          ? () => new XMLHttpRequest()
          : null;
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
    const { signal, onEvent, useDocuments, documentIds } = opts;
    let userMessage: Message | null = null;
    let assistantMessageId: string | null = null;
    let requestId = "";

    try {
      const r = await this.request(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
        method: "POST",
        // Phase 1 body stays byte-identical unless documents are requested.
        json: useDocuments
          ? { content, use_documents: true, ...(documentIds?.length ? { document_ids: documentIds } : {}) }
          : { content },
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

  // --- Phase 3: report runs -------------------------------------------------

  /** `202` + the queued run, with the request id (shown if the run later fails). */
  async createReport(body: { question: string; document_ids?: string[] }): Promise<{ run: ReportRun; requestId: string }> {
    const { res, requestId } = await this.request("/reports", { method: "POST", json: body });
    const id = res.headers.get("x-request-id") ?? requestId;
    try {
      return { run: (await res.json()) as ReportRun, requestId: id };
    } catch {
      throw new ApiError({ code: "unknown_error", message: "The server returned an invalid response", status: res.status, requestId: id });
    }
  }

  async listReports(signal?: AbortSignal): Promise<ReportRun[]> {
    const data = await this.json<{ items: ReportRun[] }>("/reports", { signal, cache: "no-store" });
    return data.items;
  }

  getReport(id: string, signal?: AbortSignal): Promise<ReportRun> {
    return this.json<ReportRun>(`/reports/${encodeURIComponent(id)}`, { signal, cache: "no-store" });
  }

  // --- Phase 4: local tracker (MCP) and approvals ---------------------------

  getTracker(signal?: AbortSignal): Promise<TrackerInfo> {
    return this.json<TrackerInfo>("/integrations/tracker", { signal, cache: "no-store" });
  }

  /** Read-only issue list/search. `query` is trimmed and capped at 200 chars client-side too. */
  listTrackerIssues(opts: { query?: string; limit?: number } = {}, signal?: AbortSignal): Promise<IssueList> {
    const params = new URLSearchParams();
    const q = (opts.query ?? "").trim().slice(0, 200);
    if (q) params.set("query", q);
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return this.json<IssueList>(`/integrations/tracker/issues${qs ? `?${qs}` : ""}`, { signal, cache: "no-store" });
  }

  /**
   * Ask the server to draft a follow-up issue from a succeeded report.
   * `existing` is true on `200` (a pending proposal for this report already existed).
   */
  async createProposal(reportId: string): Promise<{ proposal: ActionProposal; existing: boolean }> {
    const { res, requestId } = await this.request(`/reports/${encodeURIComponent(reportId)}/proposals`, {
      method: "POST",
    });
    try {
      return { proposal: (await res.json()) as ActionProposal, existing: res.status === 200 };
    } catch {
      throw new ApiError({
        code: "unknown_error",
        message: "The server returned an invalid response",
        status: res.status,
        requestId: res.headers.get("x-request-id") ?? requestId,
      });
    }
  }

  async listProposals(status?: ProposalStatus | null, signal?: AbortSignal): Promise<ActionProposal[]> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    const data = await this.json<{ items: ActionProposal[] }>(`/proposals${qs}`, { signal, cache: "no-store" });
    return data.items;
  }

  getProposal(id: string, signal?: AbortSignal): Promise<ActionProposal> {
    return this.json<ActionProposal>(`/proposals/${encodeURIComponent(id)}`, { signal, cache: "no-store" });
  }

  /**
   * Approve exactly the payload the reviewer saw: `payloadHash` must be the hash
   * of the proposal as displayed. The server refuses with `payload_mismatch` if
   * the stored payload differs.
   */
  approveProposal(id: string, payloadHash: string): Promise<ActionProposal> {
    return this.json<ActionProposal>(`/proposals/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      json: { payload_hash: payloadHash },
    });
  }

  rejectProposal(id: string): Promise<ActionProposal> {
    return this.json<ActionProposal>(`/proposals/${encodeURIComponent(id)}/reject`, { method: "POST" });
  }

  // --- Phase 2: documents & retrieval --------------------------------------

  async listDocuments(signal?: AbortSignal): Promise<Document[]> {
    const data = await this.json<{ items: Document[] }>("/documents", { signal, cache: "no-store" });
    return data.items;
  }

  getDocument(id: string, signal?: AbortSignal): Promise<Document> {
    return this.json<Document>(`/documents/${encodeURIComponent(id)}`, { signal });
  }

  getChunk(documentId: string, chunkId: string, signal?: AbortSignal): Promise<Chunk> {
    return this.json<Chunk>(
      `/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}`,
      { signal },
    );
  }

  retryDocument(id: string): Promise<Document> {
    return this.json<Document>(`/documents/${encodeURIComponent(id)}/retry`, { method: "POST" });
  }

  async deleteDocument(id: string): Promise<void> {
    await this.request(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  getMessageRetrieval(messageId: string, signal?: AbortSignal): Promise<MessageRetrieval> {
    return this.json<MessageRetrieval>(`/messages/${encodeURIComponent(messageId)}/retrieval`, { signal });
  }

  searchRetrieval(
    body: { query: string; top_k?: number; document_ids?: string[] },
    signal?: AbortSignal,
  ): Promise<{ items: RetrievedChunk[]; embedding_model: string }> {
    return this.json(`/retrieval/search`, { method: "POST", json: body, signal });
  }

  /**
   * Multipart upload. Uses XHR when progress callbacks are given (fetch can't
   * report upload progress), otherwise fetch. `X-Duplicate: true` → `duplicate`.
   */
  async uploadDocument(file: Blob, opts: UploadOptions = {}): Promise<UploadResult> {
    const form = new FormData();
    const name = "name" in file && typeof file.name === "string" ? file.name : "upload";
    form.append("file", file, name);
    if (opts.title?.trim()) form.append("title", opts.title.trim());
    const requestId = newRequestId();

    let res: Response;
    if (this.xhrFactory && (opts.onProgress || opts.onUploaded)) {
      res = await this.xhrUpload(form, requestId, opts);
    } else {
      try {
        // No explicit Content-Type: the runtime adds the multipart boundary.
        res = await this.fetchImpl(`${this.baseUrl}/documents`, {
          method: "POST",
          body: form,
          headers: { Accept: "application/json", "X-Request-ID": requestId },
          signal: opts.signal,
        });
      } catch (err) {
        if (isAbortError(err)) throw err;
        throw new ApiError({ code: "network_error", message: "The upload could not be sent", requestId });
      }
    }
    if (!res.ok) throw await errorFromResponse(res, requestId);
    let document: Document;
    try {
      document = (await res.json()) as Document;
    } catch {
      throw new ApiError({
        code: "unknown_error",
        message: "The server returned an invalid response",
        status: res.status,
        requestId: res.headers.get("x-request-id") ?? requestId,
      });
    }
    return {
      document,
      duplicate: (res.headers.get("x-duplicate") ?? "").toLowerCase() === "true",
      status: res.status,
    };
  }

  private xhrUpload(form: FormData, requestId: string, opts: UploadOptions): Promise<Response> {
    const xhr = this.xhrFactory!();
    return new Promise<Response>((resolve, reject) => {
      const onAbort = () => xhr.abort();
      const cleanup = () => opts.signal?.removeEventListener("abort", onAbort);
      if (opts.signal?.aborted) {
        reject(new DOMException("The operation was aborted.", "AbortError"));
        return;
      }
      opts.signal?.addEventListener("abort", onAbort, { once: true });

      xhr.open("POST", `${this.baseUrl}/documents`);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.setRequestHeader("X-Request-ID", requestId);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) opts.onProgress?.({ loaded: e.loaded, total: e.total });
      };
      xhr.upload.onload = () => opts.onUploaded?.();
      xhr.onload = () => {
        cleanup();
        const status = xhr.status;
        if (status < 200 || status > 599) {
          reject(new ApiError({ code: "network_error", message: "The upload failed", requestId }));
          return;
        }
        const nullBody = status === 204 || status === 304;
        resolve(
          new Response(nullBody ? null : xhr.responseText, {
            status,
            statusText: xhr.statusText,
            headers: parseRawHeaders(xhr.getAllResponseHeaders()),
          }),
        );
      };
      xhr.onerror = () => {
        cleanup();
        reject(new ApiError({ code: "network_error", message: "The upload could not be sent", requestId }));
      };
      xhr.onabort = () => {
        cleanup();
        reject(new DOMException("The operation was aborted.", "AbortError"));
      };
      xhr.send(form);
    });
  }
}

/** Narrow one RetrievedChunk; malformed entries are dropped by the caller. */
function parseRetrievedChunk(v: unknown): RetrievedChunk | null {
  if (!isRecord(v)) return null;
  if (typeof v.chunk_id !== "string" || typeof v.document_id !== "string") return null;
  if (typeof v.score !== "number" || typeof v.rank !== "number") return null;
  const loc = isRecord(v.location) ? v.location : {};
  return {
    chunk_id: v.chunk_id,
    document_id: v.document_id,
    document_title: typeof v.document_title === "string" ? v.document_title : "Untitled document",
    rank: v.rank,
    score: v.score,
    excerpt: typeof v.excerpt === "string" ? v.excerpt : "",
    location: {
      char_start: typeof loc.char_start === "number" ? loc.char_start : 0,
      char_end: typeof loc.char_end === "number" ? loc.char_end : 0,
      page: typeof loc.page === "number" ? loc.page : null,
      heading_path: Array.isArray(loc.heading_path)
        ? loc.heading_path.filter((x): x is string => typeof x === "string")
        : [],
    },
  };
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
    case "retrieval": {
      if (!Array.isArray(payload.chunks) || typeof payload.sufficient !== "boolean") return null;
      const chunks = payload.chunks
        .map(parseRetrievedChunk)
        .filter((c): c is RetrievedChunk => c !== null);
      return { type: "retrieval", chunks, sufficient: payload.sufficient };
    }
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
