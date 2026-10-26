import { cn } from "@/lib/utils";

/** Small geometric mark: two offset nodes joined by an edge (an "agent graph"). */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-lg bg-brand/12 text-brand ring-1 ring-brand/20 ring-inset",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" fill="none" className="size-[60%]" aria-hidden>
        <circle cx="7" cy="8" r="3" fill="currentColor" />
        <circle cx="17" cy="16" r="3" fill="currentColor" opacity="0.55" />
        <path d="M9.5 9.8 14.5 14.2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    </span>
  );
}

export function Brand() {
  return (
    <span className="flex items-center gap-2.5">
      <BrandMark className="size-7" />
      <span className="text-[15px] font-semibold tracking-tight">AgentOps</span>
    </span>
  );
}
