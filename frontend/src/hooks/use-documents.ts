"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { validateUploadFile } from "@/lib/api/upload";
import type { Document } from "@/lib/api/types";

export type DocumentListState =
  | { status: "loading" }
  | { status: "ready"; items: Document[] }
  | { status: "error"; error: ApiError; items: Document[] };

export type UploadPhase = "queued" | "uploading" | "processing" | "error";

export interface UploadItem {
  localId: string;
  file: File;
  phase: UploadPhase;
  /** 0..1 while uploading. */
  progress: number;
  error: ApiError | null;
}

export type UploadOutcome =
  | { kind: "created"; document: Document }
  | { kind: "duplicate"; document: Document }
  | { kind: "failed"; document: Document }
  | { kind: "rejected"; file: File; error: ApiError };

const POLL_MS = 3000;

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError({ code: "unknown_error", message: err instanceof Error ? err.message : "Unexpected error" });
}

function upsert(items: Document[], doc: Document): Document[] {
  const rest = items.filter((d) => d.id !== doc.id);
  return [doc, ...rest].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function isInFlight(d: Document): boolean {
  return d.status === "pending" || d.status === "processing";
}

let seq = 0;

export function useDocuments(opts: { onOutcome?: (o: UploadOutcome) => void } = {}) {
  const [list, setList] = useState<DocumentListState>({ status: "loading" });
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const queueRef = useRef<UploadItem[]>([]);
  const runningRef = useRef(false);
  const onOutcomeRef = useRef(opts.onOutcome);
  useEffect(() => {
    onOutcomeRef.current = opts.onOutcome;
  });

  const items = list.status === "loading" ? [] : list.items;

  const refresh = useCallback(async () => {
    try {
      const docs = await api.listDocuments();
      setList({ status: "ready", items: docs });
    } catch (err) {
      setList((prev) => ({
        status: "error",
        error: toApiError(err),
        items: prev.status === "loading" ? [] : prev.items,
      }));
    }
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

  // Documents ingested elsewhere (another tab, a slow server) may sit in
  // pending/processing: poll until they settle.
  const anyInFlight = items.some(isInFlight);
  useEffect(() => {
    if (!anyInFlight) return;
    const t = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(t);
  }, [anyInFlight, refresh]);

  const patchUpload = useCallback((localId: string, patch: Partial<UploadItem>) => {
    setUploads((u) => u.map((x) => (x.localId === localId ? { ...x, ...patch } : x)));
  }, []);

  const removeUpload = useCallback((localId: string) => {
    setUploads((u) => u.filter((x) => x.localId !== localId));
  }, []);

  const runQueue = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    try {
      while (queueRef.current.length) {
        const item = queueRef.current.shift()!;
        patchUpload(item.localId, { phase: "uploading", progress: 0 });
        try {
          const result = await api.uploadDocument(item.file, {
            onProgress: ({ loaded, total }) => patchUpload(item.localId, { progress: total ? loaded / total : 0 }),
            onUploaded: () => patchUpload(item.localId, { phase: "processing", progress: 1 }),
          });
          removeUpload(item.localId);
          setList((prev) => ({
            status: "ready",
            items: upsert(prev.status === "loading" ? [] : prev.items, result.document),
          }));
          onOutcomeRef.current?.(
            result.duplicate
              ? { kind: "duplicate", document: result.document }
              : result.document.status === "failed"
                ? { kind: "failed", document: result.document }
                : { kind: "created", document: result.document },
          );
        } catch (err) {
          const error = toApiError(err);
          patchUpload(item.localId, { phase: "error", error });
          onOutcomeRef.current?.({ kind: "rejected", file: item.file, error });
        }
      }
    } finally {
      runningRef.current = false;
    }
  }, [patchUpload, removeUpload]);

  /** Pre-check files, queue the valid ones; invalid ones show as errored rows. */
  const addFiles = useCallback(
    (files: Iterable<File>) => {
      const added: UploadItem[] = [];
      for (const file of files) {
        const localId = `up-${++seq}`;
        const v = validateUploadFile(file);
        if (!v.ok) {
          added.push({
            localId,
            file,
            phase: "error",
            progress: 0,
            error: new ApiError({ code: v.code, message: v.message }),
          });
          continue;
        }
        const item: UploadItem = { localId, file, phase: "queued", progress: 0, error: null };
        added.push(item);
        queueRef.current.push(item);
      }
      if (!added.length) return;
      setUploads((u) => [...added, ...u]);
      void runQueue();
    },
    [runQueue],
  );

  const retryUpload = useCallback(
    (localId: string) => {
      const item = uploads.find((x) => x.localId === localId);
      if (!item || item.phase !== "error" || !validateUploadFile(item.file).ok) return;
      const next: UploadItem = { ...item, phase: "queued", progress: 0, error: null };
      queueRef.current.push(next);
      setUploads((u) => u.map((x) => (x.localId === localId ? next : x)));
      void runQueue();
    },
    [runQueue, uploads],
  );

  const setBusy = useCallback((id: string, busy: boolean) => {
    setBusyIds((s) => {
      const n = new Set(s);
      if (busy) n.add(id);
      else n.delete(id);
      return n;
    });
  }, []);

  const retryDocument = useCallback(
    async (id: string): Promise<Document> => {
      setBusy(id, true);
      setList((prev) =>
        prev.status === "loading"
          ? prev
          : { ...prev, items: prev.items.map((d) => (d.id === id ? { ...d, status: "processing", error_code: null } : d)) },
      );
      try {
        const doc = await api.retryDocument(id);
        setList((prev) => ({ status: "ready", items: upsert(prev.status === "loading" ? [] : prev.items, doc) }));
        return doc;
      } catch (err) {
        void refresh();
        throw toApiError(err);
      } finally {
        setBusy(id, false);
      }
    },
    [refresh, setBusy],
  );

  const deleteDocument = useCallback(
    async (id: string) => {
      setBusy(id, true);
      try {
        await api.deleteDocument(id);
        setList((prev) => (prev.status === "loading" ? prev : { ...prev, items: prev.items.filter((d) => d.id !== id) }));
      } catch (err) {
        throw toApiError(err);
      } finally {
        setBusy(id, false);
      }
    },
    [setBusy],
  );

  return {
    list,
    documents: items,
    uploads,
    busyIds,
    refresh,
    addFiles,
    retryUpload,
    dismissUpload: removeUpload,
    retryDocument,
    deleteDocument,
  };
}
