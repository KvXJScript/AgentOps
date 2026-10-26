import { CircleDotIcon, CircleCheckIcon, TerminalSquareIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { RelativeTime } from "@/components/ui/relative-time";
import { StatusBadge } from "@/components/ui/status-badge";
import type { Issue } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * Untrusted text from an MCP tool (issue titles/bodies, labels). Rendered as a
 * React text node inside a <pre>: no Markdown, no HTML, no auto-linking — a
 * `<script>`, `**bold**` or `[link](javascript:…)` shows up literally.
 */
export function PlainText({ text, className }: { text: string; className?: string }) {
  return (
    <pre
      data-plain-text=""
      className={cn("font-sans text-[13px] leading-5 break-words whitespace-pre-wrap text-foreground/85", className)}
    >
      {text}
    </pre>
  );
}

/** Visual frame that marks content as tool output (untrusted, display only). */
export function ToolOutputFrame({
  source,
  children,
  className,
}: {
  /** e.g. "search_issues · local-tracker" */
  source: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div data-tool-output="" className={cn("overflow-hidden rounded-lg border border-dashed bg-muted/30", className)}>
      <div className="flex items-center gap-1.5 border-b border-dashed px-3 py-1 text-[10px] tracking-wide text-muted-foreground uppercase">
        <TerminalSquareIcon className="size-3" aria-hidden />
        <span>Tool output</span>
        <span aria-hidden>·</span>
        <span className="font-mono normal-case">{source}</span>
        <span className="ml-auto normal-case">untrusted · shown as plain text</span>
      </div>
      <div className="px-3 py-2">{children}</div>
    </div>
  );
}

export function IssueStatusBadge({ status }: { status: Issue["status"] }) {
  return status === "open" ? (
    <StatusBadge tone="success" icon={CircleDotIcon}>
      open
    </StatusBadge>
  ) : (
    <StatusBadge tone="neutral" icon={CircleCheckIcon}>
      closed
    </StatusBadge>
  );
}


/** One tracker issue; every text field is tool output. */
export function IssueItem({
  issue,
  highlighted = false,
  clamp = false,
}: {
  issue: Issue;
  highlighted?: boolean;
  /** Clamp the body to 4 lines (the full text stays in the DOM). */
  clamp?: boolean;
}) {
  return (
    <article
      data-issue-id={issue.id}
      aria-label={`Issue ${issue.id}`}
      className={cn(
        "space-y-2 rounded-lg border bg-card p-3",
        highlighted && "border-brand/50 ring-2 ring-brand/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-medium">{issue.id}</span>
        <IssueStatusBadge status={issue.status} />
        {issue.labels.map((l) => (
          <Badge key={l} variant="outline" className="font-mono text-[11px]">
            {l}
          </Badge>
        ))}
        <span className="ml-auto text-[11px] text-muted-foreground"><RelativeTime iso={issue.created_at} /></span>
      </div>
      <PlainText text={issue.title} className="text-sm font-medium text-foreground" />
      {issue.body && (
        <PlainText
          text={issue.body}
          className={cn("text-muted-foreground", clamp && "line-clamp-4")}
        />
      )}
    </article>
  );
}
