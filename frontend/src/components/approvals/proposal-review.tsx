"use client";

import { useState } from "react";
import Link from "next/link";
import {
  AlertCircleIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  CheckIcon,
  ChevronRightIcon,
  ClockIcon,
  CopyIcon,
  FileSearchIcon,
  Loader2Icon,
  LockIcon,
  RotateCwIcon,
  ServerIcon,
  ShieldCheckIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RequestId } from "@/components/chat/error-callout";
import { IssueItem, PlainText, ToolOutputFrame } from "@/components/tracker/tool-output";
import { describeError, type ApiError } from "@/lib/api/errors";
import type { ActionProposal } from "@/lib/api/types";
import {
  formatCountdown,
  payloadPreview,
  proposalUiState,
  shortHash,
  type DecisionOutcome,
} from "@/lib/approvals/state";
import type { DecisionKind } from "@/lib/approvals/decide";
import { cn } from "@/lib/utils";
import { RelativeTime } from "@/components/ui/relative-time";
import { ProposalStatusBadge, TONE_CLASSES } from "./proposal-status";

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function CopyHash({ hash }: { hash: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      data-payload-hash={hash}
      title={hash}
      aria-label={copied ? "Payload hash copied" : `Copy full payload hash ${hash}`}
      className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 font-mono text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
      onClick={() =>
        void navigator.clipboard?.writeText(hash).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })
      }
    >
      <span>{shortHash(hash)}</span>
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
      <span className="font-sans text-[11px] text-muted-foreground">{copied ? "Copied" : "Copy full"}</span>
    </button>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {hint && <span className="text-[11px] text-muted-foreground/80">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

/** Exactly what the approval would execute. Every argument is plain text. */
function ExecutionCard({ proposal }: { proposal: ActionProposal }) {
  const args = proposal.arguments;
  return (
    <section aria-label="What will execute" data-section="execution" className="overflow-hidden rounded-xl border-2 bg-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b bg-muted/40 px-4 py-3">
        <h3 className="mr-auto text-xs font-semibold tracking-wide uppercase">What will execute</h3>
        <span className="inline-flex items-center gap-1.5 text-sm">
          <WrenchIcon className="size-3.5 text-muted-foreground" aria-hidden />
          <code className="font-mono font-medium" data-tool-name>
            {proposal.tool}
          </code>
          <Badge className="bg-orange-500/12 font-mono text-orange-800 dark:text-orange-300">write</Badge>
        </span>
        <ArrowRightIcon className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="inline-flex items-center gap-1.5 text-sm">
          <ServerIcon className="size-3.5 text-muted-foreground" aria-hidden />
          <code className="font-mono font-medium" data-target>
            {proposal.target}
          </code>
          <span className="text-[11px] text-muted-foreground">(local demo MCP server)</span>
        </span>
      </div>

      <div className="space-y-4 px-4 py-4">
        <p className="text-[11px] leading-4 text-muted-foreground">
          Tool and target are fixed by the server. Title, body and labels were drafted by the model and are shown
          exactly as they will be sent — as plain text, nothing rendered.
        </p>
        <Field label="title" hint={`${args.title.length} chars`}>
          <div className="rounded-md border bg-background px-3 py-2" data-arg="title">
            <PlainText text={args.title} className="text-sm font-medium text-foreground" />
          </div>
        </Field>
        <Field label="labels" hint={`${args.labels.length} of max 3`}>
          <div className="flex flex-wrap gap-1.5" data-arg="labels">
            {args.labels.length ? (
              args.labels.map((l) => (
                <Badge key={l} variant="outline" className="font-mono">
                  {l}
                </Badge>
              ))
            ) : (
              <span className="text-xs text-muted-foreground">none</span>
            )}
          </div>
        </Field>
        <Field label="body" hint={`${args.body.length} / 4000 chars`}>
          <div className="scrollbar-thin max-h-80 overflow-y-auto rounded-md border bg-background px-3 py-2" data-arg="body">
            {args.body ? (
              <PlainText text={args.body} />
            ) : (
              <span className="text-xs text-muted-foreground">(empty)</span>
            )}
          </div>
        </Field>

        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          <span className="text-xs font-medium text-muted-foreground">payload_hash</span>
          <CopyHash hash={proposal.payload_hash} />
          <span className="text-[11px] text-muted-foreground">sha256 of the canonical {"{tool, target, arguments}"}</span>
        </div>
        <details className="group text-xs">
          <summary className="-mx-1 flex min-h-8 cursor-pointer list-none items-center gap-1 rounded px-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none">
            <ChevronRightIcon className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
            Exact payload (JSON)
          </summary>
          <pre className="scrollbar-thin mt-2 max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-[12px] leading-5 whitespace-pre-wrap break-words">
            {payloadPreview(proposal)}
          </pre>
          <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{proposal.payload_hash}</p>
        </details>
      </div>
    </section>
  );
}

function RelatedIssues({ proposal }: { proposal: ActionProposal }) {
  const n = proposal.related_issues.length;
  return (
    <details className="group rounded-xl border bg-card" data-section="related">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm">
        <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden />
        <span className="font-medium">Related issues ({n})</span>
        <span className="text-xs text-muted-foreground">read while drafting · untrusted, display only</span>
      </summary>
      <div className="space-y-2 border-t px-4 py-3">
        <p className="text-xs leading-5 text-muted-foreground">
          The model saw these issues (via <code className="font-mono">search_issues</code>) as untrusted evidence. They
          can&apos;t change the tool, the target or this decision. Text is shown literally.
        </p>
        {n === 0 ? (
          <p className="text-xs text-muted-foreground">None found.</p>
        ) : (
          <ToolOutputFrame source="search_issues · local-tracker">
            <ul className="space-y-2 py-1">
              {proposal.related_issues.map((issue) => (
                <li key={issue.id}>
                  <IssueItem issue={issue} />
                </li>
              ))}
            </ul>
          </ToolOutputFrame>
        )}
      </div>
    </details>
  );
}

function Outcome({ proposal, now }: { proposal: ActionProposal; now: number }) {
  const ui = proposalUiState(proposal, now);
  if (ui.status === "executed") {
    return (
      <div role="status" data-outcome="executed" tabIndex={-1} className="outline-none flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-4 py-3 text-sm">
        <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">
            {proposal.result ? (
              <>
                Issue <span className="font-mono" data-result-issue>{proposal.result.issue_id}</span> created in local-tracker
              </>
            ) : (
              "Executed"
            )}
          </p>
          <p className="text-muted-foreground">Approved by you at {when(proposal.decided_at)}. The tool ran once.</p>
          {proposal.result && (
            <Link
              href={`/tracker?issue=${encodeURIComponent(proposal.result.issue_id)}`}
              className="inline-flex items-center gap-1 text-sm font-medium underline underline-offset-2"
            >
              Open in Tracker <ArrowRightIcon className="size-3.5" aria-hidden />
            </Link>
          )}
        </div>
      </div>
    );
  }
  if (ui.status === "failed") {
    const d = describeError({ code: proposal.error_code ?? "internal_error" });
    return (
      <div role="alert" data-outcome="failed" tabIndex={-1} className="outline-none flex items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm">
        <AlertCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">The approved tool call failed: {d.title}</p>
          <p className="text-muted-foreground">{d.description}</p>
          <p className="font-mono text-[11px] text-muted-foreground">error_code: {proposal.error_code ?? "unknown"}</p>
        </div>
      </div>
    );
  }
  if (ui.status === "rejected" || ui.status === "expired") {
    return (
      <div role="status" data-outcome={ui.status} tabIndex={-1} className="outline-none flex items-start gap-3 rounded-xl border bg-muted/30 px-4 py-3 text-sm">
        {ui.status === "rejected" ? (
          <XCircleIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        ) : (
          <ClockIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <div className="space-y-1">
          <p className="font-medium">{ui.status === "rejected" ? "Rejected — nothing was executed" : "Expired — nothing was executed"}</p>
          <p className="text-muted-foreground">
            {ui.status === "rejected"
              ? `Rejected at ${when(proposal.decided_at)}.`
              : `Expired at ${when(proposal.expires_at)}. Propose it again from the report if it's still needed.`}
          </p>
        </div>
      </div>
    );
  }
  if (ui.status === "executing") {
    return (
      <p role="status" data-outcome="executing" tabIndex={-1} className="outline-none flex items-center gap-2 rounded-xl border px-4 py-3 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden /> Running create_issue on local-tracker…
      </p>
    );
  }
  return null;
}

export interface DecisionBanner {
  outcome: DecisionOutcome;
  error: ApiError;
  /** payload_hash of the snapshot the failed decision was about. */
  hash: string;
}

export interface ProposalReviewProps {
  proposal: ActionProposal;
  now: number;
  /** Which decision request is in flight (buttons are disabled while set). */
  busy: DecisionKind | null;
  banner: DecisionBanner | null;
  onApprove: () => void;
  onReject: () => void;
  onReload: () => void;
  reloading?: boolean;
}

/** The review screen for one proposal (presentational; decisions go through callbacks). */
export function ProposalReview({ proposal, now, busy, banner, onApprove, onReject, onReload, reloading = false }: ProposalReviewProps) {
  const ui = proposalUiState(proposal, now);
  const countdown = ui.status === "pending" ? formatCountdown(proposal.expires_at, now) : null;
  // A payload_mismatch banner stays until a reload brings a different payload to review.
  const shown = banner && !(banner.outcome.kind === "stale" && banner.hash !== proposal.payload_hash) ? banner : null;
  const stale = shown?.outcome.kind === "stale";
  const decideDisabled = !ui.canDecide || busy !== null || stale || reloading;

  return (
    <div className="space-y-5" data-proposal-id={proposal.id} data-ui-status={ui.status}>
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <ProposalStatusBadge status={ui.status} />
          {countdown && (
            <span
              className={cn(
                "inline-flex items-center gap-1 font-mono text-xs tabular-nums",
                Date.parse(proposal.expires_at) - now < 60_000 ? "text-destructive" : "text-muted-foreground",
              )}
              data-countdown={countdown}
              aria-label={`Expires in ${countdown}`}
            >
              <ClockIcon className="size-3.5" aria-hidden /> expires in {countdown}
            </span>
          )}
          <span className="ml-auto text-[11px] text-muted-foreground">
            drafted <RelativeTime iso={proposal.created_at} />
          </span>
        </div>
        <div className={cn("flex items-start gap-2 rounded-lg px-3 py-2 text-sm", TONE_CLASSES[ui.tone])} data-headline>
          {ui.status === "pending" ? (
            <ShieldCheckIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          ) : (
            <LockIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
          )}
          <span>{ui.headline}</span>
        </div>
        <PlainText text={proposal.summary} className="text-sm text-foreground" />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <span className="font-mono break-all">proposal_id: {proposal.id}</span>
          <Link
            href={`/reports?run=${encodeURIComponent(proposal.source.id)}`}
            className="inline-flex min-h-6 items-center gap-1 underline underline-offset-2 hover:text-foreground"
            data-source-report={proposal.source.id}
          >
            <FileSearchIcon className="size-3" aria-hidden /> Source report
          </Link>
        </div>
      </header>

      {shown && (
        <div
          role="alert"
          data-banner={shown.error.code}
          tabIndex={-1}
          className={cn(
            "flex items-start outline-none gap-3 rounded-xl border px-4 py-3 text-sm",
            shown.outcome.kind === "error" ? "border-destructive/25 bg-destructive/5" : "border-amber-500/40 bg-amber-500/8",
          )}
        >
          <AlertCircleIcon
            className={cn("mt-0.5 size-4 shrink-0", shown.outcome.kind === "error" ? "text-destructive" : "text-amber-600 dark:text-amber-400")}
            aria-hidden
          />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="font-medium">
              {shown.error.code === "payload_mismatch"
                ? "The proposal changed since you opened it — reload"
                : shown.outcome.title}
            </p>
            <p className="text-muted-foreground">{shown.outcome.description}</p>
            <div className="flex flex-wrap items-center gap-x-2">
              <span className="font-mono text-[11px] text-muted-foreground">{shown.error.code}</span>
              {shown.error.requestId && <RequestId id={shown.error.requestId} />}
            </div>
          </div>
          {shown.outcome.kind !== "error" && (
            <Button size="sm" variant="outline" onClick={onReload} disabled={reloading}>
              <RotateCwIcon className={cn(reloading && "animate-spin")} /> Reload
            </Button>
          )}
        </div>
      )}

      <Outcome proposal={proposal} now={now} />

      <ExecutionCard proposal={proposal} />

      {ui.status === "pending" && (
        <section aria-label="Your decision" className="space-y-3 rounded-xl border bg-card p-4" data-section="decision">
          <p className="text-sm">
            <span className="font-medium">Your decision.</span>{" "}
            <span className="text-muted-foreground">
              Approving runs <code className="font-mono">create_issue</code> once on{" "}
              <code className="font-mono">local-tracker</code> with exactly the arguments above (hash{" "}
              <code className="font-mono">{shortHash(proposal.payload_hash, 8)}</code>). Rejecting runs nothing.
            </span>
          </p>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="outline" onClick={onReject} disabled={decideDisabled} data-action="reject">
              {busy === "reject" ? <Loader2Icon className="animate-spin" /> : <XCircleIcon />} Reject
            </Button>
            <Button onClick={onApprove} disabled={decideDisabled} data-action="approve">
              {busy === "approve" ? <Loader2Icon className="animate-spin" /> : <ShieldCheckIcon />} Approve…
            </Button>
          </div>
        </section>
      )}

      <RelatedIssues proposal={proposal} />
    </div>
  );
}
