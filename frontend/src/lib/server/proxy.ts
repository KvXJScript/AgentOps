/**
 * Server-side reverse proxy from `/api/v1/*` (Next.js) to the FastAPI backend.
 *
 * - BACKEND_URL is a server-only env var; the browser never sees it.
 * - Method, body, query string, X-Request-ID and status are passed through.
 * - The upstream body is returned as-is (a ReadableStream), never buffered,
 *   so SSE tokens reach the browser as soon as the backend flushes them.
 * - Request bodies (JSON or multipart uploads) are forwarded byte-for-byte with
 *   their original Content-Type (incl. the multipart boundary). Bodies above
 *   PROXY_MAX_BODY_BYTES are refused before being buffered; the limit sits well
 *   above the backend's MAX_UPLOAD_BYTES so the backend stays authoritative.
 * - Custom response headers (e.g. `X-Duplicate`) are passed through.
 */

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ProxyOptions {
  backendUrl?: string;
  fetch?: FetchLike;
  maxBodyBytes?: number;
}

export const DEFAULT_BACKEND_URL = "http://localhost:8000";
/** Hard ceiling for proxied request bodies (memory guard, not a business rule). */
export const DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024;

function maxBodyBytes(opts: ProxyOptions): number {
  if (opts.maxBodyBytes !== undefined) return opts.maxBodyBytes;
  const n = Number(process.env.PROXY_MAX_BODY_BYTES);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_MAX_BODY_BYTES;
}

/** Request headers forwarded upstream (everything else is dropped). */
const FORWARD_REQUEST_HEADERS = ["accept", "content-type", "x-request-id", "last-event-id"];

/** Response headers that must not be copied from the upstream response. */
const DROP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-length",
  // fetch() already decoded the body, so the original encoding no longer applies.
  "content-encoding",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "upgrade",
  "set-cookie",
]);

function backendBase(opts: ProxyOptions): string {
  const raw = opts.backendUrl ?? process.env.BACKEND_URL;
  return (raw && raw.trim() ? raw.trim() : DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

function newRequestId(): string {
  return crypto.randomUUID();
}

function errorResponse(status: number, code: string, message: string, requestId: string): Response {
  return Response.json(
    { error: { code, message, request_id: requestId } },
    { status, headers: { "X-Request-ID": requestId, "Cache-Control": "no-store" } },
  );
}

export async function proxyRequest(
  req: Request,
  path: string[],
  opts: ProxyOptions = {},
): Promise<Response> {
  const requestId = req.headers.get("x-request-id")?.trim() || newRequestId();

  // Reject dot segments so a crafted path can't escape /api/v1 upstream.
  if (path.some((seg) => seg === "" || seg === "." || seg === "..")) {
    return errorResponse(404, "not_found", "Not found", requestId);
  }

  const incoming = new URL(req.url);
  const target = `${backendBase(opts)}/api/v1/${path.map(encodeURIComponent).join("/")}${incoming.search}`;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("x-request-id", requestId);
  // Ask the backend not to compress so nothing upstream batches SSE frames.
  headers.set("accept-encoding", "identity");

  const method = req.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";
  const limit = maxBodyBytes(opts);
  const declared = Number(req.headers.get("content-length"));
  if (hasBody && Number.isFinite(declared) && declared > limit) {
    return errorResponse(413, "file_too_large", "The request body is too large", requestId);
  }
  const body = hasBody ? await req.arrayBuffer() : undefined;
  if (body && body.byteLength > limit) {
    return errorResponse(413, "file_too_large", "The request body is too large", requestId);
  }

  const doFetch: FetchLike = opts.fetch ?? ((input, init) => fetch(input, init));
  let upstream: Response;
  try {
    upstream = await doFetch(target, {
      method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      cache: "no-store",
      signal: req.signal,
    });
  } catch (err) {
    if (req.signal?.aborted) {
      // Client went away; nobody will read this.
      return new Response(null, { status: 499, headers: { "X-Request-ID": requestId } });
    }
    console.error(`[proxy] ${method} ${target} failed (request_id=${requestId}):`, err);
    return errorResponse(
      502,
      "backend_unreachable",
      "The AgentOps backend could not be reached",
      requestId,
    );
  }

  const outHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!DROP_RESPONSE_HEADERS.has(key.toLowerCase())) outHeaders.set(key, value);
  });
  if (!outHeaders.has("x-request-id")) outHeaders.set("x-request-id", requestId);

  const contentType = upstream.headers.get("content-type") ?? "";
  if (contentType.includes("text/event-stream")) {
    // `no-transform` makes Next's built-in gzip (next start) skip this response;
    // X-Accel-Buffering disables buffering in nginx-style reverse proxies.
    outHeaders.set("cache-control", "no-cache, no-transform");
    outHeaders.set("x-accel-buffering", "no");
  }

  const nullBody = upstream.status === 204 || upstream.status === 304 || method === "HEAD";
  return new Response(nullBody ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outHeaders,
  });
}
