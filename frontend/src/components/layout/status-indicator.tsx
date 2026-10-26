"use client";

import { RefreshCwIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RequestId } from "@/components/chat/error-callout";
import { describeError } from "@/lib/api/errors";
import { isStubModel, type ReadyState } from "@/hooks/use-ready";
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "down" | "pending";

const DOT: Record<Tone, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  down: "bg-destructive",
  pending: "bg-muted-foreground/50 animate-pulse",
};

function Row({ label, value, tone }: { label: string; value: string; tone: Tone }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-2 font-medium">
        <span className={cn("size-2 rounded-full", DOT[tone])} aria-hidden />
        {value}
      </span>
    </div>
  );
}

export function StubModelBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("border-warning/40 bg-warning/10 text-foreground", className)}
      title="Responses come from a deterministic local stub, not a real language model"
    >
      Demo stub model
    </Badge>
  );
}

export function summarizeReady(state: ReadyState): { tone: Tone; label: string } {
  if (state.kind === "loading") return { tone: "pending", label: "Checking status…" };
  if (state.kind === "unreachable") return { tone: "down", label: "Backend unreachable" };
  const { data } = state;
  if (data.checks.database !== "ok") return { tone: "down", label: "Database unavailable" };
  if (data.checks.llm !== "configured") return { tone: "warn", label: "No model configured" };
  return { tone: "ok", label: data.llm?.model ?? "Ready" };
}

export function StatusIndicator({ state, onRefresh }: { state: ReadyState; onRefresh: () => void }) {
  const summary = summarizeReady(state);
  const data = state.kind === "ok" ? state.data : null;
  const stub = data ? isStubModel(data.llm?.model, data.llm?.provider) : false;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
          aria-label={`System status: ${summary.label}. Show details`}
        >
          <span className={cn("size-2 shrink-0 rounded-full", DOT[summary.tone])} aria-hidden />
          <span className="truncate">{summary.label}</span>
          {stub && <span className="shrink-0 rounded bg-warning/15 px-1 text-[10px] font-medium text-foreground">STUB</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-72">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold">System status</p>
          <Button variant="ghost" size="icon-xs" onClick={onRefresh} aria-label="Refresh status">
            <RefreshCwIcon />
          </Button>
        </div>
        <div className="mt-2 divide-y">
          <Row
            label="Backend"
            value={state.kind === "loading" ? "Checking" : state.kind === "unreachable" ? "Unreachable" : data?.status === "ready" ? "Ready" : "Not ready"}
            tone={state.kind === "loading" ? "pending" : state.kind === "unreachable" ? "down" : data?.status === "ready" ? "ok" : "warn"}
          />
          <Row
            label="Database"
            value={data ? (data.checks.database === "ok" ? "OK" : "Error") : "—"}
            tone={data ? (data.checks.database === "ok" ? "ok" : "down") : "pending"}
          />
          <Row
            label="Model"
            value={data ? (data.checks.llm === "configured" ? "Configured" : "Not configured") : "—"}
            tone={data ? (data.checks.llm === "configured" ? "ok" : "warn") : "pending"}
          />
        </div>
        {data?.llm && (
          <div className="mt-3 rounded-lg bg-muted/60 px-3 py-2 text-xs">
            <p className="text-muted-foreground">
              Provider <span className="font-medium text-foreground">{data.llm.provider}</span>
            </p>
            <p className="mt-0.5 font-mono break-all text-foreground">{data.llm.model}</p>
            {stub && <StubModelBadge className="mt-2" />}
          </div>
        )}
        {state.kind === "unreachable" && (
          <div className="mt-3 space-y-1 text-xs text-muted-foreground">
            <p>{describeError(state.error).description}</p>
            {state.error.requestId && <RequestId id={state.error.requestId} className="-ml-1" />}
          </div>
        )}
        {state.kind !== "loading" && (
          <p className="mt-3 text-[11px] text-muted-foreground">
            Checked {new Date(state.checkedAt).toLocaleTimeString("en-US")}
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}
