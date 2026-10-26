"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertCircleIcon } from "lucide-react";
import { ErrorCallout, RequestId } from "@/components/chat/error-callout";
import { PlainText } from "@/components/tracker/tool-output";
import { api } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import type { ActionProposal } from "@/lib/api/types";
import { submitDecision, type DecisionKind } from "@/lib/approvals/decide";
import {
  decisionOutcome,
  effectiveStatus,
  notifyProposalsChanged,
  shortHash,
  SingleFlight,
} from "@/lib/approvals/state";
import { useNow, useProposal } from "@/hooks/use-proposals";
import { ProposalReview, type DecisionBanner } from "./proposal-review";

interface Confirm {
  kind: DecisionKind;
  /** The proposal exactly as the reviewer saw it when they opened the dialog. */
  snapshot: ActionProposal;
}

export function ProposalPanel({ id, onChanged }: { id: string; onChanged?: (p: ActionProposal) => void }) {
  const detail = useProposal(id);
  const now = useNow(1000);
  const guard = useRef(new SingleFlight<void>());
  const [busy, setBusy] = useState<DecisionKind | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [banner, setBanner] = useState<DecisionBanner | null>(null);
  // Banners belong to the proposal they were raised on.
  const [bannerFor, setBannerFor] = useState<string | null>(null);
  const shownBanner = bannerFor === id ? banner : null;

  const decide = (c: Confirm) =>
    guard.current.run(async () => {
      setBusy(c.kind);
      try {
        const updated = await submitDecision(api, c.kind, c.snapshot);
        detail.set(updated);
        onChanged?.(updated);
        setBanner(null);
        if (c.kind === "approve") {
          if (updated.status === "executed") toast.success(`Issue ${updated.result?.issue_id ?? ""} created in local-tracker`);
          else if (updated.status === "failed") toast.error("The approved tool call failed", { description: updated.error_code ?? undefined });
        } else {
          toast("Proposal rejected — nothing was executed");
        }
      } catch (err) {
        const e = err instanceof ApiError ? err : new ApiError({ code: "unknown_error", message: String(err) });
        setBanner({ outcome: decisionOutcome(e), error: e, hash: c.snapshot.payload_hash });
        setBannerFor(c.snapshot.id);
        // The state moved on server-side: show it. A payload mismatch is NOT auto-reloaded —
        // the reviewer must reload deliberately and look again.
        if (decisionOutcome(e).kind === "refresh") detail.reload();
      } finally {
        notifyProposalsChanged();
        setBusy(null);
        setConfirm(null);
        // The decision buttons are gone or disabled now: move keyboard focus to the outcome
        // (or the error banner) once the dialog has handed focus back.
        window.setTimeout(() => {
          (document.querySelector<HTMLElement>("[data-banner]") ?? document.querySelector<HTMLElement>("[data-outcome]"))?.focus();
        }, 50);
      }
    });

  if (!detail.proposal) {
    if (detail.error) return <ErrorCallout error={detail.error} onRetry={detail.reload} />;
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading proposal">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const proposal = detail.proposal;
  const snap = confirm?.snapshot ?? null;
  const snapExpired = snap ? effectiveStatus(snap, now) !== "pending" : false;

  return (
    <>
      {detail.error && (
        <p role="alert" className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <AlertCircleIcon className="size-3.5 text-destructive" aria-hidden />
          Couldn&apos;t refresh this proposal ({describeError(detail.error).title}).
          {detail.error.requestId && <RequestId id={detail.error.requestId} />}
        </p>
      )}
      <ProposalReview
        proposal={proposal}
        now={now}
        busy={busy}
        banner={shownBanner}
        reloading={detail.loading}
        onApprove={() => !guard.current.busy && setConfirm({ kind: "approve", snapshot: proposal })}
        onReject={() => !guard.current.busy && setConfirm({ kind: "reject", snapshot: proposal })}
        onReload={detail.reload}
      />

      <AlertDialog open={confirm !== null} onOpenChange={(open) => !open && busy === null && setConfirm(null)}>
        <AlertDialogContent className="sm:max-w-lg">
          {snap && confirm?.kind === "approve" && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>Approve and run create_issue?</AlertDialogTitle>
                <AlertDialogDescription>
                  You are approving one call of <code className="font-mono">create_issue</code> on{" "}
                  <code className="font-mono">local-tracker</code> (the local demo MCP server) with exactly these
                  arguments. It runs once and can&apos;t be undone from AgentOps.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <dl className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm" data-confirm="approve">
                <div>
                  <dt className="text-[11px] text-muted-foreground">title</dt>
                  <dd>
                    <PlainText text={snap.arguments.title} className="text-sm font-medium text-foreground" />
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">labels</dt>
                  <dd className="flex flex-wrap gap-1">
                    {snap.arguments.labels.length
                      ? snap.arguments.labels.map((l) => (
                          <Badge key={l} variant="outline" className="font-mono">
                            {l}
                          </Badge>
                        ))
                      : "none"}
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">body</dt>
                  <dd className="text-xs text-muted-foreground">{snap.arguments.body.length} characters (shown in full on the review screen)</dd>
                </div>
                <div>
                  <dt className="text-[11px] text-muted-foreground">payload_hash sent with your approval</dt>
                  <dd className="font-mono text-xs break-all" data-confirm-hash={snap.payload_hash}>
                    {snap.payload_hash}
                  </dd>
                </div>
              </dl>
              {snapExpired && (
                <p role="alert" className="text-sm text-destructive">
                  This proposal expired while the dialog was open. It can no longer be approved.
                </p>
              )}
              <AlertDialogFooter>
                <AlertDialogCancel disabled={busy !== null}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  disabled={busy !== null || snapExpired}
                  data-action="confirm-approve"
                  onClick={(e) => {
                    e.preventDefault();
                    void decide(confirm);
                  }}
                >
                  {busy === "approve" ? "Approving…" : `Approve and create issue (${shortHash(snap.payload_hash, 8)})`}
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
          {snap && confirm?.kind === "reject" && (
            <>
              <AlertDialogHeader>
                <AlertDialogTitle>Reject this proposal?</AlertDialogTitle>
                <AlertDialogDescription>
                  Nothing will be created in local-tracker. A rejected proposal can&apos;t be approved later; propose
                  again from the report if needed.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={busy !== null}>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  disabled={busy !== null}
                  data-action="confirm-reject"
                  onClick={(e) => {
                    e.preventDefault();
                    void decide(confirm);
                  }}
                >
                  {busy === "reject" ? "Rejecting…" : "Reject proposal"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </>
          )}
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
