"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { InfoIcon, LockIcon, RotateCwIcon, SearchIcon, ServerIcon, ShieldCheckIcon, XIcon } from "lucide-react";
import { PageIntro } from "@/components/layout/page-intro";
import { PageShell } from "@/components/layout/page-shell";
import { ErrorCallout } from "@/components/chat/error-callout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useTrackerInfo, useTrackerIssues } from "@/hooks/use-tracker";
import { TRACKER_QUERY_MAX, type ToolInfo, type TrackerInfo } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { IssueItem, ToolOutputFrame } from "./tool-output";

/** The honest label required by the contract: shown wherever the tracker is presented. */
export function DemoServerNotice({ className }: { className?: string }) {
  return (
    <p
      data-demo-notice=""
      className={cn(
        "flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/8 px-3 py-2 text-sm text-amber-900 dark:text-amber-200",
        className,
      )}
    >
      <InfoIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>
        <strong className="font-semibold">Local demo MCP server — not a real external service.</strong>{" "}
        <span className="text-amber-900/80 dark:text-amber-200/80">
          <code className="font-mono text-[12px]">local-tracker</code> is a small issue tracker that ships with AgentOps
          and runs as a subprocess of the backend. Issues live in a local file; nothing leaves this machine.
        </span>
      </span>
    </p>
  );
}

export function AccessBadge({ tool }: { tool: ToolInfo }) {
  return tool.access === "write" ? (
    <Badge className="bg-orange-500/12 font-mono text-orange-800 dark:text-orange-300" data-access="write">
      write
    </Badge>
  ) : (
    <Badge className="bg-sky-500/12 font-mono text-sky-800 dark:text-sky-300" data-access="read">
      read
    </Badge>
  );
}

export function ApprovalBadge({ required }: { required: boolean }) {
  return required ? (
    <Badge className="gap-1 bg-amber-500/15 text-amber-800 dark:text-amber-300" data-requires-approval="true">
      <LockIcon aria-hidden /> requires approval
    </Badge>
  ) : (
    <Badge variant="outline" className="text-muted-foreground" data-requires-approval="false">
      no approval
    </Badge>
  );
}

function ServerCard({ info, onRefresh }: { info: TrackerInfo; onRefresh: () => void }) {
  const { server, tools } = info;
  const connected = server.status === "connected";
  return (
    <section aria-label="MCP server" className="space-y-4 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex size-9 items-center justify-center rounded-lg border bg-muted/50">
          <ServerIcon className="size-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-mono text-base font-semibold">{server.name}</h2>
          <p className="text-xs text-muted-foreground">{server.label}</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Refresh server status" title="Refresh" onClick={onRefresh}>
          <RotateCwIcon />
        </Button>
      </div>

      <DemoServerNotice />

      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Transport</dt>
          <dd className="font-mono">{server.transport}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Status</dt>
          <dd>
            <StatusBadge data-server-status={server.status} tone={connected ? "success" : "danger"} className="gap-1.5">
              <span className={cn("size-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-destructive")} aria-hidden />
              {connected ? "connected" : "unavailable"}
            </StatusBadge>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Allowlisted tools</dt>
          <dd>{tools.length}</dd>
        </div>
      </dl>

      <div className="space-y-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Allowlisted tools</h3>
        <ul className="divide-y rounded-lg border" aria-label="Allowlisted tools">
          {tools.map((t) => (
            <li key={t.name} className="flex flex-wrap items-center gap-2 px-3 py-2.5" data-tool={t.name}>
              <code className="font-mono text-sm font-medium">{t.name}</code>
              <AccessBadge tool={t} />
              <ApprovalBadge required={t.requires_approval} />
              <p className="w-full text-xs leading-5 text-muted-foreground">{t.description}</p>
            </li>
          ))}
        </ul>
        <p className="flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
          <ShieldCheckIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            Any other tool the server advertises is ignored and can never be called. Write tools run only after a
            person approves the exact payload on the{" "}
            <Link href="/approvals" className="underline underline-offset-2 hover:text-foreground">
              Approvals
            </Link>{" "}
            page — never by the model.
          </span>
        </p>
      </div>
    </section>
  );
}

function Issues({ highlight }: { highlight: string | null }) {
  const [raw, setRaw] = useState("");
  const { state, query, retry } = useTrackerIssues(raw);
  const data = state.status === "ready" ? state.data : state.status === "loading" ? state.previous : null;
  const nearLimit = raw.length > TRACKER_QUERY_MAX - 20;

  return (
    <section aria-label="Issues" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Issues</h2>
        <span className="text-[11px] text-muted-foreground">
          read-only · via <code className="font-mono">{query ? "search_issues" : "list_issues"}</code>
        </span>
      </div>
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <input
          type="search"
          aria-label="Search issues"
          placeholder="Search issues…"
          value={raw}
          maxLength={TRACKER_QUERY_MAX}
          onChange={(e) => setRaw(e.target.value.slice(0, TRACKER_QUERY_MAX))}
          className="h-9 w-full rounded-lg border border-input bg-transparent pr-9 pl-8 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 [&::-webkit-search-cancel-button]:hidden"
        />
        {raw && (
          <Button
            variant="ghost"
            size="icon-xs"
            className="absolute top-1/2 right-1.5 -translate-y-1/2"
            aria-label="Clear search"
            onClick={() => setRaw("")}
          >
            <XIcon />
          </Button>
        )}
      </div>
      {nearLimit && (
        <p className="text-[11px] text-muted-foreground" aria-live="polite">
          {raw.length}/{TRACKER_QUERY_MAX} characters
        </p>
      )}

      {state.status === "error" && <ErrorCallout error={state.error} onRetry={retry} />}

      {!data && state.status === "loading" && (
        <div className="space-y-2" aria-busy="true" aria-label="Loading issues">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      )}

      {data && (
        <div className={cn("space-y-2", state.status === "loading" && "opacity-60")} aria-busy={state.status === "loading"}>
          {data.items.length === 0 ? (
            <p className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
              {query ? <>No issues match “{query}”.</> : "The tracker has no issues yet."}
            </p>
          ) : (
            <ToolOutputFrame source={`${query ? "search_issues" : "list_issues"} · local-tracker`}>
              <ul className="space-y-2 py-1" aria-label="Issue results">
                {data.items.map((issue) => (
                  <li key={issue.id}>
                    <IssueItem issue={issue} highlighted={issue.id === highlight} clamp={issue.id !== highlight} />
                  </li>
                ))}
              </ul>
            </ToolOutputFrame>
          )}
          {data.truncated && (
            <p role="status" data-truncated="" className="text-xs text-muted-foreground">
              Showing the first {data.items.length} results — there are more. Refine the search to narrow them down.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export function TrackerPage() {
  const { state, refresh } = useTrackerInfo();
  const [highlight, setHighlight] = useState<string | null>(null);

  // Deep link: /tracker?issue=LT-12 highlights that issue. Read after mount (like /reports?run=).
  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      const id = new URL(window.location.href).searchParams.get("issue");
      if (!cancelled && id) setHighlight(id);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <PageShell title="Tracker">
      <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 md:px-6">
        <PageIntro
          title="Tracker"
          description="A read-only view of the local demo issue tracker. Issues are created here only through an approved proposal."
        />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
          <div className="space-y-4">
            {state.status === "loading" && <Skeleton className="h-80 w-full" aria-label="Loading tracker" />}
            {state.status === "error" && (
              <div className="space-y-3">
                <DemoServerNotice />
                <ErrorCallout error={state.error} onRetry={() => void refresh()} />
              </div>
            )}
            {state.status === "ready" && <ServerCard info={state.info} onRefresh={() => void refresh()} />}
          </div>
          <Issues highlight={highlight} />
        </div>
      </div>
    </PageShell>
  );
}
