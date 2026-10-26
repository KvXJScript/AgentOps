/**
 * DEV-ONLY Phase 3 (report workflow) part of the mock backend.
 * Imported by mock-backend.mjs; see docs/api-contract.md "Phase 3".
 *
 * Runs advance one stage every MOCK_STAGE_MS (default 900 ms), computed from
 * elapsed time on each GET, so polling shows a live timeline. Evidence comes
 * from documents uploaded to the mock (mock-phase2.mjs search).
 *
 * Question triggers:
 *   "/insufficient" → retrieval finds nothing relevant (stops after retrieve)
 *   "/fail"         → draft fails with llm_unavailable
 *   "/timeout"      → analyze runs until report_timeout
 *   "/invalid"      → draft gets 2 attempts, then invalid_model_output
 *   "/noconf"       → POST returns 503 llm_not_configured (nothing stored)
 *   "/slow"         → stages take 3× longer
 */
import { randomUUID } from "node:crypto";
import { MIN_SCORE, search } from "./mock-phase2.mjs";

const STAGE_MS = Number(process.env.MOCK_STAGE_MS ?? 900);
const MODEL = process.env.MOCK_MODEL ?? "fake-mock";
const NAMES = ["validate", "retrieve", "analyze", "draft", "verify"];

/** @type {Map<string, any>} */
const runs = new Map();
const iso = (ms) => new Date(ms).toISOString();

function send(res, status, body, rid) {
  res.writeHead(status, { "Content-Type": "application/json", "X-Request-ID": rid });
  res.end(JSON.stringify(body));
}
function err(res, status, code, message, rid, details) {
  send(res, status, { error: { code, message, request_id: rid, ...(details ? { details } : {}) } }, rid);
}
async function readJson(req) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return null; }
}

function firstSentence(text) {
  const t = text.replace(/^#+\s.*$/gm, "").replace(/\s+/g, " ").trim();
  const m = /^(.{20,200}?[.!?])(\s|$)/.exec(t);
  return (m ? m[1] : t.slice(0, 160)).replace(/\[S\d+\]/g, "");
}

/** Decide the whole script of a run up front; GET reveals it as time passes. */
function plan(run) {
  const q = run.question;
  const hits = q.includes("/insufficient") ? [] : search(q, { documentIds: run.document_ids ?? undefined });
  const evidence = hits.filter((h) => h.score >= MIN_SCORE);
  const unit = q.includes("/slow") ? STAGE_MS * 3 : STAGE_MS;
  const steps = [
    { name: "validate", ms: unit * 0.4, detail: run.document_ids ? `Scope: ${run.document_ids.length} document(s)` : "Scope: all ready documents" },
    {
      name: "retrieve",
      ms: unit,
      detail: evidence.length
        ? `${hits.length} chunks retrieved, ${evidence.length} used as evidence`
        : hits.length ? `No relevant evidence (${hits.length} chunks below the relevance threshold)` : "No matching chunks found",
      end: evidence.length ? null : { status: "insufficient_evidence" },
    },
  ];
  if (!evidence.length) return { steps, evidence };
  steps.push(
    q.includes("/timeout")
      ? { name: "analyze", ms: unit * 4, end: { status: "failed", error_code: "report_timeout" } }
      : { name: "analyze", ms: unit * 1.5, detail: `${Math.min(evidence.length, 4)} findings extracted` },
  );
  if (q.includes("/timeout")) return { steps, evidence };
  if (q.includes("/fail")) steps.push({ name: "draft", ms: unit, end: { status: "failed", error_code: "llm_unavailable" } });
  else if (q.includes("/invalid")) steps.push({ name: "draft", ms: unit * 2, attempts: 2, end: { status: "failed", error_code: "invalid_model_output" } });
  else steps.push({ name: "draft", ms: unit * 1.5, detail: "3 sections drafted" });
  if (q.includes("/fail") || q.includes("/invalid")) return { steps, evidence };
  steps.push({ name: "verify", ms: unit * 0.3, detail: `${Math.min(evidence.length, 3)} references verified, 1 invalid reference removed`, end: { status: "succeeded" } });
  return { steps, evidence };
}

function buildReport(run, evidence) {
  const used = evidence.slice(0, 3);
  const refs = used.map((h, i) => ({
    marker: `S${i + 1}`, chunk_id: h.chunk_id, document_id: h.document_id, document_title: h.document_title,
    excerpt: h.excerpt, location: h.location, document_deleted: false,
  }));
  const bullets = used.map((h, i) => `- ${firstSentence(h.text)} [S${i + 1}]`).join("\n");
  return {
    title: `Report: ${run.question.replace(/\/\w+/g, "").trim().slice(0, 80)}`,
    summary: `This mock report is assembled from your documents without a model [S1].${used.length > 1 ? " Several sources agree [S1][S2]." : ""}`,
    sections: [
      { heading: "Key findings", body: bullets },
      { heading: "Details", body: `The strongest passage comes from **${used[0].document_title}** [S1].\n\nAn invented reference was removed during verification.` },
      { heading: "Open questions", body: "The documents do not say who approves exceptions; check with the policy owner." },
    ],
    references: refs,
  };
}

/** The run as of now (pure function of elapsed time + plan). */
function view(run, { withReport = true } = {}) {
  const elapsed = Date.now() - run.startedAt;
  let t = 0;
  let status = "running";
  let error_code = null;
  const stages = NAMES.map((name) => ({ name, status: "pending", attempts: 0, started_at: null, finished_at: null, detail: null }));
  let endedAt = null;
  for (const step of run.plan.steps) {
    const stage = stages.find((s) => s.name === step.name);
    if (elapsed < t) break;
    stage.status = "running";
    stage.started_at = iso(run.startedAt + t);
    stage.attempts = Math.max(1, Math.min(step.attempts ?? 1, 1 + Math.floor((elapsed - t) / (step.ms / (step.attempts ?? 1)))));
    if (elapsed < t + step.ms) break;
    t += step.ms;
    stage.finished_at = iso(run.startedAt + t);
    stage.detail = step.detail ?? null;
    if (step.end?.status === "failed") {
      stage.status = "failed";
      status = "failed";
      error_code = step.end.error_code;
      endedAt = t;
      break;
    }
    stage.status = "done";
    if (step.end) {
      status = step.end.status;
      endedAt = t;
      break;
    }
  }
  if (elapsed < 0) status = "queued";
  if (endedAt !== null) for (const s of stages) if (s.status === "pending") s.status = "skipped";
  const out = {
    id: run.id, question: run.question, document_ids: run.document_ids, status, stages, error_code, model: MODEL,
    created_at: run.created_at, updated_at: iso(run.startedAt + Math.max(0, Math.min(elapsed, endedAt ?? elapsed))),
  };
  if (withReport) out.report = status === "succeeded" ? buildReport(run, run.plan.evidence) : null;
  return out;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Returns true if the request was handled. */
export async function handleReports(req, res, parts, rid) {
  if (parts[0] !== "reports") return false;

  if (parts.length === 1 && req.method === "GET") {
    const items = [...runs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 50).map((r) => view(r, { withReport: false }));
    send(res, 200, { items }, rid);
    return true;
  }
  if (parts.length === 1 && req.method === "POST") {
    const body = await readJson(req);
    const question = typeof body?.question === "string" ? body.question.trim() : "";
    const ids = body?.document_ids;
    const idsOk = ids === undefined || ids === null || (Array.isArray(ids) && ids.length >= 1 && ids.length <= 50 && ids.every((x) => typeof x === "string" && UUID_RE.test(x)));
    if (body === null || question.length < 10 || question.length > 1000 || !idsOk) {
      err(res, 422, "validation_error", "Request validation failed", rid, [{ loc: ["body", "question"], msg: "question must be 10..1000 characters; document_ids 1..50 UUIDs", type: "value_error" }]);
      return true;
    }
    if (question.includes("/noconf")) {
      err(res, 503, "llm_not_configured", "No language model is configured on the server.", rid);
      return true;
    }
    const now = Date.now();
    const run = { id: randomUUID(), question, document_ids: ids ? [...new Set(ids)] : null, created_at: iso(now), startedAt: now + 400 };
    run.plan = plan(run);
    runs.set(run.id, run);
    const out = view(run);
    send(res, 202, { ...out, status: "queued" }, rid);
    return true;
  }
  if (parts.length === 2 && req.method === "GET") {
    const run = runs.get(parts[1]);
    if (!run) err(res, 404, "not_found", "Report not found", rid);
    else send(res, 200, view(run), rid);
    return true;
  }
  err(res, 404, "not_found", "Not found", rid);
  return true;
}

/** For mock-phase4.mjs: the run as of now (with report), or null. */
export function getReportRun(id) {
  const run = runs.get(id);
  return run ? view(run) : null;
}
