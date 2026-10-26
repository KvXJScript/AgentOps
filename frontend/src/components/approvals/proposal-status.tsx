import { StatusBadge, TONE_CLASSES as SHARED_TONES } from "@/components/ui/status-badge";
import type { ProposalStatus } from "@/lib/api/types";
import { STATUS_LABELS, type Tone } from "@/lib/approvals/state";

/** Approval tones map 1:1 onto the shared status palette. */
export const TONE_CLASSES: Record<Tone, string> = SHARED_TONES;

const STATUS_TONE: Record<ProposalStatus, Tone> = {
  pending: "pending",
  executing: "working",
  executed: "success",
  rejected: "neutral",
  expired: "neutral",
  failed: "danger",
};

/** `status` should be the effective status (expired-by-clock applied). */
export function ProposalStatusBadge({ status, className }: { status: ProposalStatus; className?: string }) {
  return (
    <StatusBadge
      tone={STATUS_TONE[status]}
      spin={status === "executing"}
      className={className}
      data-proposal-status={status}
    >
      {STATUS_LABELS[status]}
    </StatusBadge>
  );
}
