"use client";

import { useState } from "react";
import { AlertCircleIcon, CheckIcon, CopyIcon, RotateCwIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { describeError, type ApiError } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

export function RequestId({ id, className }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded px-1 font-mono text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
        className,
      )}
      aria-label={copied ? "Request ID copied" : `Copy request ID ${id}`}
      title="Copy request ID"
      onClick={() =>
        void navigator.clipboard?.writeText(id).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        })
      }
    >
      <span className="truncate">request_id: {id}</span>
      {copied ? <CheckIcon className="size-3 shrink-0" /> : <CopyIcon className="size-3 shrink-0" />}
    </button>
  );
}

export function ErrorCallout({
  error,
  onRetry,
  onDismiss,
  className,
}: {
  error: ApiError;
  onRetry?: () => void;
  onDismiss?: () => void;
  className?: string;
}) {
  const d = describeError(error);
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-xl border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm",
        className,
      )}
    >
      <AlertCircleIcon className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="font-medium text-foreground">{d.title}</p>
        <p className="text-muted-foreground">{d.description}</p>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-0.5">
          <span className="font-mono text-[11px] text-muted-foreground">{error.code}</span>
          {error.requestId && <RequestId id={error.requestId} />}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {d.retryable && onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RotateCwIcon /> Retry
          </Button>
        )}
        {onDismiss && (
          <Button size="icon-sm" variant="ghost" aria-label="Dismiss error" onClick={onDismiss}>
            <XIcon />
          </Button>
        )}
      </div>
    </div>
  );
}
