"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { ReportRun } from "@/lib/api/types";
import { pollRun } from "@/lib/reports/poller";

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
}

export type ReportListState =
  | { status: "loading" }
  | { status: "ready"; items: ReportRun[] }
  | { status: "error"; error: ApiError; items: ReportRun[] };

/** Past runs (newest first). `upsert` merges a run seen elsewhere (create, detail polling). */
export function useReportList() {
  const [state, setState] = useState<ReportListState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const items = await api.listReports();
      setState({ status: "ready", items });
    } catch (err) {
      setState((prev) => ({ status: "error", error: toApiError(err), items: "items" in prev ? prev.items : [] }));
    }
  }, []);

  const upsert = useCallback((run: ReportRun) => {
    const { report: _report, ...summary } = run;
    setState((prev) => {
      const items = "items" in prev ? prev.items : [];
      const exists = items.some((r) => r.id === run.id);
      const next = exists ? items.map((r) => (r.id === run.id ? summary : r)) : [summary, ...items];
      return prev.status === "error" ? { ...prev, items: next } : { status: "ready", items: next };
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) void refresh();
    });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  return { state, refresh, upsert };
}

interface RunState {
  id: string | null;
  run: ReportRun | null;
  error: ApiError | null;
}

/**
 * Live view of one run: polls ~1 s while queued/running, stops when the run
 * is terminal, the id changes, or the component unmounts.
 */
export function useReportRun(id: string | null, onUpdate?: (run: ReportRun) => void) {
  const [state, setState] = useState<RunState>({ id: null, run: null, error: null });
  const onUpdateRef = useRef(onUpdate);
  useEffect(() => {
    onUpdateRef.current = onUpdate;
  }, [onUpdate]);

  useEffect(() => {
    if (!id) return;
    return pollRun(id, {
      fetchRun: (runId, signal) => api.getReport(runId, signal),
      onUpdate: (run) => {
        setState({ id, run, error: null });
        onUpdateRef.current?.(run);
      },
      onError: (error) => setState((prev) => ({ id, run: prev.id === id ? prev.run : null, error })),
    });
  }, [id]);

  // State from a previous id is never shown for the new one.
  const current = state.id === id ? state : { id, run: null, error: null };
  return { run: current.run, error: current.error };
}
