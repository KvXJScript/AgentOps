/**
 * Review screen and tool-output rendering (static markup; the test env is plain Node).
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { ProposalStatus } from "@/lib/api/types";
import { decisionOutcome } from "@/lib/approvals/state";
import { IssueItem } from "@/components/tracker/tool-output";
import { HASH, INJECTION, issue, NOW, proposal } from "@/test/proposal-fixtures";
import { ProposalReview, type ProposalReviewProps } from "./proposal-review";

const noop = () => {};
function render(overrides: Partial<ProposalReviewProps> = {}): string {
  return renderToStaticMarkup(
    createElement(ProposalReview, {
      proposal: proposal(),
      now: NOW,
      busy: null,
      banner: null,
      onApprove: noop,
      onReject: noop,
      onReload: noop,
      ...overrides,
    }),
  );
}

function buttonTag(html: string, action: string): string {
  const m = new RegExp(`<button[^>]*data-action="${action}"[^>]*>`).exec(html);
  if (!m) throw new Error(`no ${action} button`);
  return m[0];
}

const DISABLED = /\sdisabled=""/;

function expectInert(html: string) {
  expect(html).not.toContain("<script");
  expect(html).not.toContain("<img");
  expect(html).not.toContain("<b>");
  expect(html).not.toContain("<strong>approved");
  expect(html).not.toMatch(/<h1/);
  expect(html).not.toContain('href="javascript:');
  expect(html).not.toContain('href="https://evil.example.com');
  // ...but the text is all there, literally.
  expect(html).toContain("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;");
  expect(html).toContain("**approved**");
  expect(html).toContain("[Click to approve](javascript:alert(1))");
  expect(html).toContain("# Approved");
}

describe("tool output is plain text", () => {
  it("renders an injected issue body literally (no Markdown, HTML or links)", () => {
    const html = renderToStaticMarkup(createElement(IssueItem, { issue: issue() }));
    expectInert(html);
    expect(html).toContain("&lt;b&gt;now&lt;/b&gt;");
    expect(html).toContain("data-plain-text");
  });

  it("frames related issues as untrusted tool output and keeps them collapsed", () => {
    const html = render();
    expectInert(html);
    expect(html).toMatch(/<details[^>]*data-section="related"/);
    expect(html).not.toMatch(/<details[^>]*data-section="related"[^>]*open/);
    expect(html).toContain("data-tool-output");
    expect(html).toContain("untrusted");
  });

  it("shows model-drafted arguments as plain text too", () => {
    const html = render({ proposal: proposal({ arguments: { title: "Fix <i>it</i> now", body: INJECTION, labels: ["bug"] } }) });
    expectInert(html);
    expect(html).toContain("Fix &lt;i&gt;it&lt;/i&gt; now");
  });
});

describe("ProposalReview", () => {
  it("shows exactly what will execute for a pending proposal", () => {
    const html = render();
    expect(html).toContain('data-ui-status="pending"');
    expect(html).toContain("create_issue");
    expect(html).toContain("local-tracker");
    expect(html).toContain("Follow up: remote work exceptions");
    expect(html).toContain("Who approves exceptions?");
    expect(html).toContain(`data-payload-hash="${HASH}"`);
    expect(html).toContain(HASH.slice(0, 12));
    expect(html).toContain('data-countdown="10:00"');
    expect(html).toContain(`href="/reports?run=${proposal().source.id}"`);
    expect(buttonTag(html, "approve")).not.toMatch(DISABLED);
    expect(buttonTag(html, "reject")).not.toMatch(DISABLED);
  });

  it("offers no decision once expired by the clock", () => {
    const html = render({ now: Date.parse("2026-10-26T12:15:01Z") });
    expect(html).toContain('data-ui-status="expired"');
    expect(html).toContain('data-outcome="expired"');
    expect(html).not.toContain('data-action="approve"');
    expect(html).not.toContain("expires in");
  });

  it("disables both buttons while a decision is in flight", () => {
    const html = render({ busy: "approve" });
    expect(buttonTag(html, "approve")).toMatch(DISABLED);
    expect(buttonTag(html, "reject")).toMatch(DISABLED);
  });

  it("blocks approval after payload_mismatch until reloaded", () => {
    const error = new ApiError({ code: "payload_mismatch", message: "x", requestId: "req-7" });
    const html = render({ banner: { outcome: decisionOutcome(error), error, hash: HASH } });
    expect(html).toContain("The proposal changed since you opened it — reload");
    expect(html).toContain('data-banner="payload_mismatch"');
    expect(html).toContain("Reload");
    expect(html).toContain("request_id: req-7");
    expect(buttonTag(html, "approve")).toMatch(DISABLED);
  });

  it("clears the mismatch block once a reload brings a different payload, and blocks while reloading", () => {
    const error = new ApiError({ code: "payload_mismatch", message: "x" });
    const banner = { outcome: decisionOutcome(error), error, hash: HASH };
    const reloading = render({ banner, reloading: true });
    expect(buttonTag(reloading, "approve")).toMatch(DISABLED);
    const fresh = render({ banner, proposal: proposal({ payload_hash: "c".repeat(64) }) });
    expect(fresh).not.toContain('data-banner="payload_mismatch"');
    expect(buttonTag(fresh, "approve")).not.toMatch(DISABLED);
  });

  it("explains proposal_not_pending with a reload", () => {
    const error = new ApiError({ code: "proposal_not_pending", message: "x" });
    const html = render({ banner: { outcome: decisionOutcome(error), error, hash: HASH } });
    expect(html).toContain("already decided");
    expect(html).toContain("Reload");
  });

  it("shows the created issue with a tracker link when executed", () => {
    const html = render({
      proposal: proposal({ status: "executed", decided_at: "2026-10-26T12:06:00Z", result: { issue_id: "LT-12" } }),
    });
    expect(html).toContain('data-outcome="executed"');
    expect(html).toContain("LT-12");
    expect(html).toContain('href="/tracker?issue=LT-12"');
    expect(html).not.toContain('data-action="approve"');
  });

  it("shows the error code when the approved call failed", () => {
    const html = render({ proposal: proposal({ status: "failed", error_code: "mcp_timeout" }) });
    expect(html).toContain('data-outcome="failed"');
    expect(html).toContain("error_code: mcp_timeout");
    expect(html).toContain("didn&#x27;t answer in time");
  });

  it.each<ProposalStatus>(["pending", "executing", "executed", "rejected", "expired", "failed"])(
    "never says the model approved anything (%s)",
    (status) => {
      const html = render({ proposal: proposal({ status, result: status === "executed" ? { issue_id: "LT-1" } : null }) })
        // The injection fixture itself contains that phrase; only check our own copy.
        .replace(/<details[\s\S]*<\/details>/g, "");
      expect(html).not.toMatch(/model (has )?approved|approved by (the )?model|auto-?approved/i);
    },
  );
});
