import { describe, expect, it, vi } from "vitest";
import { ApiClient, errorFromResponse, type FetchLike } from "./client";
import { ApiError, describeError } from "./errors";
import type { ChatStreamEvent } from "./types";
import { CONV_ID, message } from "@/test/fixtures";
import { sse, streamFromChunks } from "@/test/streams";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function envelope(code: string, msg = "msg", extra: Record<string, unknown> = {}) {
  return { error: { code, message: msg, request_id: "req-123", ...extra } };
}

function sseResponse(chunks: string[], opts: { holdOpen?: boolean } = {}) {
  return new Response(streamFromChunks(chunks, opts), {
    status: 200,
    headers: { "Content-Type": "text/event-stream", "X-Request-ID": "req-stream" },
  });
}

function clientWith(fetchImpl: FetchLike) {
  return new ApiClient({ fetch: fetchImpl });
}

describe("errorFromResponse", () => {
  it.each([
    ["validation_error", 422, false],
    ["not_found", 404, false],
    ["llm_not_configured", 503, false],
    ["llm_auth_failed", 502, false],
    ["llm_rate_limited", 429, true],
    ["llm_timeout", 504, true],
    ["llm_unavailable", 502, true],
    ["internal_error", 500, false],
  ])("maps %s envelope (HTTP %i) with retryable=%s", async (code, status, retryable) => {
    const err = await errorFromResponse(jsonResponse(status, envelope(code)));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe(code);
    expect(err.status).toBe(status);
    expect(err.requestId).toBe("req-123");
    expect(err.retryable).toBe(retryable);
  });

  it("reads retry_after_s from the envelope, falling back to Retry-After", async () => {
    const a = await errorFromResponse(jsonResponse(429, envelope("llm_rate_limited", "x", { retry_after_s: 12 })));
    expect(a.retryAfterS).toBe(12);
    const b = await errorFromResponse(jsonResponse(429, envelope("llm_rate_limited"), { "Retry-After": "7" }));
    expect(b.retryAfterS).toBe(7);
  });

  it("falls back to X-Request-ID header and infers code for non-envelope bodies", async () => {
    const err = await errorFromResponse(
      new Response("<html>Bad Gateway</html>", { status: 502, headers: { "X-Request-ID": "hdr-1" } }),
    );
    expect(err.code).toBe("backend_unreachable");
    expect(err.requestId).toBe("hdr-1");
    expect(err.retryable).toBe(true);
  });
});

describe("describeError", () => {
  it("uses the human messages from the spec", () => {
    expect(describeError({ code: "llm_not_configured" }).title).toBe("No model is configured on the server");
    expect(describeError({ code: "llm_rate_limited", retryAfterS: 4.2 }).title).toBe(
      "Free-tier rate limit reached, try again in 5s",
    );
    expect(describeError({ code: "llm_rate_limited" }).retryable).toBe(true);
    expect(describeError({ code: "internal_error" }).retryable).toBe(false);
    expect(describeError({ code: "cancelled" }).retryable).toBe(false);
    expect(describeError({ code: "something_new", message: "Server said so" }).description).toBe("Server said so");
  });
});

describe("ApiClient JSON endpoints", () => {
  it("sends X-Request-ID and parses the conversation list", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => jsonResponse(200, { items: [{ id: CONV_ID }] }));
    const items = await clientWith(fetchImpl).listConversations();
    expect(items).toEqual([{ id: CONV_ID }]);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/conversations");
    expect(new Headers(init?.headers).get("x-request-id")).toMatch(/.+/);
  });

  it("creates a conversation with a JSON body", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => jsonResponse(201, { id: CONV_ID, title: "New conversation" }));
    await clientWith(fetchImpl).createConversation();
    const [, init] = fetchImpl.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe("{}");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("handles 204 on delete and throws ApiError on 404", async () => {
    const ok = clientWith(async () => new Response(null, { status: 204 }));
    await expect(ok.deleteConversation(CONV_ID)).resolves.toBeUndefined();
    const missing = clientWith(async () => jsonResponse(404, envelope("not_found", "Conversation not found")));
    await expect(missing.getConversation(CONV_ID)).rejects.toMatchObject({ code: "not_found", status: 404 });
  });

  it("maps a fetch rejection to network_error", async () => {
    const c = clientWith(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(c.listConversations()).rejects.toMatchObject({ code: "network_error", retryable: true });
  });

  it("returns /ready bodies for both 200 and 503", async () => {
    const ready = { status: "ready", checks: { database: "ok", llm: "configured" }, llm: { provider: "fake", model: "fake-echo" } };
    await expect(clientWith(async () => jsonResponse(200, ready)).getReady()).resolves.toEqual(ready);
    const notReady = { status: "not_ready", checks: { database: "error", llm: "not_configured" } };
    await expect(clientWith(async () => jsonResponse(503, notReady)).getReady()).resolves.toEqual(notReady);
    await expect(clientWith(async () => new Response("nope", { status: 502 })).getReady()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("ApiClient.sendMessage", () => {
  const user = message({ content: "Hi" });
  const assistant = message({ id: "a1", role: "assistant", content: "Hello there", model: "fake-echo" });

  it("emits typed events and resolves with the final message", async () => {
    const body =
      sse("message.created", { user_message: user, assistant_message_id: "a1" }) +
      sse("token", { delta: "Hello" }) +
      sse("token", { delta: " there" }) +
      sse("done", { message: assistant, usage: { input_tokens: 3, output_tokens: 2 } });
    // split mid-event to exercise chunk boundaries end to end
    const chunks = [body.slice(0, 50), body.slice(50, 140), body.slice(140)];
    const fetchImpl = vi.fn<FetchLike>(async () => sseResponse(chunks));
    const events: ChatStreamEvent[] = [];
    const result = await clientWith(fetchImpl).sendMessage(CONV_ID, "Hi", { onEvent: (e) => events.push(e) });

    expect(events.map((e) => e.type)).toEqual(["message.created", "token", "token", "done"]);
    expect(result).toMatchObject({ outcome: "done", message: assistant, userMessage: user, requestId: "req-stream" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(`/api/v1/conversations/${CONV_ID}/messages`);
    expect(init?.body).toBe(JSON.stringify({ content: "Hi" }));
    expect(new Headers(init?.headers).get("accept")).toBe("text/event-stream");
  });

  it("throws a pre-stream envelope error (llm_not_configured)", async () => {
    const c = clientWith(async () => jsonResponse(503, envelope("llm_not_configured", "No provider")));
    await expect(c.sendMessage(CONV_ID, "Hi")).rejects.toMatchObject({
      code: "llm_not_configured",
      status: 503,
      retryable: false,
      requestId: "req-123",
    });
  });

  it("throws an ApiError from an in-stream error event", async () => {
    const body =
      sse("message.created", { user_message: user, assistant_message_id: "a1" }) +
      sse("token", { delta: "Part" }) +
      sse("error", { code: "llm_rate_limited", message: "Slow down", retryable: true, request_id: "req-e", message_id: "a1", retry_after_s: 9 });
    const c = clientWith(async () => sseResponse([body]));
    const err = (await c.sendMessage(CONV_ID, "Hi").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "llm_rate_limited", retryable: true, requestId: "req-e", retryAfterS: 9 });
    expect(describeError(err).title).toBe("Free-tier rate limit reached, try again in 9s");
  });

  it("accepts retry_after_s: null on an in-stream error", async () => {
    const body = sse("error", { code: "llm_rate_limited", message: "x", retryable: true, request_id: "r", message_id: "a1", retry_after_s: null });
    const err = (await clientWith(async () => sseResponse([body])).sendMessage(CONV_ID, "Hi").catch((e: unknown) => e)) as ApiError;
    expect(err.retryAfterS).toBeNull();
    expect(describeError(err).title).toBe("Free-tier rate limit reached, try again shortly");
  });

  it("reports stream_interrupted when the stream closes without a terminal event", async () => {
    const body = sse("message.created", { user_message: user, assistant_message_id: "a1" }) + sse("token", { delta: "x" });
    const c = clientWith(async () => sseResponse([body]));
    await expect(c.sendMessage(CONV_ID, "Hi")).rejects.toMatchObject({ code: "stream_interrupted", retryable: true });
  });

  it("ignores unknown events and malformed data", async () => {
    const body =
      "event: ping\ndata: {}\n\n" +
      "event: token\ndata: not-json\n\n" +
      sse("done", { message: assistant, usage: { input_tokens: null, output_tokens: null } });
    const c = clientWith(async () => sseResponse([body]));
    await expect(c.sendMessage(CONV_ID, "Hi")).resolves.toMatchObject({ outcome: "done" });
  });

  it("resolves as aborted when the AbortController fires mid-stream", async () => {
    const body = sse("message.created", { user_message: user, assistant_message_id: "a1" }) + sse("token", { delta: "x" });
    const ac = new AbortController();
    const c = clientWith(async () => sseResponse([body], { holdOpen: true }));
    const result = await c.sendMessage(CONV_ID, "Hi", {
      signal: ac.signal,
      onEvent: (e) => {
        if (e.type === "token") ac.abort();
      },
    });
    expect(result).toMatchObject({ outcome: "aborted", assistantMessageId: "a1", userMessage: user });
  });

  it("resolves as aborted when aborted before the response arrives", async () => {
    const ac = new AbortController();
    const c = clientWith(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const p = c.sendMessage(CONV_ID, "Hi", { signal: ac.signal });
    ac.abort();
    await expect(p).resolves.toMatchObject({ outcome: "aborted" });
  });
});
