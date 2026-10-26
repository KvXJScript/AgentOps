import { describe, expect, it, vi } from "vitest";
import { ApiClient, parseChatEvent, type FetchLike } from "./client";
import { ApiError, describeDocumentFailure, describeError } from "./errors";
import type { ChatStreamEvent, Document, RetrievedChunk } from "./types";
import { CONV_ID, message } from "@/test/fixtures";
import { sse, streamFromChunks } from "@/test/streams";

const chunk = (over: Partial<RetrievedChunk> = {}): RetrievedChunk => ({
  chunk_id: "c1",
  document_id: "d1",
  document_title: "Handbook",
  rank: 1,
  score: 0.82,
  excerpt: "Install with pip.",
  location: { char_start: 0, char_end: 17, page: null, heading_path: ["Setup"] },
  ...over,
});

const doc = (over: Partial<Document> = {}): Document => ({
  id: "d1",
  title: "Handbook",
  filename: "handbook.md",
  media_type: "text/markdown",
  size_bytes: 120,
  checksum_sha256: "abc",
  status: "ready",
  error_code: null,
  chunk_count: 3,
  embedding_model: "e5",
  created_at: "2026-10-26T09:00:00Z",
  updated_at: "2026-10-26T09:00:00Z",
  ...over,
});

describe("parseChatEvent: retrieval", () => {
  it("parses a retrieval event", () => {
    const ev = parseChatEvent("retrieval", JSON.stringify({ chunks: [chunk(), chunk({ chunk_id: "c2", rank: 2, score: 0.4 })], sufficient: true }));
    expect(ev).toEqual({ type: "retrieval", chunks: [chunk(), chunk({ chunk_id: "c2", rank: 2, score: 0.4 })], sufficient: true });
  });

  it("accepts an empty insufficient retrieval", () => {
    expect(parseChatEvent("retrieval", '{"chunks":[],"sufficient":false}')).toEqual({ type: "retrieval", chunks: [], sufficient: false });
  });

  it("drops malformed chunks and normalises missing location fields", () => {
    const ev = parseChatEvent(
      "retrieval",
      JSON.stringify({ chunks: [{ nope: 1 }, { chunk_id: "c3", document_id: "d", rank: 1, score: 0.5, location: { heading_path: ["A", 3] } }], sufficient: true }),
    );
    expect(ev?.type === "retrieval" && ev.chunks).toEqual([
      { chunk_id: "c3", document_id: "d", document_title: "Untitled document", rank: 1, score: 0.5, excerpt: "", location: { char_start: 0, char_end: 0, page: null, heading_path: ["A"] } },
    ]);
  });

  it("rejects payloads without chunks/sufficient", () => {
    expect(parseChatEvent("retrieval", '{"chunks":[]}')).toBeNull();
    expect(parseChatEvent("retrieval", '{"sufficient":true}')).toBeNull();
    expect(parseChatEvent("retrieval", "not json")).toBeNull();
  });
});

describe("sendMessage with documents", () => {
  function sseResponse(chunks: string[]) {
    return new Response(streamFromChunks(chunks), { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  it("sends use_documents/document_ids and surfaces retrieval before tokens; done carries citations", async () => {
    const final = message({
      id: "a1",
      role: "assistant",
      content: "Use pip [S1].",
      grounding: "grounded",
      citations: [{ marker: "S1", chunk_id: "c1", document_id: "d1", document_title: "Handbook", excerpt: "Install with pip.", location: chunk().location, document_deleted: false }],
    });
    const fetchImpl = vi.fn<FetchLike>(async () =>
      sseResponse([
        sse("message.created", { user_message: message(), assistant_message_id: "a1" }),
        sse("retrieval", { chunks: [chunk()], sufficient: true }),
        sse("token", { delta: "Use pip [S1]." }),
        sse("done", { message: final, usage: { input_tokens: 1, output_tokens: 2 } }),
      ]),
    );
    const events: ChatStreamEvent["type"][] = [];
    const r = await new ApiClient({ fetch: fetchImpl }).sendMessage(CONV_ID, "how to install?", {
      useDocuments: true,
      documentIds: ["d1"],
      onEvent: (e) => events.push(e.type),
    });
    expect(JSON.parse(fetchImpl.mock.calls[0][1]!.body as string)).toEqual({ content: "how to install?", use_documents: true, document_ids: ["d1"] });
    expect(events).toEqual(["message.created", "retrieval", "token", "done"]);
    expect(r.outcome === "done" && r.message.citations?.[0].marker).toBe("S1");
  });

  it("keeps the Phase 1 body when documents are off, and omits empty document_ids", async () => {
    const done = () => sseResponse([sse("done", { message: message({ role: "assistant" }), usage: {} })]);
    const fetchImpl = vi.fn<FetchLike>(async () => done());
    const c = new ApiClient({ fetch: fetchImpl });
    await c.sendMessage(CONV_ID, "hi");
    await c.sendMessage(CONV_ID, "hi", { useDocuments: true, documentIds: [] });
    expect(JSON.parse(fetchImpl.mock.calls[0][1]!.body as string)).toEqual({ content: "hi" });
    expect(JSON.parse(fetchImpl.mock.calls[1][1]!.body as string)).toEqual({ content: "hi", use_documents: true });
  });
});

describe("uploadDocument (fetch path)", () => {
  const file = new File(["# Title\n\nbody"], "notes.md", { type: "text/markdown" });

  it("posts multipart with file + title and no manual Content-Type", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => Response.json(doc(), { status: 201 }));
    const r = await new ApiClient({ fetch: fetchImpl, xhr: null }).uploadDocument(file, { title: "  My notes " });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/v1/documents");
    expect(init?.method).toBe("POST");
    const body = init?.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("notes.md");
    expect(body.get("title")).toBe("My notes");
    expect(new Headers(init?.headers).get("content-type")).toBeNull();
    expect(r).toEqual({ document: doc(), duplicate: false, status: 201 });
  });

  it("detects X-Duplicate: true", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => Response.json(doc(), { status: 200, headers: { "X-Duplicate": "true" } }));
    const r = await new ApiClient({ fetch: fetchImpl, xhr: null }).uploadDocument(file);
    expect(r.duplicate).toBe(true);
    expect(r.status).toBe(200);
  });

  it("returns a failed document (201) as a result, not an error", async () => {
    const failed = doc({ status: "failed", error_code: "embedding_failed", chunk_count: 0 });
    const fetchImpl = vi.fn<FetchLike>(async () => Response.json(failed, { status: 201 }));
    const r = await new ApiClient({ fetch: fetchImpl, xhr: null }).uploadDocument(file);
    expect(r.document.status).toBe("failed");
  });

  it.each([
    ["unsupported_file_type", 415],
    ["file_too_large", 413],
    ["empty_document", 422],
    ["corrupt_document", 422],
  ])("maps a %s envelope (%i) to ApiError", async (code, status) => {
    const fetchImpl = vi.fn<FetchLike>(async () =>
      Response.json({ error: { code, message: "m", request_id: "rid-u" } }, { status }),
    );
    const err = await new ApiClient({ fetch: fetchImpl, xhr: null }).uploadDocument(file).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code, status, requestId: "rid-u", retryable: false });
  });

  it("maps a network failure to network_error", async () => {
    const fetchImpl = vi.fn<FetchLike>(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(new ApiClient({ fetch: fetchImpl, xhr: null }).uploadDocument(file)).rejects.toMatchObject({ code: "network_error" });
  });
});

describe("document endpoints", () => {
  it("hits the contract paths", async () => {
    const fetchImpl = vi.fn<FetchLike>(async (url) => {
      if (url.endsWith("/retrieval")) return Response.json({ chunks: [], cited_chunk_ids: [], top_k: 5, min_score: 0.3, embedding_model: "e5" });
      if (url === "/api/v1/documents") return Response.json({ items: [doc()] });
      return init204OrDoc(url);
    });
    function init204OrDoc(url: string) {
      return url.endsWith("/d1") ? new Response(null, { status: 204 }) : Response.json(doc());
    }
    const c = new ApiClient({ fetch: fetchImpl, xhr: null });
    expect(await c.listDocuments()).toEqual([doc()]);
    await c.retryDocument("d1");
    await c.deleteDocument("d1");
    await c.getChunk("d1", "c 1");
    expect((await c.getMessageRetrieval("m1")).top_k).toBe(5);
    expect(fetchImpl.mock.calls.map(([u, i]) => `${i?.method ?? "GET"} ${u}`)).toEqual([
      "GET /api/v1/documents",
      "POST /api/v1/documents/d1/retry",
      "DELETE /api/v1/documents/d1",
      "GET /api/v1/documents/d1/chunks/c%201",
      "GET /api/v1/messages/m1/retrieval",
    ]);
  });
});

describe("Phase 2 error-code mapping", () => {
  it.each(["unsupported_file_type", "file_too_large", "empty_document", "corrupt_document", "document_not_ready", "embedding_failed", "retrieval_not_configured"])(
    "%s has specific, non-generic copy",
    (code) => {
      const d = describeError({ code });
      expect(d.title).not.toBe("Something went wrong");
      expect(d.retryable).toBe(false);
    },
  );

  it("describes stored document failures with a retry hint", () => {
    expect(describeDocumentFailure("embedding_failed")).toMatchObject({ label: "Indexing failed", canRetry: true });
    expect(describeDocumentFailure("corrupt_document").canRetry).toBe(false);
    expect(describeDocumentFailure("empty_document").canRetry).toBe(false);
    expect(describeDocumentFailure(null).canRetry).toBe(true);
    expect(describeDocumentFailure("something_new").label).toBe("Something went wrong");
  });
});

describe("uploadDocument (XHR path with progress)", () => {
  class FakeXhr {
    static last: FakeXhr;
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null; onload: (() => void) | null } = { onprogress: null, onload: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    status = 0;
    statusText = "";
    responseText = "";
    headers: Record<string, string> = {};
    sent: unknown = null;
    opened: [string, string] | null = null;
    requestHeaders: Record<string, string> = {};
    constructor() {
      FakeXhr.last = this;
    }
    open(m: string, u: string) {
      this.opened = [m, u];
    }
    setRequestHeader(k: string, v: string) {
      this.requestHeaders[k] = v;
    }
    getAllResponseHeaders() {
      return Object.entries(this.headers).map(([k, v]) => `${k}: ${v}`).join("\r\n");
    }
    send(body: unknown) {
      this.sent = body;
    }
    abort() {
      this.onabort?.();
    }
    respond(status: number, body: unknown, headers: Record<string, string> = {}) {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      this.upload.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 });
      this.upload.onload?.();
      this.status = status;
      this.responseText = JSON.stringify(body);
      this.headers = { "content-type": "application/json", ...headers };
      this.onload?.();
    }
  }
  const file = new File(["hello"], "a.txt", { type: "text/plain" });
  const client = () => new ApiClient({ fetch: vi.fn<FetchLike>(), xhr: () => new FakeXhr() as unknown as XMLHttpRequest });

  it("reports progress, the processing phase and reads X-Duplicate from raw headers", async () => {
    const progress: number[] = [];
    const onUploaded = vi.fn();
    const p = client().uploadDocument(file, { onProgress: ({ loaded, total }) => progress.push(loaded / total), onUploaded });
    expect(FakeXhr.last.opened).toEqual(["POST", "/api/v1/documents"]);
    expect(FakeXhr.last.requestHeaders["X-Request-ID"]).toBeTruthy();
    FakeXhr.last.respond(200, doc(), { "x-duplicate": "true", "x-request-id": "r" });
    const r = await p;
    expect(progress).toEqual([0.5, 1]);
    expect(onUploaded).toHaveBeenCalledOnce();
    expect(r).toMatchObject({ duplicate: true, status: 200 });
  });

  it("maps error envelopes from XHR responses", async () => {
    const p = client().uploadDocument(file, { onProgress: () => {} });
    FakeXhr.last.respond(415, { error: { code: "unsupported_file_type", message: "nope", request_id: "r2" } });
    await expect(p).rejects.toMatchObject({ code: "unsupported_file_type", status: 415, requestId: "r2" });
  });

  it("rejects with AbortError when aborted", async () => {
    const ac = new AbortController();
    const p = client().uploadDocument(file, { onProgress: () => {}, signal: ac.signal });
    ac.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
});
