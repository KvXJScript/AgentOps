"use client";

import { useEffect, useState } from "react";
import { formatAbsolute, formatRelative } from "@/lib/format/time";
import { cn } from "@/lib/utils";

/** Re-renders once a minute so "5 min ago" stays true without a reload. */
function useMinuteTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(t);
  }, []);
  return now;
}

/** Relative time with the absolute local time as tooltip (`title`). */
export function RelativeTime({ iso, short = false, className }: { iso: string; short?: boolean; className?: string }) {
  const now = useMinuteTick();
  const absolute = formatAbsolute(iso);
  return (
    <time dateTime={iso} title={absolute} className={cn("tabular-nums", className)}>
      {formatRelative(iso, now, short)}
    </time>
  );
}
