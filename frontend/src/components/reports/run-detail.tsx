import { AlertCircleIcon, Loader2Icon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorCallout, RequestId } from "@/components/chat/error-callout";
import { ApiError, describeError } from "@/lib/api/errors";
import type { ReportRun, ReportRunStatus } from "@/lib/api/types";
import { insufficientReason, progressLabel, STATUS_LABELS } from "@/lib/reports/format";
import { isTerminal } from "@/lib/reports/poller";
import { ReportView } from "./report-view";
import { StageTimeline } from "./stage-timeline";

const RUN_TONE: Record<ReportRunStatus, StatusTone> = {
  queued: "neutral",
  running: "working",
  succeeded: "success",
  insufficient_evidence: "pending",
  failed: "danger",
};

export function RunStatusBadge({ status, className }: { status: ReportRunStatus; className?: string }) {
  return (
    <StatusBadge
      tone={RUN_TONE[status]}
      spin={status === "running" || status === "queued"}
      className={className}
      data-run-status={status}
    >
      {STATUS_LABELS[status]}
    </StatusBadge>
  );
}

function RunBody({ run, requestId, onRetry }: { run: ReportRun; requestId: string | null; onRetry?: () => void }) {
  if (run.status === "succeeded" && run.report) return <ReportView report={run.report} />;

  if (run.status === "succeeded") {
    // A list item (no report body) shown while the detail loads.
    return <Skeleton className="h-40 w-full" aria-label="Loading report" />;
  }

  if (run.status === "insufficient_evidence") {
    const reason = insufficientReason(run);
    return (
      <div role="status" className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm" data-state="insufficient">
        <SearchXIcon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">Not enough evidence in your documents</p>
          <p className="text-muted-foreground">
            No report was written, because it could not be grounded in your documents.
            {reason && <> Last check: <span className="text-foreground">{reason}</span>.</>}
          </p>
          <p className="text-muted-foreground">
            Try a more specific question, widen the document scope, or{" "}
            <Link href="/documents" className="underline underline-offset-2 hover:text-foreground">
              upload a document
            </Link>{" "}
            that covers it.
          </p>
        </div>
      </div>
    );
  }

  if (run.status === "failed") {
    const error = new ApiError({ code: run.error_code ?? "internal_error", message: "", requestId });
    return (
      <div data-state="failed" className="space-y-2">
        <ErrorCallout error={error} onRetry={onRetry} />
      </div>
    );
  }

  return (
    <div className="space-y-3" aria-busy="true" data-state="working">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" aria-hidden /> {progressLabel(run)}
      </p>
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-11/12" />
      <Skeleton className="h-4 w-4/5" />
    </div>
  );
}

export interface RunDetailProps {
  run: ReportRun | null;
  /** Error from the latest poll (polling keeps retrying unless it is not_found). */
  pollError: ApiError | null;
  /** X-Request-ID of the POST that created this run, when created in this session. */
  requestId?: string | null;
  onRetry?: () => void;
  /** Shown under the header of a succeeded run (Phase 4: propose a follow-up issue). */
  followUp?: React.ReactNode;
}

export function RunDetail({ run, pollError, requestId = null, onRetry, followUp }: RunDetailProps) {
  if (!run) {
    if (pollError) return <ErrorCallout error={pollError} />;
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading report run">
        <Skeleton className="h-6 w-3/4" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6" data-run-id={run.id}>
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <RunStatusBadge status={run.status} />
          {run.model && <span className="font-mono text-[11px] text-muted-foreground">{run.model}</span>}
          <span className="text-[11px] text-muted-foreground">
            {run.document_ids ? `${run.document_ids.length} document${run.document_ids.length === 1 ? "" : "s"}` : "All documents"}
          </span>
        </div>
        <p className="text-base leading-6 font-medium break-words">{run.question}</p>
        <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
          <span className="font-mono break-all">run_id: {run.id}</span>
          {requestId && <RequestId id={requestId} />}
        </div>
      </header>

      {run.status === "succeeded" && run.report && followUp}

      {pollError && !isTerminal(run.status) && (
        <p role="alert" className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertCircleIcon className="size-3.5 text-destructive" aria-hidden />
          Couldn&apos;t refresh progress ({describeError(pollError).title}); retrying…
          {pollError.requestId && <RequestId id={pollError.requestId} />}
        </p>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_15rem]">
        <div className="min-w-0 xl:order-1">
          <RunBody run={run} requestId={requestId} onRetry={onRetry} />
        </div>
        <aside className="xl:order-2" aria-label="Progress">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-muted-foreground uppercase">Workflow</h3>
          <StageTimeline stages={run.stages} />
        </aside>
      </div>
      {run.status === "failed" && onRetry && !describeError({ code: run.error_code ?? "" }).retryable && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Run again
        </Button>
      )}
    </div>
  );
}
