"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeftIcon, ClockIcon, RotateCwIcon, ShieldCheckIcon } from "lucide-react";
import { PageIntro } from "@/components/layout/page-intro";
import { PageShell, scrollPageToTop } from "@/components/layout/page-shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RelativeTime } from "@/components/ui/relative-time";
import { ErrorCallout } from "@/components/chat/error-callout";
import type { ActionProposal, ProposalStatus } from "@/lib/api/types";
import { effectiveStatus, formatCountdown } from "@/lib/approvals/state";
import { useNow, useProposalList } from "@/hooks/use-proposals";
import { cn } from "@/lib/utils";
import { ProposalPanel } from "./proposal-panel";
import { ProposalStatusBadge } from "./proposal-status";

const PARAM = "proposal";

const FILTERS: { value: ProposalStatus | null; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: null, label: "All" },
  { value: "executed", label: "Executed" },
  { value: "failed", label: "Failed" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
];

function syncUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set(PARAM, id);
  else url.searchParams.delete(PARAM);
  window.history.replaceState(window.history.state, "", url);
}


function ProposalRow({
  p,
  now,
  active,
  onSelect,
}: {
  p: ActionProposal;
  now: number;
  active: boolean;
  onSelect: () => void;
}) {
  const status = effectiveStatus(p, now);
  const countdown = status === "pending" ? formatCountdown(p.expires_at, now) : null;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      data-proposal-row={p.id}
      className={cn(
        "w-full space-y-1.5 rounded-lg border px-3 py-2 text-left transition-colors",
        "hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
        active ? "border-brand/40 bg-muted/60" : "bg-card",
      )}
    >
      <p className="line-clamp-2 text-sm leading-5 break-words">{p.arguments.title}</p>
      <div className="flex items-center gap-2">
        <ProposalStatusBadge status={status} />
        <code className="font-mono text-[11px] text-muted-foreground">{p.tool}</code>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground tabular-nums">
          {countdown ? (
            <>
              <ClockIcon className="size-3" aria-hidden /> {countdown}
            </>
          ) : (
            <RelativeTime iso={p.created_at} />
          )}
        </span>
      </div>
    </button>
  );
}

export function ApprovalsPage() {
  const [filter, setFilter] = useState<ProposalStatus | null>("pending");
  const list = useProposalList(filter);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const now = useNow(1000);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      const id = new URL(window.location.href).searchParams.get(PARAM);
      if (!cancelled && id) {
        setSelectedId(id);
        setFilter(null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const select = useCallback((id: string | null) => {
    setSelectedId(id);
    syncUrl(id);
    scrollPageToTop();
  }, []);

  const items = "items" in list.state ? list.state.items : [];
  // The server reads stale pending proposals back as expired; apply the same rule to what's on screen.
  const visible = filter === "pending" ? items.filter((p) => effectiveStatus(p, now) === "pending") : items;

  return (
    <PageShell
      title="Approvals"
      actions={
        <Button variant="ghost" size="icon-sm" aria-label="Refresh proposals" title="Refresh" onClick={() => void list.refresh()}>
          <RotateCwIcon />
        </Button>
      }
    >
      <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 md:px-6">
        <PageIntro
          className={cn(selectedId && "hidden lg:flex")}
          title="Approvals"
          description="Write actions drafted by the model wait here. Review the exact tool, target and arguments; nothing runs in the tracker until you approve it."
        />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,21rem)_minmax(0,1fr)]">
          {/* On narrow screens the list and the review are separate panes. */}
          <div className={cn("space-y-3", selectedId && "hidden lg:block")}>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by status">
              {FILTERS.map((f) => (
                <Button
                  key={f.label}
                  size="xs"
                  variant={filter === f.value ? "secondary" : "ghost"}
                  aria-pressed={filter === f.value}
                  onClick={() => setFilter(f.value)}
                >
                  {f.label}
                </Button>
              ))}
            </div>

            {list.state.status === "loading" ? (
              <div className="space-y-2" aria-busy="true" aria-label="Loading proposals">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : (
              <>
                {list.state.status === "error" && (
                  <ErrorCallout compact error={list.state.error} onRetry={() => void list.refresh()} />
                )}
                {visible.length === 0 && list.state.status === "ready" ? (
                  <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs leading-5 text-muted-foreground">
                    {filter === "pending" ? (
                      <>
                        Nothing is waiting for your review.
                        <br />
                        Propose a follow-up issue from a finished{" "}
                        <Link href="/reports" className="underline underline-offset-2">
                          report
                        </Link>
                        .
                      </>
                    ) : (
                      "No proposals with this status."
                    )}
                  </p>
                ) : (
                  <ul className="space-y-1" aria-label="Proposals">
                    {visible.map((p) => (
                      <li key={p.id}>
                        <ProposalRow p={p} now={now} active={p.id === selectedId} onSelect={() => select(p.id)} />
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          <section className={cn("min-w-0", !selectedId && "hidden lg:block")} aria-label="Proposal review">
            {selectedId && (
              <Button variant="ghost" size="sm" className="-ml-2 mb-3 lg:hidden" onClick={() => select(null)}>
                <ArrowLeftIcon /> All proposals
              </Button>
            )}
            {selectedId ? (
              <ProposalPanel key={selectedId} id={selectedId} onChanged={list.upsert} />
            ) : (
              <div className="flex h-full min-h-64 flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center">
                <ShieldCheckIcon className="size-6 text-muted-foreground" aria-hidden />
                <p className="text-sm font-medium">The approval boundary</p>
                <p className="max-w-md text-xs leading-5 text-muted-foreground">
                  The model can only <em>draft</em> a write action. Nothing is written to the tracker until a person
                  reviews the exact tool, target and arguments here and approves them. Approval is bound to the payload
                  hash you saw, and proposals expire after 15 minutes.
                </p>
              </div>
            )}
          </section>
        </div>
      </div>
    </PageShell>
  );
}
