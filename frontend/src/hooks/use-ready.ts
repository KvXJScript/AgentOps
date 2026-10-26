"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { ReadyResponse } from "@/lib/api/types";

export type ReadyState =
  | { kind: "loading" }
  | { kind: "ok"; data: ReadyResponse; checkedAt: number }
  | { kind: "unreachable"; error: ApiError; checkedAt: number };

const POLL_MS = 30_000;

export function useReady(): ReadyState & { refresh: () => void } {
  const [state, setState] = useState<ReadyState>({ kind: "loading" });

  const refresh = useCallback(() => {
    api
      .getReady()
      .then((data) => setState({ kind: "ok", data, checkedAt: Date.now() }))
      .catch((err: unknown) => {
        const error =
          err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
        setState({ kind: "unreachable", error, checkedAt: Date.now() });
      });
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  return { ...state, refresh };
}

export function isStubModel(model: string | undefined, provider?: string): boolean {
  return Boolean(model?.toLowerCase().startsWith("fake") || provider?.toLowerCase() === "fake");
}
