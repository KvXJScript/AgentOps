import type { Citation, Report, ReportRun, Stage, StageName, StageStatus } from "@/lib/api/types";

export const RUN_ID = "7d1c7a52-3c1f-4c56-9d0e-2b8f3b9d1a01";
const T = "2026-10-26T12:00:00Z";
const NAMES: StageName[] = ["validate", "retrieve", "analyze", "draft", "verify"];

export function stages(statuses: Partial<Record<StageName, StageStatus>> = {}, detail: Partial<Record<StageName, string>> = {}): Stage[] {
  return NAMES.map((name) => {
    const status = statuses[name] ?? "pending";
    const started = status === "pending" || status === "skipped" ? null : T;
    const finished = status === "done" || status === "failed" ? "2026-10-26T12:00:01.250Z" : null;
    return { name, status, attempts: started ? 1 : 0, started_at: started, finished_at: finished, detail: detail[name] ?? null };
  });
}

export function citation(marker: string, overrides: Partial<Citation> = {}): Citation {
  return {
    marker,
    chunk_id: `chunk-${marker}`,
    document_id: `doc-${marker}`,
    document_title: `Handbook ${marker}`,
    excerpt: `Excerpt for ${marker}.`,
    location: { char_start: 0, char_end: 10, page: 3, heading_path: ["Policies"] },
    document_deleted: false,
    ...overrides,
  };
}

export function report(overrides: Partial<Report> = {}): Report {
  return {
    title: "Remote work policy",
    summary: "Staff may work remotely two days a week [S1].",
    sections: [{ heading: "Eligibility", body: "- After probation [S1][S2]\n- Manager approval [S2]" }],
    references: [citation("S1"), citation("S2", { document_deleted: true })],
    ...overrides,
  };
}

export function reportRun(overrides: Partial<ReportRun> = {}): ReportRun {
  return {
    id: RUN_ID,
    question: "What does the handbook say about remote work?",
    document_ids: null,
    status: "queued",
    stages: stages(),
    report: null,
    error_code: null,
    model: "fake-mock",
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}
