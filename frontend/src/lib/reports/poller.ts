import { ApiError } from "@/lib/api/errors";
import { isAbortError } from "@/lib/api/sse";
import type { ReportRun, ReportRunStatus } from "@/lib/api/types";

export const POLL_INTERVAL_MS = 1000;
export const MAX_ERROR_BACKOFF_MS = 8000;

export function isTerminal(status: ReportRunStatus): boolean {
  return status === "succeeded" || status === "insufficient_evidence" || status === "failed";
}

export interface RunPollerOptions {
  fetchRun: (id: string, signal: AbortSignal) => Promise<ReportRun>;
  onUpdate: (run: ReportRun) => void;
  /** Called on every failed poll; polling continues (with backoff) unless the run is gone. */
  onError: (error: ApiError) => void;
  intervalMs?: number;
  maxBackoffMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

/**
 * Poll one report run until it reaches a terminal status.
 *
 * - The first fetch happens immediately; the next one is scheduled only after
 *   the previous finished (no overlapping requests).
 * - Transient errors back off exponentially (2×, 4×… up to `maxBackoffMs`);
 *   `not_found` stops polling.
 * - The returned `stop()` cancels the timer and aborts an in-flight request;
 *   no callback fires after it.
 */
export function pollRun(id: string, opts: RunPollerOptions): () => void {
  const interval = opts.intervalMs ?? POLL_INTERVAL_MS;
  const maxBackoff = opts.maxBackoffMs ?? MAX_ERROR_BACKOFF_MS;
  const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));

  let stopped = false;
  let timer: unknown = null;
  let controller: AbortController | null = null;
  let failures = 0;

  const schedule = (ms: number) => {
    if (stopped) return;
    timer = setTimer(() => {
      timer = null;
      void tick();
    }, ms);
  };

  const tick = async () => {
    if (stopped) return;
    controller = new AbortController();
    try {
      const run = await opts.fetchRun(id, controller.signal);
      if (stopped) return;
      failures = 0;
      opts.onUpdate(run);
      if (isTerminal(run.status)) {
        stopped = true;
        return;
      }
      schedule(interval);
    } catch (err) {
      if (stopped || isAbortError(err)) return;
      const error =
        err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
      opts.onError(error);
      if (error.code === "not_found") {
        stopped = true;
        return;
      }
      failures += 1;
      schedule(Math.min(maxBackoff, interval * 2 ** failures));
    } finally {
      controller = null;
    }
  };

  void tick();

  return () => {
    stopped = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
    controller?.abort();
  };
}
