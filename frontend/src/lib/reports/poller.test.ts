import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { ReportRun, ReportRunStatus } from "@/lib/api/types";
import { isTerminal, pollRun } from "./poller";
import { reportRun } from "@/test/report-fixtures";

function runWith(status: ReportRunStatus): ReportRun {
  return reportRun({ status });
}

describe("pollRun", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("fetches immediately, then every interval until the run is terminal", async () => {
    const statuses: ReportRunStatus[] = ["queued", "running", "running", "succeeded"];
    const fetchRun = vi.fn(async () => runWith(statuses.shift() ?? "succeeded"));
    const onUpdate = vi.fn();

    pollRun("r1", { fetchRun, onUpdate, onError: vi.fn(), intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchRun).toHaveBeenCalledTimes(1);
    expect(fetchRun).toHaveBeenCalledWith("r1", expect.any(AbortSignal));

    await vi.advanceTimersByTimeAsync(999);
    expect(fetchRun).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchRun).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(5000);
    expect(onUpdate.mock.calls.map(([r]) => r.status)).toEqual(["queued", "running", "running", "succeeded"]);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchRun).toHaveBeenCalledTimes(4); // stopped at the terminal status
  });

  it("does not poll again when the first response is already terminal", async () => {
    const fetchRun = vi.fn(async () => runWith("insufficient_evidence"));
    pollRun("r1", { fetchRun, onUpdate: vi.fn(), onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchRun).toHaveBeenCalledTimes(1);
  });

  it("never overlaps requests: the next poll waits for a slow response", async () => {
    let resolve: (r: ReportRun) => void = () => {};
    const fetchRun = vi.fn(() => new Promise<ReportRun>((r) => (resolve = r)));
    pollRun("r1", { fetchRun, onUpdate: vi.fn(), onError: vi.fn(), intervalMs: 1000 });

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchRun).toHaveBeenCalledTimes(1);
    resolve(runWith("running"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchRun).toHaveBeenCalledTimes(2);
  });

  it("stop() cancels the timer, aborts the in-flight request and silences callbacks", async () => {
    let signal: AbortSignal | undefined;
    let resolve: (r: ReportRun) => void = () => {};
    const fetchRun = vi.fn((_id: string, s: AbortSignal) => {
      signal = s;
      return new Promise<ReportRun>((r) => (resolve = r));
    });
    const onUpdate = vi.fn();

    const stop = pollRun("r1", { fetchRun, onUpdate, onError: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    stop();
    expect(signal?.aborted).toBe(true);
    resolve(runWith("running"));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(onUpdate).not.toHaveBeenCalled();
    expect(fetchRun).toHaveBeenCalledTimes(1);
  });

  it("stop() between polls clears the pending timer (unmount)", async () => {
    const fetchRun = vi.fn(async () => runWith("running"));
    const stop = pollRun("r1", { fetchRun, onUpdate: vi.fn(), onError: vi.fn(), intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(500);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchRun).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("backs off on transient errors and recovers", async () => {
    const fetchRun = vi
      .fn<(id: string, s: AbortSignal) => Promise<ReportRun>>()
      .mockRejectedValueOnce(new ApiError({ code: "backend_unreachable", message: "down", requestId: "req-1" }))
      .mockRejectedValueOnce(new ApiError({ code: "backend_unreachable", message: "down" }))
      .mockResolvedValue(runWith("succeeded"));
    const onError = vi.fn();
    const onUpdate = vi.fn();

    pollRun("r1", { fetchRun, onUpdate, onError, intervalMs: 1000, maxBackoffMs: 8000 });
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].requestId).toBe("req-1");

    await vi.advanceTimersByTimeAsync(1999); // first backoff = 2 s
    expect(fetchRun).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchRun).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(4000); // second backoff = 4 s
    expect(fetchRun).toHaveBeenCalledTimes(3);
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
  });

  it("stops on not_found", async () => {
    const fetchRun = vi.fn(async () => {
      throw new ApiError({ code: "not_found", message: "Report not found" });
    });
    const onError = vi.fn();
    pollRun("r1", { fetchRun, onUpdate: vi.fn(), onError });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetchRun).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].code).toBe("not_found");
  });

  it("wraps unknown errors", async () => {
    const fetchRun = vi.fn(async () => {
      throw new Error("boom");
    });
    const onError = vi.fn();
    const stop = pollRun("r1", { fetchRun, onUpdate: vi.fn(), onError });
    await vi.advanceTimersByTimeAsync(0);
    stop();
    expect(onError.mock.calls[0][0]).toBeInstanceOf(ApiError);
    expect(onError.mock.calls[0][0].code).toBe("unknown_error");
  });
});

describe("isTerminal", () => {
  it.each([
    ["queued", false],
    ["running", false],
    ["succeeded", true],
    ["insufficient_evidence", true],
    ["failed", true],
  ] as const)("%s → %s", (status, expected) => {
    expect(isTerminal(status)).toBe(expected);
  });
});
