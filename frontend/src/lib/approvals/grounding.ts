/**
 * Wording for the server's grounding check (pure, unit-tested).
 *
 * The check is advisory: it never blocks approval and never changes what executes.
 * It flags claims in the model-drafted body that it could not tie to the cited excerpts.
 */
import type { GroundingReason, ProposalGrounding } from "@/lib/api/types";

export const REASON_COPY: Record<GroundingReason, { label: string; description: string }> = {
  unsupported: {
    label: "No source",
    description: "Cites no excerpt the model was shown, so nothing backs it.",
  },
  unverified_number: {
    label: "Number not in source",
    description: "A number or duration here doesn't appear in the excerpts it cites.",
  },
  low_overlap: {
    label: "Weak match",
    description: "Cites an excerpt, but shares few words with it — the excerpt may not say this.",
  },
};

export type GroundingState = "unchecked" | "no_claims" | "clean" | "issues";

export interface GroundingSummary {
  state: GroundingState;
  checked: number;
  supported: number;
  flagged: number;
  /** One line for the panel header. */
  headline: string;
  /** Shown in the approve dialog when something was flagged; null otherwise. */
  confirmLine: string | null;
}

const claims = (n: number) => `${n} claim${n === 1 ? "" : "s"}`;

export function groundingSummary(g: ProposalGrounding | null): GroundingSummary {
  if (!g) {
    return {
      state: "unchecked",
      checked: 0,
      supported: 0,
      flagged: 0,
      headline: "Not checked — this proposal was drafted before grounding checks existed.",
      confirmLine: null,
    };
  }
  const flagged = g.issues.length;
  const base = { checked: g.checked_claims, supported: g.supported, flagged };
  if (g.checked_claims === 0) {
    return { ...base, state: "no_claims", headline: "No factual claims found to check.", confirmLine: null };
  }
  if (flagged === 0) {
    return {
      ...base,
      state: "clean",
      headline: `${claims(g.checked_claims)} checked, all ${g.supported} supported by the cited excerpts.`,
      confirmLine: null,
    };
  }
  return {
    ...base,
    state: "issues",
    headline: `${claims(g.checked_claims)} checked, ${g.supported} supported, ${flagged} flagged.`,
    confirmLine: `${claims(flagged)} ${flagged === 1 ? "is" : "are"} not supported by the cited evidence.`,
  };
}
