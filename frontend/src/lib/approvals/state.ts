/**
 * Pure helpers for the approval boundary UI (no React, unit-tested).
 *
 * Wording rule: a proposal is *drafted* by the model and *decided* by a person.
 * Nothing here may say or imply that the model approved anything.
 */
import type { ActionProposal, ProposalStatus } from "@/lib/api/types";
import { describeError } from "@/lib/api/errors";

/**
 * Status as the reviewer should see it right now. The server reads pending
 * proposals past `expires_at` back as `expired`; the client applies the same
 * rule against its own clock so a stale copy can't be approved.
 */
export function effectiveStatus(p: Pick<ActionProposal, "status" | "expires_at">, now: number): ProposalStatus {
  if (p.status === "pending") {
    const exp = Date.parse(p.expires_at);
    if (Number.isFinite(exp) && now >= exp) return "expired";
  }
  return p.status;
}

export type Tone = "pending" | "working" | "success" | "neutral" | "danger";

export interface ProposalUiState {
  status: ProposalStatus;
  label: string;
  tone: Tone;
  /** One-line explanation shown at the top of the review screen. */
  headline: string;
  /** Approve / Reject are offered only here. */
  canDecide: boolean;
  /** The proposal is moving server-side; the view should keep polling. */
  inFlight: boolean;
}

export const STATUS_LABELS: Record<ProposalStatus, string> = {
  pending: "Awaiting your review",
  executing: "Executing",
  executed: "Executed",
  rejected: "Rejected",
  expired: "Expired",
  failed: "Failed",
};

export function proposalUiState(p: ActionProposal, now: number): ProposalUiState {
  const status = effectiveStatus(p, now);
  const base = { status, label: STATUS_LABELS[status], canDecide: false, inFlight: false };
  switch (status) {
    case "pending":
      return {
        ...base,
        tone: "pending",
        canDecide: true,
        headline: "Drafted by the model, not executed. Nothing happens unless you approve it.",
      };
    case "executing":
      return {
        ...base,
        tone: "working",
        inFlight: true,
        headline: "You approved this proposal. The tool call is running now.",
      };
    case "executed":
      return {
        ...base,
        tone: "success",
        headline: p.result
          ? `You approved this proposal. Issue ${p.result.issue_id} was created in local-tracker.`
          : "You approved this proposal and it was executed.",
      };
    case "rejected":
      return { ...base, tone: "neutral", headline: "You rejected this proposal. Nothing was executed." };
    case "expired":
      return {
        ...base,
        tone: "neutral",
        headline: "Nobody approved this proposal within 15 minutes, so it expired. Nothing was executed.",
      };
    case "failed":
      return {
        ...base,
        tone: "danger",
        headline: `You approved this proposal, but the tool call failed${p.error_code ? ` (${p.error_code})` : ""}. No issue id was returned.`,
      };
  }
}

/** "14:59" / "0:05" until expiry; null once expired. */
export function formatCountdown(expiresAt: string, now: number): string | null {
  const ms = Date.parse(expiresAt) - now;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const total = Math.ceil(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function shortHash(hash: string, n = 12): string {
  return hash.length > n ? `${hash.slice(0, n)}…` : hash;
}

/**
 * Exactly what the approve request sends: the hash of the proposal the
 * reviewer is looking at (the snapshot shown in the confirm dialog), never a
 * re-fetched value.
 */
export function approvalBody(displayed: Pick<ActionProposal, "payload_hash">): { payload_hash: string } {
  return { payload_hash: displayed.payload_hash };
}

/**
 * At most one call in flight per guard. A second `run` while the first is
 * pending returns the first promise without calling `fn` again, so a
 * double-click (or Enter + click) can never send two requests.
 */
export class SingleFlight<T> {
  private current: Promise<T> | null = null;

  get busy(): boolean {
    return this.current !== null;
  }

  run(fn: () => Promise<T>): Promise<T> {
    if (this.current) return this.current;
    const p = (async () => {
      try {
        return await fn();
      } finally {
        this.current = null;
      }
    })();
    this.current = p;
    return p;
  }
}

export type DecisionOutcome =
  | { kind: "stale"; title: string; description: string } // payload_mismatch: must reload before deciding
  | { kind: "refresh"; title: string; description: string } // state moved on server-side: refetch
  | { kind: "error"; title: string; description: string; retryable: boolean };

/** How the review screen reacts to an approve/reject error. */
export function decisionOutcome(err: { code: string; message?: string; retryAfterS?: number | null }): DecisionOutcome {
  const d = describeError(err);
  if (err.code === "payload_mismatch") return { kind: "stale", title: d.title, description: d.description };
  if (err.code === "proposal_not_pending" || err.code === "proposal_expired" || err.code === "not_found") {
    return { kind: "refresh", title: d.title, description: d.description };
  }
  return { kind: "error", title: d.title, description: d.description, retryable: d.retryable };
}

/** Canonical JSON preview of the exact payload (what `payload_hash` covers). */
export function payloadPreview(p: Pick<ActionProposal, "tool" | "target" | "arguments">): string {
  return JSON.stringify({ tool: p.tool, target: p.target, arguments: p.arguments }, null, 2);
}

export const PROPOSALS_CHANGED_EVENT = "agentops:proposals-changed";

/** Tell other parts of the app (nav badge, lists) that proposals changed. */
export function notifyProposalsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(PROPOSALS_CHANGED_EVENT));
}
