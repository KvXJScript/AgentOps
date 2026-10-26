import type { LucideIcon } from "lucide-react";
import { Loader2Icon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One colour language for every status in the app:
 * - success  → finished and usable (document ready, report ready, issue executed, tracker connected)
 * - pending  → waiting on a person (awaiting review) or a soft warning (not enough evidence)
 * - working  → in progress (processing, running, executing)
 * - neutral  → inactive / closed / queued (rejected, expired, queued)
 * - danger   → failed
 * Text colours are the -700/-800 (light) and -300/-400 (dark) steps so badges stay ≥ 4.5:1.
 */
export type StatusTone = "success" | "pending" | "working" | "neutral" | "danger";

export const TONE_CLASSES: Record<StatusTone, string> = {
  success: "border-emerald-600/25 bg-emerald-500/10 text-emerald-800 dark:border-emerald-400/25 dark:text-emerald-300",
  pending: "border-amber-600/30 bg-amber-500/12 text-amber-900 dark:border-amber-400/25 dark:text-amber-300",
  working: "border-brand/30 bg-brand/10 text-brand",
  neutral: "border-border bg-muted/40 text-muted-foreground",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
};

export function StatusBadge({
  tone,
  icon: Icon,
  spin = false,
  children,
  className,
  ...rest
}: {
  tone: StatusTone;
  icon?: LucideIcon;
  /** Show a spinner (ignores `icon`); stops under prefers-reduced-motion. */
  spin?: boolean;
  children: React.ReactNode;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLSpanElement>, "className" | "children">) {
  const Shown = spin ? Loader2Icon : Icon;
  return (
    <span
      className={cn(
        "inline-flex h-5 w-fit shrink-0 items-center gap-1 rounded-full border px-2 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[tone],
        className,
      )}
      {...rest}
    >
      {Shown && <Shown className={cn("size-3 shrink-0", spin && "animate-spin motion-reduce:animate-none")} aria-hidden />}
      {children}
    </span>
  );
}
