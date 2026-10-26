import { describe, expect, it } from "vitest";
import { SSEParser, isAbortError, readSSE, type SSEEvent } from "./sse";
import { streamFromChunks } from "@/test/streams";

function parseAll(chunks: string[]): SSEEvent[] {
  const p = new SSEParser();
  return chunks.flatMap((c) => p.feed(c));
}

async function collect(stream: ReadableStream<Uint8Array>, signal?: AbortSignal) {
  const out: SSEEvent[] = [];
  for await (const ev of readSSE(stream, signal)) out.push(ev);
  return out;
}

describe("SSEParser", () => {
  it("parses a simple event", () => {
    expect(parseAll(['event: token\ndata: {"delta":"hi"}\n\n'])).toEqual([
      { event: "token", data: '{"delta":"hi"}' },
    ]);
  });

  it("defaults the event type to 'message'", () => {
    expect(parseAll(["data: x\n\n"])).toEqual([{ event: "message", data: "x" }]);
  });

  it("joins multi-line data with newlines", () => {
    expect(parseAll(["data: line 1\ndata: line 2\ndata:\ndata:  indented\n\n"])).toEqual([
      { event: "message", data: "line 1\nline 2\n\n indented" },
    ]);
  });

  it("handles every possible split point of a multi-event payload", () => {
    const payload =
      'event: message.created\ndata: {"a":1}\n\n: keep-alive\n\nevent: token\ndata: {"delta":"x"}\n\nevent: done\ndata: {}\n\n';
    const expected = parseAll([payload]);
    expect(expected).toHaveLength(3);
    for (let i = 0; i <= payload.length; i++) {
      for (let j = i; j <= payload.length; j += 7) {
        expect(parseAll([payload.slice(0, i), payload.slice(i, j), payload.slice(j)])).toEqual(expected);
      }
    }
  });

  it("feeds one character at a time", () => {
    const payload = "event: token\r\ndata: a\r\ndata: b\r\n\r\n";
    expect(parseAll(payload.split(""))).toEqual([{ event: "token", data: "a\nb" }]);
  });

  it("supports CRLF, CR and LF line endings, including CRLF split across chunks", () => {
    expect(parseAll(["data: a\r", "\ndata: b\r", "\r", "data: c\rdata: d\r\r"])).toEqual([
      { event: "message", data: "a\nb" },
      { event: "message", data: "c\nd" },
    ]);
  });

  it("ignores comments, unknown fields and events without data", () => {
    expect(parseAll([": ping\n\nfoo: bar\nevent: token\n\nevent: x\ndata: 1\n\n"])).toEqual([
      { event: "x", data: "1" },
    ]);
  });

  it("does not leak the event type into the next event", () => {
    expect(parseAll(["event: token\ndata: 1\n\ndata: 2\n\n"])).toEqual([
      { event: "token", data: "1" },
      { event: "message", data: "2" },
    ]);
  });

  it("parses id and retry fields", () => {
    expect(parseAll(["id: 7\nretry: 1500\ndata: x\n\n"])).toEqual([
      { event: "message", data: "x", id: "7", retry: 1500 },
    ]);
  });

  it("treats a field with no colon as an empty value and keeps colons in values", () => {
    expect(parseAll(["data\ndata: a:b: c\n\n"])).toEqual([{ event: "message", data: "\na:b: c" }]);
  });

  it("strips a leading BOM", () => {
    expect(parseAll(["﻿data: x\n\n"])).toEqual([{ event: "message", data: "x" }]);
  });

  it("does not emit an unterminated trailing event", () => {
    const p = new SSEParser();
    expect(p.feed("event: token\ndata: partial")).toEqual([]);
    expect(p.flush()).toEqual([]);
  });
});

describe("readSSE", () => {
  it("reads events from a byte stream", async () => {
    const events = await collect(streamFromChunks(["event: token\nda", "ta: 1\n", "\nevent: done\ndata: 2\n\n"]));
    expect(events).toEqual([
      { event: "token", data: "1" },
      { event: "done", data: "2" },
    ]);
  });

  it("decodes multi-byte UTF-8 characters split across chunks", async () => {
    const bytes = new TextEncoder().encode('data: {"delta":"zażółć 🚀"}\n\n');
    const chunks: Uint8Array[] = [];
    for (let i = 0; i < bytes.length; i += 3) chunks.push(bytes.slice(i, i + 3));
    const events = await collect(streamFromChunks(chunks));
    expect(JSON.parse(events[0].data)).toEqual({ delta: "zażółć 🚀" });
  });

  it("yields events incrementally, before the stream ends", async () => {
    const stream = streamFromChunks(["data: first\n\n"], { holdOpen: true });
    const it = readSSE(stream);
    const first = await it.next();
    expect(first.value).toEqual({ event: "message", data: "first" });
    await it.return(undefined);
  });

  it("aborts a stream that is still open and cancels the reader", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("data: 1\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });
    const ac = new AbortController();
    const seen: string[] = [];
    const run = (async () => {
      for await (const ev of readSSE(stream, ac.signal)) {
        seen.push(ev.data);
        ac.abort();
      }
    })();
    const err = await run.catch((e: unknown) => e);
    expect(isAbortError(err)).toBe(true);
    expect(seen).toEqual(["1"]);
    expect(cancelled).toBe(true);
  });

  it("throws immediately for an already-aborted signal", async () => {
    const ac = new AbortController();
    ac.abort();
    const err = await collect(streamFromChunks(["data: 1\n\n"]), ac.signal).catch((e: unknown) => e);
    expect(isAbortError(err)).toBe(true);
  });

  it("cancels the source when the consumer breaks early", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("data: 1\n\ndata: 2\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });
    for await (const ev of readSSE(stream)) {
      expect(ev.data).toBe("1");
      break;
    }
    expect(cancelled).toBe(true);
  });
});
