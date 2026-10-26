import { cn } from "@/lib/utils";

/**
 * Shared intro block at the top of each non-chat page: what this page is for,
 * in one sentence, plus where it sits in the upload → ask → report → approve flow.
 */
export function PageIntro({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description: React.ReactNode;
  /** Optional extras on the right (desktop) / below (mobile), e.g. a "next step" link. */
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        <h2 className="text-xl font-semibold tracking-tight text-balance">{title}</h2>
        <p className="mt-1 max-w-2xl text-sm text-pretty text-muted-foreground">{description}</p>
      </div>
      {children && <div className="flex shrink-0 flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}
