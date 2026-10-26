import { afterEach, describe, expect, it, vi } from "vitest";
import { proxyRequest, type FetchLike } from "./proxy";

const enc = new TextEncoder();

afterEach(() => {
  vi.unstubAllEnvs();
});

function upstreamOk(body: BodyInit | null, init: ResponseInit = {}) {
  return vi.fn<FetchLike>(async () => new Response(body, init));
}

describe("proxyRequest", () => {
  it("forwards method, body, query, content-type and X-Request-ID to BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    const fetchImpl = upstreamOk('{"id":"c1"}', { status: 201, headers: { "content-type": "application/json", "x-request-id": "rid-1" } });
    const req = new Request("http://localhost:3000/api/v1/conversations?x=1&y=2", {
      method: "POST",
      headers: { "content-type": "application/json", "x-request-id": "rid-1", cookie: "secret=1", authorization: "Bearer nope" },
      body: '{"title":"Hi"}',
    });

    const res = await proxyRequest(req, ["conversations"], { fetch: fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/api/v1/conversations?x=1&y=2");
    expect(init?.method).toBe("POST");
    expect(new TextDecoder().decode(init?.body as ArrayBuffer)).toBe('{"title":"Hi"}');
    const h = new Headers(init?.headers);
    expect(h.get("x-request-id")).toBe("rid-1");
    expect(h.get("content-type")).toBe("application/json");
    expect(h.get("cookie")).toBeNull();
    expect(h.get("authorization")).toBeNull();
    expect(init?.signal).toBe(req.signal);

    expect(res.status).toBe(201);
    expect(res.headers.get("x-request-id")).toBe("rid-1");
    expect(await res.json()).toEqual({ id: "c1" });
  });

  it("defaults to http://localhost:8000 and generates a request id when missing", async () => {
    vi.stubEnv("BACKEND_URL", "");
    const fetchImpl = upstreamOk('{"status":"ok"}', { status: 200 });
    const res = await proxyRequest(new Request("http://localhost:3000/api/v1/health"), ["health"], { fetch: fetchImpl });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/health");
    expect(init?.body).toBeUndefined();
    const rid = new Headers(init?.headers).get("x-request-id");
    expect(rid).toMatch(/^[0-9a-f-]{36}$/);
    expect(res.headers.get("x-request-id")).toBe(rid);
  });

  it("passes error statuses and bodies through unchanged", async () => {
    const env = { error: { code: "llm_not_configured", message: "No model", request_id: "r" } };
    const fetchImpl = upstreamOk(JSON.stringify(env), { status: 503, headers: { "content-type": "application/json" } });
    const res = await proxyRequest(new Request("http://x/api/v1/conversations/1/messages", { method: "POST", body: "{}" }), ["conversations", "1", "messages"], { fetch: fetchImpl });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(env);
  });

  it("returns an empty body for 204", async () => {
    const fetchImpl = upstreamOk(null, { status: 204 });
    const res = await proxyRequest(new Request("http://x/api/v1/conversations/1", { method: "DELETE" }), ["conversations", "1"], { fetch: fetchImpl });
    expect(res.status).toBe(204);
    expect(fetchImpl.mock.calls[0][1]?.method).toBe("DELETE");
    expect(await res.text()).toBe("");
  });

  it("streams SSE bodies without buffering and disables transforms", async () => {
    let push!: (s: string) => void;
    let close!: () => void;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(c) {
        push = (s) => c.enqueue(enc.encode(s));
        close = () => c.close();
      },
    });
    const fetchImpl = upstreamOk(upstreamBody, {
      status: 200,
      headers: { "content-type": "text/event-stream", "content-encoding": "gzip", "content-length": "999" },
    });
    const res = await proxyRequest(new Request("http://x/api/v1/conversations/1/messages", { method: "POST", body: '{"content":"hi"}' }), ["conversations", "1", "messages"], { fetch: fetchImpl });

    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toBe("no-cache, no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(res.headers.get("content-encoding")).toBeNull();
    expect(res.headers.get("content-length")).toBeNull();

    // The proxy resolved while upstream is still open: each chunk must be readable
    // as soon as it is produced, proving there is no buffering of the whole body.
    const reader = res.body!.getReader();
    const dec = new TextDecoder();
    push("event: token\ndata: {\"delta\":\"a\"}\n\n");
    expect(dec.decode((await reader.read()).value)).toContain('"a"');
    push("event: token\ndata: {\"delta\":\"b\"}\n\n");
    expect(dec.decode((await reader.read()).value)).toContain('"b"');
    close();
    expect((await reader.read()).done).toBe(true);
  });

  it("returns a 502 backend_unreachable envelope when the backend is down", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchImpl = vi.fn<FetchLike>(async () => {
      throw new TypeError("fetch failed");
    });
    const res = await proxyRequest(new Request("http://x/api/v1/ready", { headers: { "x-request-id": "rid-down" } }), ["ready"], { fetch: fetchImpl });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({
      error: { code: "backend_unreachable", message: "The AgentOps backend could not be reached", request_id: "rid-down" },
    });
    expect(res.headers.get("x-request-id")).toBe("rid-down");
    spy.mockRestore();
  });

  it("rejects dot segments and encodes path segments", async () => {
    const fetchImpl = upstreamOk("{}", { status: 200 });
    const bad = await proxyRequest(new Request("http://x/api/v1/x"), ["..", "admin"], { fetch: fetchImpl });
    expect(bad.status).toBe(404);
    expect(fetchImpl).not.toHaveBeenCalled();

    await proxyRequest(new Request("http://x/api/v1/x"), ["conversations", "a b/c"], { fetch: fetchImpl });
    expect(fetchImpl.mock.calls[0][0]).toBe("http://localhost:8000/api/v1/conversations/a%20b%2Fc");
  });
  it("passes multipart upload bodies through byte-for-byte with their boundary", async () => {
    const form = new FormData();
    const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x00, 0xff, 0xfe, 0x0a, 0x0d]); // binary, incl. NUL/CRLF
    form.append("file", new File([bytes], "paper.pdf", { type: "application/pdf" }));
    form.append("title", "Zażółć ✓");
    const req = new Request("http://localhost:3000/api/v1/documents", { method: "POST", body: form });
    const contentType = req.headers.get("content-type")!;
    expect(contentType).toMatch(/^multipart\/form-data; boundary=/);
    const original = new Uint8Array(await req.clone().arrayBuffer());

    const fetchImpl = upstreamOk(JSON.stringify({ id: "d1" }), { status: 201, headers: { "content-type": "application/json" } });
    const res = await proxyRequest(req, ["documents"], { fetch: fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/v1/documents");
    expect(new Headers(init?.headers).get("content-type")).toBe(contentType);
    expect(new Uint8Array(init?.body as ArrayBuffer)).toEqual(original);
    // The forwarded body is still a parseable multipart payload.
    const reparsed = await new Request("http://x", { method: "POST", body: init?.body as ArrayBuffer, headers: { "content-type": contentType } }).formData();
    expect(new Uint8Array(await (reparsed.get("file") as File).arrayBuffer())).toEqual(bytes);
    expect(reparsed.get("title")).toBe("Zażółć ✓");
    expect(res.status).toBe(201);
  });

  it("forwards custom response headers such as X-Duplicate", async () => {
    const fetchImpl = upstreamOk('{"id":"d1"}', { status: 200, headers: { "content-type": "application/json", "x-duplicate": "true" } });
    const res = await proxyRequest(new Request("http://x/api/v1/documents", { method: "POST", body: "x" }), ["documents"], { fetch: fetchImpl });
    expect(res.status).toBe(200);
    expect(res.headers.get("x-duplicate")).toBe("true");
  });

  it("refuses bodies above the proxy ceiling without calling the backend", async () => {
    const fetchImpl = upstreamOk("{}", { status: 201 });
    const big = new Request("http://x/api/v1/documents", { method: "POST", body: new Uint8Array(2048) });
    const res = await proxyRequest(big, ["documents"], { fetch: fetchImpl, maxBodyBytes: 1024 });
    expect(res.status).toBe(413);
    expect((await res.json()).error.code).toBe("file_too_large");
    expect(fetchImpl).not.toHaveBeenCalled();

    const declared = new Request("http://x/api/v1/documents", { method: "POST", body: "small", headers: { "content-length": "999999999" } });
    expect((await proxyRequest(declared, ["documents"], { fetch: fetchImpl, maxBodyBytes: 1024 })).status).toBe(413);

    vi.stubEnv("PROXY_MAX_BODY_BYTES", "4096");
    const ok = await proxyRequest(new Request("http://x/api/v1/documents", { method: "POST", body: new Uint8Array(2048) }), ["documents"], { fetch: fetchImpl });
    expect(ok.status).toBe(201);
  });
});
