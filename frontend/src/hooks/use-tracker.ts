"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { isAbortError } from "@/lib/api/sse";
import { TRACKER_QUERY_MAX, type IssueList, type TrackerInfo } from "@/lib/api/types";

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
}

export type TrackerState =
  | { status: "loading" }
  | { status: "ready"; info: TrackerInfo }
  | { status: "error"; error: ApiError };

export function useTrackerInfo() {
  const [state, setState] = useState<TrackerState>({ status: "loading" });

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const info = await api.getTracker(signal);
      setState({ status: "ready", info });
    } catch (err) {
      if (isAbortError(err)) return;
      setState({ status: "error", error: toApiError(err) });
    }
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    void Promise.resolve().then(() => {
      if (!ctrl.signal.aborted) void refresh(ctrl.signal);
    });
    return () => ctrl.abort();
  }, [refresh]);

  return { state, refresh: () => refresh() };
}

/** Normalise a search box value into the `query` the API accepts (trimmed, ≤ 200). */
export function normalizeQuery(raw: string): string {
  return raw.trim().slice(0, TRACKER_QUERY_MAX);
}

export const SEARCH_DEBOUNCE_MS = 300;

export type IssuesState =
  | { status: "loading"; query: string; previous: IssueList | null }
  | { status: "ready"; query: string; data: IssueList }
  | { status: "error"; query: string; error: ApiError };

/**
 * Issue list for a (debounced) search box. Only the latest query's response is
 * applied; earlier requests are aborted.
 */
export function useTrackerIssues(rawQuery: string, limit = 20) {
  const [query, setQuery] = useState(() => normalizeQuery(rawQuery));
  const [state, setState] = useState<IssuesState>({ status: "loading", query, previous: null });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const next = normalizeQuery(rawQuery);
    const t = window.setTimeout(() => setQuery(next), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [rawQuery]);

  useEffect(() => {
    const ctrl = new AbortController();
    void Promise.resolve().then(async () => {
      if (ctrl.signal.aborted) return;
      setState((prev) => ({ status: "loading", query, previous: prev.status === "ready" ? prev.data : null }));
      try {
        const data = await api.listTrackerIssues({ query, limit }, ctrl.signal);
        if (!ctrl.signal.aborted) setState({ status: "ready", query, data });
      } catch (err) {
        if (isAbortError(err) || ctrl.signal.aborted) return;
        setState({ status: "error", query, error: toApiError(err) });
      }
    });
    return () => ctrl.abort();
  }, [query, limit, nonce]);

  return { state, query, retry: () => setNonce((n) => n + 1) };
}
