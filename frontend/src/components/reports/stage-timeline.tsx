import { CheckIcon, CircleIcon, Loader2Icon, MinusIcon, XIcon } from "lucide-react";
import type { Stage, StageStatus } from "@/lib/api/types";
import { STAGE_LABELS, stageDuration } from "@/lib/reports/format";
import { cn } from "@/lib/utils";

const STATUS_TEXT: Record<StageStatus, string> = {
  pending: "Pending",
  running: "In progress",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
};

function StageIcon({ status }: { status: StageStatus }) {
  const base = "flex size-6 shrink-0 items-center justify-center rounded-full border";
  switch (status) {
    case "done":
      return (
        <span className={cn(base, "border-brand bg-brand text-background")}>
          <CheckIcon className="size-3.5" aria-hidden />
        </span>
      );
    case "running":
      return (
        <span className={cn(base, "border-brand/50 bg-brand/10 text-brand")}>
          <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
        </span>
      );
    case "failed":
      return (
        <span className={cn(base, "border-destructive bg-destructive text-white")}>
          <XIcon className="size-3.5" aria-hidden />
        </span>
      );
    case "skipped":
      return (
        <span className={cn(base, "border-dashed text-muted-foreground")}>
          <MinusIcon className="size-3.5" aria-hidden />
        </span>
      );
    default:
      return (
        <span className={cn(base, "text-muted-foreground/60")}>
          <CircleIcon className="size-2" aria-hidden />
        </span>
      );
  }
}

/** Vertical timeline of the five workflow stages. */
export function StageTimeline({ stages }: { stages: Stage[] }) {
  return (
    <ol className="space-y-0" aria-label="Workflow stages">
      {stages.map((stage, i) => {
        const label = STAGE_LABELS[stage.name];
        const duration = stageDuration(stage);
        const last = i === stages.length - 1;
        return (
          <li
            key={stage.name}
            className="relative flex gap-3 pb-4 last:pb-0"
            data-stage={stage.name}
            data-status={stage.status}
            aria-current={stage.status === "running" ? "step" : undefined}
          >
            {!last && (
              <span
                className={cn(
                  "absolute top-6 bottom-0 left-3 w-px -translate-x-1/2",
                  stage.status === "done" ? "bg-brand/50" : "bg-border",
                )}
                aria-hidden
              />
            )}
            <StageIcon status={stage.status} />
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span
                  className={cn(
                    "text-sm font-medium",
                    (stage.status === "pending" || stage.status === "skipped") && "text-muted-foreground",
                  )}
                >
                  {label.title}
                </span>
                <span className="sr-only">: {STATUS_TEXT[stage.status]}</span>
                {stage.status === "skipped" && <span className="text-xs text-muted-foreground">skipped</span>}
                {stage.attempts > 1 && (
                  <span className="rounded border px-1 text-[10px] text-muted-foreground" title="Model calls">
                    {stage.attempts} attempts
                  </span>
                )}
                {duration && <span className="ml-auto font-mono text-[11px] text-muted-foreground">{duration}</span>}
              </div>
              <p
                className={cn(
                  "text-xs leading-5",
                  stage.status === "failed" ? "text-destructive" : "text-muted-foreground",
                )}
              >
                {stage.detail ?? label.hint}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
