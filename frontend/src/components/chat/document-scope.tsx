"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BookOpenIcon, CheckIcon, ChevronDownIcon, RotateCwIcon } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import type { Document } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type DocsState =
  | { status: "loading" }
  | { status: "ready"; items: Document[] }
  | { status: "error"; error: ApiError };

/** Ready documents for the composer scope picker. */
export function useReadyDocuments() {
  const [state, setState] = useState<DocsState>({ status: "loading" });
  const refresh = useCallback(async () => {
    try {
      const items = await api.listDocuments();
      setState({ status: "ready", items: items.filter((d) => d.status === "ready") });
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) }),
      });
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
  return { state, refresh };
}

export interface DocumentScopeValue {
  enabled: boolean;
  /** Empty = all ready documents. */
  documentIds: string[];
}

/**
 * "Use my documents" switch plus an optional scope picker over ready
 * documents. Lives in the composer toolbar.
 */
export function DocumentScope({
  value,
  onChange,
  disabled,
  docs,
  onRefreshDocs,
}: {
  value: DocumentScopeValue;
  onChange: (v: DocumentScopeValue) => void;
  disabled?: boolean;
  docs: DocsState;
  onRefreshDocs: () => void;
}) {
  const ready = docs.status === "ready" ? docs.items : [];
  const none = docs.status === "ready" && ready.length === 0;
  const selected = value.documentIds.filter((id) => ready.some((d) => d.id === id));
  const scopeLabel =
    selected.length === 0
      ? `All documents${ready.length ? ` (${ready.length})` : ""}`
      : selected.length === 1
        ? (ready.find((d) => d.id === selected[0])?.title ?? "1 document")
        : `${selected.length} documents`;

  const toggleDoc = (id: string) => {
    const set = new Set(selected);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onChange({ ...value, documentIds: [...set] });
  };

  return (
    <div className="flex min-w-0 items-center gap-1">
      <button
        type="button"
        role="switch"
        aria-checked={value.enabled}
        disabled={disabled || none}
        title={none ? "Upload documents first" : undefined}
        onClick={() => onChange({ ...value, enabled: !value.enabled })}
        className={cn(
          "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors",
          "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
          value.enabled
            ? "border-brand/40 bg-brand/10 text-foreground"
            : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <span
          className={cn(
            "relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors",
            value.enabled ? "bg-brand" : "bg-muted-foreground/30",
          )}
          aria-hidden
        >
          <span
            className={cn(
              "size-2.5 rounded-full bg-white shadow-sm transition-transform",
              value.enabled ? "translate-x-3" : "translate-x-0.5",
            )}
          />
        </span>
        <BookOpenIcon className="size-3.5" aria-hidden />
        Use my documents
      </button>

      {value.enabled && !none && (
        <Popover onOpenChange={(open) => open && onRefreshDocs()}>
          <PopoverTrigger asChild>
            <button
              type="button"
              disabled={disabled}
              aria-label={`Document scope: ${scopeLabel}`}
              className="inline-flex h-7 min-w-0 items-center gap-1 rounded-full px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50 aria-expanded:bg-muted"
            >
              <span className="max-w-[10rem] truncate">{scopeLabel}</span>
              <ChevronDownIcon className="size-3 shrink-0" aria-hidden />
            </button>
          </PopoverTrigger>
          <PopoverContent align="start" side="top" className="w-72 p-1.5">
            <p className="px-2 pt-1 pb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              Search in
            </p>
            {docs.status === "error" ? (
              <div className="flex items-center justify-between gap-2 px-2 py-2 text-xs">
                <span>{describeError(docs.error).title}</span>
                <Button size="xs" variant="outline" onClick={onRefreshDocs}>
                  <RotateCwIcon /> Retry
                </Button>
              </div>
            ) : (
              <ul className="scrollbar-thin max-h-64 overflow-y-auto" aria-label="Documents to search">
                <li>
                  <ScopeOption
                    checked={selected.length === 0}
                    onClick={() => onChange({ ...value, documentIds: [] })}
                    label="All ready documents"
                    hint={`${ready.length}`}
                    radio
                  />
                </li>
                {ready.map((d) => (
                  <li key={d.id}>
                    <ScopeOption
                      checked={selected.includes(d.id)}
                      onClick={() => toggleDoc(d.id)}
                      label={d.title}
                      hint={`${d.chunk_count}`}
                    />
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-1 border-t px-2 pt-1.5 pb-0.5">
              <Link href="/documents" className="text-xs text-muted-foreground hover:text-foreground">
                Manage documents →
              </Link>
            </div>
          </PopoverContent>
        </Popover>
      )}
      {none && (
        <Link
          href="/documents"
          aria-label="Upload documents"
          className="inline-flex h-7 shrink-0 items-center rounded-full px-2 text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          Upload<span className="hidden sm:inline">&nbsp;documents</span>
        </Link>
      )}
    </div>
  );
}

function ScopeOption({
  checked,
  onClick,
  label,
  hint,
  radio = false,
}: {
  checked: boolean;
  onClick: () => void;
  label: string;
  hint?: string;
  radio?: boolean;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
    >
      <span
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center border",
          radio ? "rounded-full" : "rounded",
          checked ? "border-brand bg-brand text-white" : "border-input",
        )}
        aria-hidden
      >
        {checked && <CheckIcon className="size-3" />}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{hint}</span>}
    </button>
  );
}
