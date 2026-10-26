import { describe, expect, it } from "vitest";
import { formatAbsolute, formatRelative } from "./time";

const NOW = new Date("2026-10-26T12:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe("formatRelative", () => {
  it("covers each bucket in long and short form", () => {
    expect(formatRelative(ago(20_000), NOW)).toBe("just now");
    expect(formatRelative(ago(20_000), NOW, true)).toBe("now");
    expect(formatRelative(ago(5 * 60_000), NOW)).toBe("5 min ago");
    expect(formatRelative(ago(5 * 60_000), NOW, true)).toBe("5m");
    expect(formatRelative(ago(3 * 3_600_000 + 59 * 60_000), NOW)).toBe("3 h ago");
    expect(formatRelative(ago(26 * 3_600_000), NOW)).toBe("yesterday");
    expect(formatRelative(ago(3 * 86_400_000), NOW)).toBe("3 days ago");
    expect(formatRelative(ago(3 * 86_400_000), NOW, true)).toBe("3d");
  });

  it("falls back to a date after a week, with the year only when it differs", () => {
    expect(formatRelative("2026-10-01T12:00:00Z", NOW)).toBe("Oct 1");
    expect(formatRelative("2025-10-01T12:00:00Z", NOW)).toBe("Oct 1, 2025");
  });

  it("treats future times (clock skew) as just now and bad input as empty", () => {
    expect(formatRelative(new Date(NOW + 90_000).toISOString(), NOW)).toBe("just now");
    expect(formatRelative("not a date", NOW)).toBe("");
    expect(formatAbsolute("not a date")).toBe("");
  });
});
