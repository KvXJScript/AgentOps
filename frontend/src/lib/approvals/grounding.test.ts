import { describe, expect, it } from "vitest";
import type { ProposalGrounding } from "@/lib/api/types";
import { groundingSummary, REASON_COPY } from "./grounding";

const issue = (reason: "unsupported" | "unverified_number" | "low_overlap") => ({
  claim: "SEV2 follows a timed acknowledgment and triage.",
  reason,
  markers: [],
  numbers: [],
});

describe("groundingSummary", () => {
  it("says when a proposal predates the check", () => {
    const s = groundingSummary(null);
    expect(s.state).toBe("unchecked");
    expect(s.headline).toMatch(/Not checked/);
    expect(s.confirmLine).toBeNull();
  });

  it("handles a body with nothing to check", () => {
    const s = groundingSummary({ checked_claims: 0, supported: 0, issues: [] });
    expect(s.state).toBe("no_claims");
    expect(s.confirmLine).toBeNull();
  });

  it("reports a clean result without a dialog warning", () => {
    const s = groundingSummary({ checked_claims: 3, supported: 3, issues: [] });
    expect(s.state).toBe("clean");
    expect(s.headline).toBe("3 claims checked, all 3 supported by the cited excerpts.");
    expect(s.confirmLine).toBeNull();
  });

  it("counts flagged claims with correct plurals for the confirm dialog", () => {
    const two: ProposalGrounding = { checked_claims: 4, supported: 2, issues: [issue("unsupported"), issue("unverified_number")] };
    expect(groundingSummary(two)).toMatchObject({
      state: "issues",
      flagged: 2,
      headline: "4 claims checked, 2 supported, 2 flagged.",
      confirmLine: "2 claims are not supported by the cited evidence.",
    });
    const one: ProposalGrounding = { checked_claims: 1, supported: 0, issues: [issue("low_overlap")] };
    expect(groundingSummary(one).confirmLine).toBe("1 claim is not supported by the cited evidence.");
  });

  it("has copy for every reason and never claims the model verified anything", () => {
    for (const copy of Object.values(REASON_COPY)) {
      expect(copy.label).not.toBe("");
      expect(copy.description).not.toMatch(/model (has )?(verified|approved)/i);
    }
  });
});
