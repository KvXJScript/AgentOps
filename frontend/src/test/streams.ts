/** Test helpers: build byte streams from string chunks, optionally with delays. */
const enc = new TextEncoder();

export function streamFromChunks(
  chunks: Array<string | Uint8Array>,
  opts: { delayMs?: number; holdOpen?: boolean } = {},
): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs));
      if (i < chunks.length) {
        const c = chunks[i++];
        controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
        return;
      }
      if (!opts.holdOpen) controller.close();
      else await new Promise(() => {}); // never resolves: simulates an idle open stream
    },
  });
}

export function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
