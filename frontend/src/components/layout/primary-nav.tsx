"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CircleDotIcon, FileTextIcon, MessageSquareIcon, NotebookPenIcon, ShieldCheckIcon } from "lucide-react";
import { usePendingProposalCount } from "@/hooks/use-proposals";
import { cn } from "@/lib/utils";

/** Product areas. Flip `enabled` when a phase ships; disabled items show "Soon". */
export const NAV = [
  { label: "Chat", href: "/", icon: MessageSquareIcon, enabled: true },
  { label: "Documents", href: "/documents", icon: FileTextIcon, enabled: true },
  { label: "Reports", href: "/reports", icon: NotebookPenIcon, enabled: true },
  { label: "Approvals", href: "/approvals", icon: ShieldCheckIcon, enabled: true },
  { label: "Tracker", href: "/tracker", icon: CircleDotIcon, enabled: true },
] as const;

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function PrimaryNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname() ?? "/";
  const pending = usePendingProposalCount();
  return (
    <nav aria-label="Primary" className="px-2">
      <ul className="space-y-0.5">
        {NAV.map(({ label, href, icon: Icon, enabled }) => {
          const active = isActive(pathname, href);
          return (
            <li key={label}>
              {enabled ? (
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  onClick={onNavigate}
                  className={cn(
                    "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
                    "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
                    active
                      ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/85 hover:bg-sidebar-accent/70",
                  )}
                >
                  <Icon className="size-4" aria-hidden /> {label}
                  {href === "/approvals" && pending !== null && pending > 0 && (
                    <span
                      className="ml-auto inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-amber-500/15 px-1.5 text-[11px] font-medium text-amber-800 tabular-nums dark:text-amber-300"
                      aria-label={`${pending} awaiting review`}
                      title={`${pending} proposal${pending === 1 ? "" : "s"} awaiting your review`}
                      data-pending-count={pending}
                    >
                      {pending}
                    </span>
                  )}
                </Link>
              ) : (
                <span
                  aria-disabled="true"
                  title={`${label} is coming soon`}
                  className="flex h-8 cursor-not-allowed items-center gap-2.5 rounded-md px-2.5 text-sm text-muted-foreground/70"
                >
                  <Icon className="size-4" aria-hidden /> {label}
                  <span className="ml-auto rounded border px-1.5 text-[10px] tracking-wide uppercase">Soon</span>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
