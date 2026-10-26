/**
 * One way to show times across the app: relative ("5 min ago") in lists and
 * headers, with the absolute local time available as a tooltip / `title`.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Absolute local time, e.g. "Oct 26, 2026, 11:58 AM". */
export function formatAbsolute(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Relative time. `short` is the compact sidebar form ("5m", "3h", "2d").
 * Future times (clock skew) read as "just now". Older than a week → "Oct 3"
 * (plus the year when it is not the current one).
 */
export function formatRelative(iso: string, now: number = Date.now(), short = false): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diff = Math.max(0, now - t);
  if (diff < MINUTE) return short ? "now" : "just now";
  if (diff < HOUR) {
    const m = Math.floor(diff / MINUTE);
    return short ? `${m}m` : `${m} min ago`;
  }
  if (diff < DAY) {
    const h = Math.floor(diff / HOUR);
    return short ? `${h}h` : `${h} h ago`;
  }
  if (diff < 7 * DAY) {
    const d = Math.floor(diff / DAY);
    return short ? `${d}d` : d === 1 ? "yesterday" : `${d} days ago`;
  }
  const date = new Date(t);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}
