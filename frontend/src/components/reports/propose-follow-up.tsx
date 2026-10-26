"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRightIcon, Loader2Icon, ShieldCheckIcon, SquarePenIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorCallout } from "@/components/chat/error-callout";
import { api } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { ActionProposal } from "@/lib/api/types";
import { effectiveStatus, notifyProposalsChanged, SingleFlight } from "@/lib/approvals/state";

type State =
  | { status: "idle" }
  | { status: "drafting" }
  | { status: "done"; proposal: ActionProposal; existing: boolean }
  | { status: "error"; error: ApiError };

export function proposalHref(id: string): string {
  return `/approvals?proposal=${encodeURIComponent(id)}`;
}

/**
 * "Propose follow-up issue" on a succeeded report. The server drafts a
 * create_issue proposal; nothing runs until a person approves it.
 */
export function ProposeFollowUp({ reportId }: { reportId: string }) {
  const [state, setState] = useState<State>({ status: "idle" });
  const guard = useRef(new SingleFlight<void>());

  // A pending proposal for this report may already exist (e.g. after a reload).
  useEffect(() => {
    const ctrl = new AbortController();
    void api
      .listProposals("pending", ctrl.signal)
      .then((items) => {
        const p = items.find((x) => x.source.id === reportId && effectiveStatus(x, Date.now()) === "pending");
        if (p && !ctrl.signal.aborted) setState((s) => (s.status === "idle" ? { status: "done", proposal: p, existing: true } : s));
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [reportId]);

  const propose = () =>
    guard.current.run(async () => {
      setState({ status: "drafting" });
      try {
        const { proposal, existing } = await api.createProposal(reportId);
        setState({ status: "done", proposal, existing });
        notifyProposalsChanged();
      } catch (err) {
        setState({
          status: "error",
          error: err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) }),
        });
      }
    });

  if (state.status === "done") {
    return (
      <div
        role="status"
        data-proposal-state={state.existing ? "existing" : "created"}
        className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm"
      >
        <ShieldCheckIcon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
        <div className="min-w-0 flex-1 basis-56">
          <p className="font-medium">
            {state.existing ? "A follow-up issue proposal is already waiting for review" : "Follow-up issue drafted — awaiting your review"}
          </p>
          <p className="text-xs text-muted-foreground">Nothing is created in the tracker until you approve it.</p>
        </div>
        <Button asChild size="sm">
          <Link href={proposalHref(state.proposal.id)}>
            Review proposal <ArrowRightIcon />
          </Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-card px-4 py-3">
        <div className="min-w-0 flex-1 basis-64">
          <p className="text-sm font-medium">Follow up in the tracker</p>
          <p className="text-xs text-muted-foreground">
            The model drafts a <code className="font-mono">create_issue</code> proposal for{" "}
            <code className="font-mono">local-tracker</code>. You review and approve it before anything is written.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={state.status === "drafting"}
          onClick={() => void propose()}
          data-action="propose"
        >
          {state.status === "drafting" ? <Loader2Icon className="animate-spin" /> : <SquarePenIcon />}
          {state.status === "drafting" ? "Drafting proposal…" : "Propose follow-up issue"}
        </Button>
      </div>
      {state.status === "error" && (
        <ErrorCallout error={state.error} onRetry={() => void propose()} onDismiss={() => setState({ status: "idle" })} />
      )}
    </div>
  );
}
