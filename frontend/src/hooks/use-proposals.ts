"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { isAbortError } from "@/lib/api/sse";
import type { ActionProposal, ProposalStatus } from "@/lib/api/types";
import { PROPOSALS_CHANGED_EVENT } from "@/lib/approvals/state";

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
}

/**
 * Wall clock that ticks every `intervalMs` (for countdowns / expiry by clock).
 * Read after mount only (prerendering must not capture the build-time clock);
 * proposals are fetched after mount too, so nothing is shown against `0`.
 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    void Promise.resolve().then(() => setNow(Date.now()));
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** Re-run `fn` whenever another part of the app reports a proposal change. */
function useProposalsChanged(fn: () => void) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  }, [fn]);
  useEffect(() => {
    const on = () => ref.current();
    window.addEventListener(PROPOSALS_CHANGED_EVENT, on);
    return () => window.removeEventListener(PROPOSALS_CHANGED_EVENT, on);
  }, []);
}

export type ProposalListState =
  | { status: "loading" }
  | { status: "ready"; items: ActionProposal[] }
  | { status: "error"; error: ApiError; items: ActionProposal[] };

export function useProposalList(filter: ProposalStatus | null) {
  const [state, setState] = useState<ProposalListState>({ status: "loading" });
  const ctrlRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    try {
      const items = await api.listProposals(filter, ctrl.signal);
      if (!ctrl.signal.aborted) setState({ status: "ready", items });
    } catch (err) {
      if (isAbortError(err) || ctrl.signal.aborted) return;
      setState((prev) => ({ status: "error", error: toApiError(err), items: "items" in prev ? prev.items : [] }));
    }
  }, [filter]);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      setState({ status: "loading" });
      void refresh();
    });
    return () => {
      cancelled = true;
      ctrlRef.current?.abort();
    };
  }, [refresh]);

  useProposalsChanged(() => void refresh());

  /** Merge a proposal seen elsewhere (detail view, decision result). */
  const upsert = useCallback((p: ActionProposal) => {
    setState((prev) => {
      if (!("items" in prev)) return prev;
      const items = prev.items.some((x) => x.id === p.id) ? prev.items.map((x) => (x.id === p.id ? p : x)) : prev.items;
      return { ...prev, items };
    });
  }, []);

  return { state, refresh, upsert };
}

export interface ProposalDetailState {
  id: string | null;
  proposal: ActionProposal | null;
  error: ApiError | null;
  loading: boolean;
}

/** One proposal. Polls ~1 s while it is `executing`; `reload` refetches on demand. */
export function useProposal(id: string | null) {
  const [state, setState] = useState<ProposalDetailState>({ id: null, proposal: null, error: null, loading: false });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!id) return;
    const ctrl = new AbortController();
    let timer: number | undefined;
    const load = async () => {
      setState((prev) => ({ id, proposal: prev.id === id ? prev.proposal : null, error: null, loading: true }));
      try {
        const proposal = await api.getProposal(id, ctrl.signal);
        if (ctrl.signal.aborted) return;
        setState({ id, proposal, error: null, loading: false });
        if (proposal.status === "executing") timer = window.setTimeout(() => void load(), 1000);
      } catch (err) {
        if (isAbortError(err) || ctrl.signal.aborted) return;
        setState((prev) => ({ id, proposal: prev.id === id ? prev.proposal : null, error: toApiError(err), loading: false }));
      }
    };
    void Promise.resolve().then(() => {
      if (!ctrl.signal.aborted) void load();
    });
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [id, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  /** Apply a proposal returned by approve/reject without a round trip. */
  const set = useCallback((proposal: ActionProposal) => {
    setState({ id: proposal.id, proposal, error: null, loading: false });
  }, []);

  const current = state.id === id ? state : { id, proposal: null, error: null, loading: true };
  return { ...current, reload, set };
}

/** Count of proposals awaiting review, for the nav badge. Silent on errors. */
export function usePendingProposalCount(pollMs = 20_000): number | null {
  const [count, setCount] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const items = await api.listProposals("pending");
      const now = Date.now();
      setCount(items.filter((p) => Date.parse(p.expires_at) > now).length);
    } catch {
      setCount(null);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => void refresh());
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, pollMs);
    return () => window.clearInterval(t);
  }, [refresh, pollMs]);

  useProposalsChanged(() => void refresh());
  return count;
}
