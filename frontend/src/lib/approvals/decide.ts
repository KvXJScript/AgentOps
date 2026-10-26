import type { ApiClient } from "@/lib/api/client";
import type { ActionProposal } from "@/lib/api/types";
import { approvalBody } from "./state";

export type DecisionKind = "approve" | "reject";

/**
 * Send the reviewer's decision about the proposal *as displayed*. For approve,
 * the request carries `snapshot.payload_hash` — the hash of what the person
 * reviewed in the confirm dialog — so the server can refuse with
 * `payload_mismatch` if what it would execute is different.
 */
export function submitDecision(
  client: Pick<ApiClient, "approveProposal" | "rejectProposal">,
  kind: DecisionKind,
  snapshot: Pick<ActionProposal, "id" | "payload_hash">,
): Promise<ActionProposal> {
  return kind === "approve"
    ? client.approveProposal(snapshot.id, approvalBody(snapshot).payload_hash)
    : client.rejectProposal(snapshot.id);
}
