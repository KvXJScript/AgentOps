/**
 * Rendering states of the report run view (server-rendered to static markup;
 * the test environment is plain Node, no DOM).
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { insufficientReason, progressLabel, stageDuration } from "@/lib/reports/format";
import { questionProblem } from "./report-form";
import { RunDetail, type RunDetailProps } from "./run-detail";
import { report, reportRun, stages } from "@/test/report-fixtures";

function render(props: RunDetailProps): string {
  return renderToStaticMarkup(createElement(RunDetail, props));
}

function stageStatuses(html: string): Record<string, string> {
  return Object.fromEntries([...html.matchAll(/data-stage="(\w+)" data-status="(\w+)"/g)].map((m) => [m[1], m[2]]));
}

describe("RunDetail", () => {
  it("shows a live timeline and a working state while running", () => {
    const run = reportRun({
      status: "running",
      stages: stages({ validate: "done", retrieve: "running" }, { validate: "Scope: all ready documents" }),
    });
    const html = render({ run, pollError: null });

    expect(html).toContain('data-state="working"');
    expect(html).toContain("Retrieve…");
    expect(stageStatuses(html)).toEqual({
      validate: "done",
      retrieve: "running",
      analyze: "pending",
      draft: "pending",
      verify: "pending",
    });
    expect(html).toContain('aria-current="step"');
    expect(html).toContain("Scope: all ready documents");
    expect(html).toContain("1.3 s"); // finished stage duration
  });

  it("renders the report with citation chips and a references list", () => {
    const run = reportRun({
      status: "succeeded",
      stages: stages(
        { validate: "done", retrieve: "done", analyze: "done", draft: "done", verify: "done" },
        { verify: "2 references verified, 1 invalid reference removed" },
      ),
      report: report({ summary: "Two days [S1]. Invented [S9] stays text." }),
    });
    const html = render({ run, pollError: null });

    expect(html).toContain("<h2");
    expect(html).toContain("Remote work policy");
    expect(html).toContain("<h3");
    expect(html).toContain("Eligibility");
    // Valid markers become chips, the unknown one stays plain text.
    expect(html).toContain('data-citation-chip="S1"');
    expect(html).toContain('data-citation-chip="S2"');
    expect(html).not.toContain('data-citation-chip="S9"');
    expect(html).toContain("[S9]");
    expect(html).toContain('data-reference="S1"');
    expect(html).toContain("References (2)");
    expect(html).toContain("Source deleted");
    expect(html).toContain("1 invalid reference removed");
    expect(html).not.toContain('data-state="working"');
  });

  it("shows an explicit insufficient-evidence state with the reason", () => {
    const run = reportRun({
      status: "insufficient_evidence",
      stages: stages(
        { validate: "done", retrieve: "done", analyze: "skipped", draft: "skipped", verify: "skipped" },
        { retrieve: "No relevant evidence (3 chunks below the relevance threshold)" },
      ),
    });
    const html = render({ run, pollError: null });

    expect(html).toContain('data-state="insufficient"');
    expect(html).toContain("Not enough evidence in your documents");
    expect(html).toContain("No relevant evidence (3 chunks below the relevance threshold)");
    expect(html).toContain('href="/documents"');
    expect(stageStatuses(html).analyze).toBe("skipped");
  });

  it("shows a failed run with its error code, copy, run id and request id", () => {
    const run = reportRun({
      status: "failed",
      error_code: "llm_rate_limited",
      stages: stages({ validate: "done", retrieve: "done", analyze: "failed", draft: "skipped", verify: "skipped" }),
    });
    const html = render({ run, pollError: null, requestId: "req-abc", onRetry: () => {} });

    expect(html).toContain('data-state="failed"');
    expect(html).toContain("rate limit");
    expect(html).toContain("llm_rate_limited");
    expect(html).toContain("request_id: req-abc");
    expect(html).toContain(`run_id: ${run.id}`);
    expect(html).toContain("Retry");
    expect(stageStatuses(html).analyze).toBe("failed");
  });

  it.each([
    ["report_timeout", "took too long"],
    ["interrupted", "interrupted"],
    ["invalid_model_output", "couldn&#x27;t be used"],
  ])("describes %s", (code, text) => {
    const html = render({ run: reportRun({ status: "failed", error_code: code }), pollError: null });
    expect(html).toContain(text);
  });

  it("shows a poll error while still running, without hiding progress", () => {
    const run = reportRun({ status: "running", stages: stages({ validate: "running" }) });
    const html = render({
      run,
      pollError: new ApiError({ code: "backend_unreachable", message: "x", requestId: "req-poll" }),
    });
    expect(html).toContain("retrying");
    expect(html).toContain("request_id: req-poll");
    expect(html).toContain('data-state="working"');
  });

  it("shows the error when the run can't be loaded at all", () => {
    const html = render({ run: null, pollError: new ApiError({ code: "not_found", message: "Report not found" }) });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Report not found");
  });

  it("shows a skeleton before the first response", () => {
    expect(render({ run: null, pollError: null })).toContain('aria-busy="true"');
  });
});

describe("format helpers", () => {
  it("labels queued and running runs", () => {
    expect(progressLabel(reportRun())).toBe("Waiting to start…");
    expect(progressLabel(reportRun({ status: "running", stages: stages({ validate: "done", retrieve: "running" }) }))).toBe(
      "Retrieve…",
    );
  });

  it("finds the reason for insufficient evidence", () => {
    const run = reportRun({
      status: "insufficient_evidence",
      stages: stages({ validate: "done", retrieve: "done", analyze: "done" }, { analyze: "No findings supported by the evidence" }),
    });
    expect(insufficientReason(run)).toBe("No findings supported by the evidence");
  });

  it("formats stage durations", () => {
    const [validate, retrieve] = stages({ validate: "done", retrieve: "running" });
    expect(stageDuration(validate)).toBe("1.3 s");
    expect(stageDuration(retrieve)).toBeNull();
    expect(stageDuration({ ...validate, finished_at: "2026-10-26T12:00:00.400Z" })).toBe("400 ms");
  });

  it("validates the question length like the contract (trimmed 10..1000)", () => {
    expect(questionProblem("   short   ")).toMatch(/At least 10/);
    expect(questionProblem("What is our remote policy?")).toBeNull();
    expect(questionProblem("x".repeat(1001))).toMatch(/At most 1000/);
  });
});
