/**
 * Minimal, spec-aligned Server-Sent Events parser for fetch() streams.
 *
 * EventSource cannot POST, so the chat stream is read via fetch + ReadableStream
 * and fed through this parser. Follows the WHATWG "event stream interpretation"
 * rules: lines end in CRLF, LF or CR; `data` lines are joined with "\n";
 * comment lines (":") are ignored; an event is dispatched on a blank line.
 */

export interface SSEEvent {
  /** Event type; defaults to "message" when no `event:` field was sent. */
  event: string;
  data: string;
  id?: string;
  retry?: number;
}

export class SSEParser {
  private buffer = "";
  private eventType = "";
  private dataLines: string[] = [];
  private hasData = false;
  private lastId: string | undefined;
  private retry: number | undefined;
  private sawFirstChunk = false;
  /** A chunk ended in "\r"; a leading "\n" in the next chunk belongs to it. */
  private pendingCR = false;

  /** Feed decoded text; returns every event completed by this chunk. */
  feed(chunk: string): SSEEvent[] {
    if (!this.sawFirstChunk) {
      this.sawFirstChunk = true;
      if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1);
    }
    if (this.pendingCR) {
      this.pendingCR = false;
      if (chunk.startsWith("\n")) chunk = chunk.slice(1);
    }
    this.buffer += chunk;

    const events: SSEEvent[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      const line = this.buffer.slice(start, i);
      if (ch === "\r") {
        if (i + 1 < this.buffer.length) {
          if (this.buffer[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      start = i + 1;
      const ev = this.processLine(line);
      if (ev) events.push(ev);
    }
    this.buffer = this.buffer.slice(start);
    return events;
  }

  /**
   * Call at end of stream. Per spec an event without a terminating blank line
   * is discarded; we do the same so a truncated stream never yields a
   * half-received event.
   */
  flush(): SSEEvent[] {
    this.buffer = "";
    this.reset();
    return [];
  }

  private reset() {
    this.eventType = "";
    this.dataLines = [];
    this.hasData = false;
  }

  private processLine(line: string): SSEEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;

    const colon = line.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
    }

    switch (field) {
      case "event":
        this.eventType = value;
        break;
      case "data":
        this.dataLines.push(value);
        this.hasData = true;
        break;
      case "id":
        if (!value.includes("\0")) this.lastId = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
      default:
        break; // unknown fields are ignored
    }
    return null;
  }

  private dispatch(): SSEEvent | null {
    if (!this.hasData) {
      this.reset();
      return null;
    }
    const ev: SSEEvent = {
      event: this.eventType || "message",
      data: this.dataLines.join("\n"),
    };
    if (this.lastId !== undefined) ev.id = this.lastId;
    if (this.retry !== undefined) ev.retry = this.retry;
    this.reset();
    return ev;
  }
}

/**
 * Read an SSE byte stream and yield parsed events. Uses a streaming
 * TextDecoder so multi-byte UTF-8 characters split across chunks survive.
 * Aborting `signal` cancels the underlying reader and throws an AbortError.
 */
export async function* readSSE(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SSEParser();

  const onAbort = () => {
    reader.cancel(signal?.reason).catch(() => {});
  };
  if (signal) {
    if (signal.aborted) {
      onAbort();
      throw abortError(signal);
    }
    signal.addEventListener("abort", onAbort, { once: true });
  }

  let finished = false;
  try {
    while (true) {
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (err) {
        if (signal?.aborted) throw abortError(signal);
        throw err;
      }
      if (signal?.aborted) throw abortError(signal);
      if (result.done) {
        finished = true;
        yield* parser.feed(decoder.decode());
        parser.flush();
        return;
      }
      yield* parser.feed(decoder.decode(result.value, { stream: true }));
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // Consumer stopped early (break / throw / abort): cancel so the upstream
    // connection is closed instead of being drained in the background.
    if (!finished) reader.cancel().catch(() => {});
    else reader.releaseLock();
  }
}

function abortError(signal: AbortSignal): Error {
  const reason: unknown = signal.reason;
  if (reason instanceof Error && reason.name === "AbortError") return reason;
  return new DOMException("The operation was aborted.", "AbortError");
}

export function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "name" in err &&
    (err as { name: unknown }).name === "AbortError"
  );
}
