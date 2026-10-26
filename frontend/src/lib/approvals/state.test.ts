import { describe, expect, it, vi } from "vitest";
import { ApiClient, type FetchLike } from "@/lib/api/client";
import { ApiError, describeError } from "@/lib/api/errors";
import type { ProposalStatus } from "@/lib/api/types";
import { HASH, NOW, proposal } from "@/test/proposal-fixtures";
import { submitDecision } from "./decide";
import {
  decisionOutcome,
  effectiveStatus,
  formatCountdown,
  proposalUiState,
  shortHash,
  SingleFlight,
} from "./state";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Records every request; answers with `respond`. */
function recordingFetch(respond: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    return respond(url, init);
  };
  return { calls, fetchImpl };
}

const MODEL_APPROVED = /model (has )?approved|approved by (the )?model|model approval|auto-?approved/i;

describe("status → UI state", () => {
  it.each<[ProposalStatus, string, boolean]>([
    ["pending", "pending", true],
    ["executing", "working", false],
    ["executed", "success", false],
    ["rejected", "neutral", false],
    ["expired", "neutral", false],
    ["failed", "danger", false],
  ])("%s → tone %s, canDecide=%s", (status, tone, canDecide) => {
    const ui = proposalUiState(proposal({ status, result: status === "executed" ? { issue_id: "LT-9" } : null }), NOW);
    expect(ui.status).toBe(status);
    expect(ui.tone).toBe(tone);
    expect(ui.canDecide).toBe(canDecide);
    expect(ui.headline).not.toMatch(MODEL_APPROVED);
  });

  it("treats a pending proposal past expires_at as expired by the client clock", () => {
    const p = proposal({ status: "pending", expires_at: "2026-10-26T12:15:00Z" });
    expect(effectiveStatus(p, Date.parse("2026-10-26T12:14:59Z"))).toBe("pending");
    expect(effectiveStatus(p, Date.parse("2026-10-26T12:15:00Z"))).toBe("expired");
    const ui = proposalUiState(p, Date.parse("2026-10-26T12:20:00Z"));
    expect(ui.status).toBe("expired");
    expect(ui.canDecide).toBe(false);
  });

  it("never re-labels decided proposals by clock", () => {
    const p = proposal({ status: "executed", expires_at: "2026-10-26T12:00:01Z", result: { issue_id: "LT-9" } });
    expect(effectiveStatus(p, NOW)).toBe("executed");
  });

  it("mentions the created issue and the failure code", () => {
    expect(proposalUiState(proposal({ status: "executed", result: { issue_id: "LT-9" } }), NOW).headline).toContain("LT-9");
    expect(proposalUiState(proposal({ status: "failed", error_code: "mcp_timeout" }), NOW).headline).toContain("mcp_timeout");
  });

  it("formats the expiry countdown", () => {
    expect(formatCountdown("2026-10-26T12:15:00Z", NOW)).toBe("10:00");
    expect(formatCountdown("2026-10-26T12:05:04.200Z", NOW)).toBe("0:05");
    expect(formatCountdown("2026-10-26T12:05:00Z", NOW)).toBeNull();
    expect(shortHash(HASH)).toBe(`${HASH.slice(0, 12)}…`);
  });
});

describe("approve request", () => {
  it("carries the payload_hash of the proposal as displayed", async () => {
    const { calls, fetchImpl } = recordingFetch(() =>
      jsonResponse(200, proposal({ status: "executed", result: { issue_id: "LT-9" } })),
    );
    const displayed = proposal({ payload_hash: "a".repeat(64) });
    await submitDecision(new ApiClient({ fetch: fetchImpl }), "approve", displayed);

    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe(`/api/v1/proposals/${displayed.id}/approve`);
    expect(calls[0].body).toEqual({ payload_hash: "a".repeat(64) });
  });

  it("reject sends no payload and hits the reject endpoint", async () => {
    const { calls, fetchImpl } = recordingFetch(() => jsonResponse(200, proposal({ status: "rejected" })));
    await submitDecision(new ApiClient({ fetch: fetchImpl }), "reject", proposal());
    expect(calls[0].url).toBe(`/api/v1/proposals/${proposal().id}/reject`);
    expect(calls[0].body).toBeNull();
  });

  it("surfaces payload_mismatch as a stale view that needs a reload", async () => {
    const { fetchImpl } = recordingFetch(() =>
      jsonResponse(409, { error: { code: "payload_mismatch", message: "x", request_id: "req-9" } }),
    );
    const err = await submitDecision(new ApiClient({ fetch: fetchImpl }), "approve", proposal()).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("payload_mismatch");
    expect(decisionOutcome(err).kind).toBe("stale");
  });
});

describe("double-submit guard", () => {
  it("runs only one call while one is in flight, then allows the next", async () => {
    let release!: () => void;
    const { calls, fetchImpl } = recordingFetch(
      () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(jsonResponse(200, proposal({ status: "executed", result: { issue_id: "LT-9" } })));
        }),
    );
    const client = new ApiClient({ fetch: fetchImpl });
    const guard = new SingleFlight<unknown>();
    const click = () => guard.run(() => submitDecision(client, "approve", proposal()));

    const a = click();
    const b = click(); // double-click
    const c = click(); // and Enter
    expect(b).toBe(a);
    expect(c).toBe(a);
    expect(guard.busy).toBe(true);
    await Promise.resolve();
    release();
    await a;
    expect(calls).toHaveLength(1);
    expect(guard.busy).toBe(false);

    const d = click();
    await Promise.resolve();
    release();
    await d;
    expect(calls).toHaveLength(2);
  });

  it("releases the guard after a failure", async () => {
    const guard = new SingleFlight<void>();
    const fn = vi.fn(async () => {
      throw new Error("boom");
    });
    await expect(guard.run(fn)).rejects.toThrow("boom");
    expect(guard.busy).toBe(false);
    await expect(guard.run(fn)).rejects.toThrow("boom");
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("decision errors and Phase 4 error copy", () => {
  it.each([
    ["payload_mismatch", "stale"],
    ["proposal_not_pending", "refresh"],
    ["proposal_expired", "refresh"],
    ["not_found", "refresh"],
    ["mcp_timeout", "error"],
    ["mcp_unavailable", "error"],
    ["internal_error", "error"],
  ])("%s → %s", (code, kind) => {
    expect(decisionOutcome({ code }).kind).toBe(kind);
  });

  it.each([
    ["mcp_unavailable", "local tracker isn't available", true],
    ["mcp_timeout", "didn't answer in time", true],
    ["invalid_tool_output", "returned something unexpected", true],
    ["report_not_ready", "isn't finished", false],
    ["proposal_not_pending", "already decided", false],
    ["payload_mismatch", "changed since you opened it", false],
    ["proposal_expired", "expired", false],
    ["invalid_model_output", "couldn't be used", true],
    ["llm_rate_limited", "rate limit", true],
    ["llm_not_configured", "No model is configured", false],
  ])("describes %s", (code, text, retryable) => {
    const d = describeError({ code });
    expect(`${d.title} ${d.description}`).toContain(text);
    expect(d.retryable).toBe(retryable);
    expect(`${d.title} ${d.description}`).not.toMatch(MODEL_APPROVED);
  });

  it("is retryable for transient MCP errors on ApiError too", () => {
    expect(new ApiError({ code: "mcp_timeout", message: "" }).retryable).toBe(true);
    expect(new ApiError({ code: "payload_mismatch", message: "" }).retryable).toBe(false);
  });
});

describe("tracker + proposal client", () => {
  it("trims and caps the issue query at 200 characters", async () => {
    const { calls, fetchImpl } = recordingFetch(() => jsonResponse(200, { items: [], truncated: false }));
    const client = new ApiClient({ fetch: fetchImpl });
    await client.listTrackerIssues({ query: `  ${"x".repeat(250)}  `, limit: 20 });
    const url = new URL(calls[0].url, "http://x");
    expect(url.pathname).toBe("/api/v1/integrations/tracker/issues");
    expect(url.searchParams.get("query")).toHaveLength(200);
    expect(url.searchParams.get("limit")).toBe("20");
    await client.listTrackerIssues({ query: "   " });
    expect(calls[1].url).toBe("/api/v1/integrations/tracker/issues");
  });

  it("flags an existing pending proposal (200) vs a new one (201)", async () => {
    let status = 201;
    const { fetchImpl } = recordingFetch(() => jsonResponse(status, proposal()));
    const client = new ApiClient({ fetch: fetchImpl });
    expect((await client.createProposal("r1")).existing).toBe(false);
    status = 200;
    expect((await client.createProposal("r1")).existing).toBe(true);
  });

  it("maps report_not_ready to an ApiError", async () => {
    const { fetchImpl } = recordingFetch(() =>
      jsonResponse(409, { error: { code: "report_not_ready", message: "x", request_id: "req-1" } }),
    );
    const err = await new ApiClient({ fetch: fetchImpl }).createProposal("r1").catch((e) => e);
    expect(err.code).toBe("report_not_ready");
    expect(err.status).toBe(409);
  });
});
