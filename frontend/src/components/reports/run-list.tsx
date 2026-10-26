import { ErrorCallout } from "@/components/chat/error-callout";
import { Skeleton } from "@/components/ui/skeleton";
import { RelativeTime } from "@/components/ui/relative-time";
import type { ReportRun } from "@/lib/api/types";
import type { ReportListState } from "@/hooks/use-reports";
import { cn } from "@/lib/utils";
import { RunStatusBadge } from "./run-detail";


export function RunList({
  state,
  selectedId,
  onSelect,
  onRetry,
}: {
  state: ReportListState;
  selectedId: string | null;
  onSelect: (run: ReportRun) => void;
  onRetry: () => void;
}) {
  if (state.status === "loading") {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading reports">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }
  const items = state.items;
  return (
    <div className="space-y-2">
      <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Past reports</h2>
      {state.status === "error" && (
        <ErrorCallout compact error={state.error} onRetry={onRetry} />
      )}
      {items.length === 0 && state.status === "ready" ? (
        <p className="py-4 text-center text-xs text-muted-foreground">No reports yet. Ask a question above.</p>
      ) : (
        <ul className="space-y-1" aria-label="Reports">
          {items.map((run) => {
            const active = run.id === selectedId;
            return (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => onSelect(run)}
                  aria-current={active ? "true" : undefined}
                  className={cn(
                    "w-full space-y-1 rounded-lg border px-3 py-2 text-left transition-colors",
                    "hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
                    active ? "border-brand/40 bg-muted/60" : "bg-card",
                  )}
                >
                  <p className="line-clamp-2 text-sm leading-5">{run.question}</p>
                  <div className="flex items-center gap-2">
                    <RunStatusBadge status={run.status} />
                    <RelativeTime iso={run.created_at} className="ml-auto text-[11px] text-muted-foreground" />
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
