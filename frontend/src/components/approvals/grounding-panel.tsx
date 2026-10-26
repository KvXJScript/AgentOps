import { AlertTriangleIcon, CheckCircle2Icon, HelpCircleIcon, ScanSearchIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PlainText } from "@/components/tracker/tool-output";
import type { ProposalGrounding } from "@/lib/api/types";
import { groundingSummary, REASON_COPY } from "@/lib/approvals/grounding";
import { cn } from "@/lib/utils";

/**
 * "Grounding check" panel on the review screen. Claims are model output, so they are plain text.
 * Advisory only: it informs the decision, it never blocks it.
 */
export function GroundingPanel({ grounding }: { grounding: ProposalGrounding | null }) {
  const s = groundingSummary(grounding);
  const Icon = s.state === "issues" ? AlertTriangleIcon : s.state === "clean" ? CheckCircle2Icon : HelpCircleIcon;
  return (
    <section
      aria-label="Grounding check"
      data-section="grounding"
      data-grounding-state={s.state}
      className={cn(
        "space-y-3 rounded-xl border bg-card p-4",
        s.state === "issues" && "border-amber-500/40",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <ScanSearchIcon className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="mr-auto text-xs font-semibold tracking-wide uppercase">Grounding check</h3>
        {grounding && grounding.checked_claims > 0 && (
          <span className="font-mono text-xs tabular-nums" data-grounding-counts>
            {s.supported}/{s.checked} supported
          </span>
        )}
      </div>
      <p
        className={cn(
          "flex items-start gap-2 text-sm",
          s.state === "issues" ? "text-amber-800 dark:text-amber-300" : "text-muted-foreground",
          s.state === "clean" && "text-emerald-700 dark:text-emerald-400",
        )}
      >
        <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>{s.headline}</span>
      </p>
      {grounding && grounding.issues.length > 0 && (
        <ul className="space-y-2" aria-label="Flagged claims">
          {grounding.issues.map((issue, i) => {
            const copy = REASON_COPY[issue.reason];
            return (
              <li
                key={`${i}-${issue.reason}`}
                data-grounding-issue={issue.reason}
                className="space-y-1.5 rounded-lg border border-amber-500/30 bg-amber-500/6 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge className="bg-amber-500/15 text-amber-900 dark:text-amber-200">{copy.label}</Badge>
                  {issue.markers.map((m) => (
                    <Badge key={m} variant="outline" className="font-mono">
                      {m}
                    </Badge>
                  ))}
                  {issue.numbers.map((n) => (
                    <Badge key={n} variant="outline" className="font-mono" data-grounding-number>
                      {n}
                    </Badge>
                  ))}
                </div>
                <PlainText text={issue.claim} className="text-sm text-foreground" />
                <p className="text-[11px] leading-4 text-muted-foreground">{copy.description}</p>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-[11px] leading-4 text-muted-foreground">
        A deterministic server check of the body against the report&apos;s cited excerpts: every factual sentence
        needs a valid <code className="font-mono">[Sn]</code> marker, and its numbers must appear in what it cites.
        It is a heuristic — it can miss a wrong claim that reuses the excerpt&apos;s words, and it can flag a fair
        paraphrase. It doesn&apos;t change the payload or the hash; you decide.
      </p>
    </section>
  );
}

/** The line the approve dialog shows when the check flagged something (nothing otherwise). */
export function ConfirmGroundingNote({ grounding }: { grounding: ProposalGrounding | null }) {
  const { confirmLine } = groundingSummary(grounding);
  if (!confirmLine) return null;
  return (
    <p
      role="note"
      data-confirm-grounding
      className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/8 px-3 py-2 text-sm text-amber-900 dark:text-amber-200"
    >
      <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>
        {confirmLine} You can still approve — check the flagged claims on the review screen first.
      </span>
    </p>
  );
}
