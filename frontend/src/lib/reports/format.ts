import type { ReportRun, ReportRunStatus, Stage, StageName } from "@/lib/api/types";

export const STAGE_LABELS: Record<StageName, { title: string; hint: string }> = {
  validate: { title: "Validate", hint: "Check the question and document scope" },
  retrieve: { title: "Retrieve", hint: "Search your documents for evidence" },
  analyze: { title: "Analyze", hint: "Extract key findings, each tied to evidence" },
  draft: { title: "Draft", hint: "Write the report from the findings" },
  verify: { title: "Verify", hint: "Check every citation against the evidence" },
};

export const STATUS_LABELS: Record<ReportRunStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Ready",
  insufficient_evidence: "Not enough evidence",
  failed: "Failed",
};

/** "1.2 s" / "850 ms" / null when the stage has not finished. */
export function stageDuration(stage: Stage): string | null {
  if (!stage.started_at || !stage.finished_at) return null;
  const ms = Date.parse(stage.finished_at) - Date.parse(stage.started_at);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** The stage that is running now, or the last one that did something. */
export function currentStage(run: ReportRun): Stage | null {
  const running = run.stages.find((s) => s.status === "running");
  if (running) return running;
  const touched = run.stages.filter((s) => s.status === "done" || s.status === "failed");
  return touched.at(-1) ?? null;
}

/** Why a run ended as `insufficient_evidence`: the detail of the last stage that ran. */
export function insufficientReason(run: ReportRun): string | null {
  const last = [...run.stages].reverse().find((s) => s.status === "done");
  return last?.detail ?? null;
}

export function progressLabel(run: ReportRun): string {
  if (run.status === "queued") return "Waiting to start…";
  const stage = currentStage(run);
  return stage ? `${STAGE_LABELS[stage.name].title}…` : "Starting…";
}
