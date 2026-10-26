/**
 * DEV-ONLY Phase 2 (Documents and RAG) part of the mock backend.
 * Imported by mock-backend.mjs; see docs/api-contract.md "Phase 2".
 *
 * Upload triggers (by filename):
 *   "*fail-embed*"  → 201 Document with status failed / embedding_failed (Retry then succeeds)
 *   "*corrupt*"     → 422 corrupt_document envelope (also: a .pdf without a %PDF header)
 *   "*slow*"        → ~4 s of processing (to see the processing state)
 * Chat triggers (with "Use my documents"):
 *   "/invalid"      → answer also contains an invented marker [S9] (must stay plain text)
 *   "/nocite"       → model answers without valid citations → grounding insufficient_evidence
 *   a question with no word overlap with your documents → retrieval insufficient → fixed answer
 */
import { createHash, randomUUID } from "node:crypto";

export const MAX_UPLOAD_BYTES = Number(process.env.MOCK_MAX_UPLOAD_BYTES ?? 10 * 1024 * 1024);
export const TOP_K = 5;
export const MIN_SCORE = Number(process.env.MOCK_MIN_SCORE ?? 0.18);
export const EMBEDDING_MODEL = "mock-bow-cosine-v1";
export const INSUFFICIENT_ANSWER =
  "I couldn't find enough evidence in your documents to answer that. Try rephrasing, or upload a document that covers it.";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();

/** @type {Map<string, any>} */
export const documents = new Map();
/** @type {Map<string, any[]>} document id → chunks (with full text) */
const chunksByDoc = new Map();
/** @type {Map<string, any>} assistant message id → retrieval trace */
export const retrievalTraces = new Map();
/** Raw bytes kept so failed documents can be retried. */
const sources = new Map();

const MEDIA = { md: "text/markdown", txt: "text/plain", pdf: "application/pdf" };

// --- helpers -----------------------------------------------------------------

function sendJson(res, status, body, rid, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", "X-Request-ID": rid, ...headers });
  res.end(body === undefined ? undefined : JSON.stringify(body));
}
function sendErr(res, status, code, message, rid) {
  sendJson(res, status, { error: { code, message, request_id: rid } }, rid);
}
async function readBuffer(req) {
  const parts = [];
  for await (const c of req) parts.push(c);
  return Buffer.concat(parts);
}
async function readJson(req) {
  const b = await readBuffer(req);
  if (!b.length) return {};
  try { return JSON.parse(b.toString("utf8")); } catch { return null; }
}

/** Minimal multipart/form-data parser (enough for `file` + `title`). */
function parseMultipart(buf, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType ?? "");
  if (!m) return null;
  const boundary = Buffer.from(`--${m[1] ?? m[2]}`);
  const fields = {};
  let pos = buf.indexOf(boundary);
  while (pos !== -1) {
    const start = pos + boundary.length;
    if (buf.slice(start, start + 2).toString() === "--") break;
    const headerEnd = buf.indexOf("\r\n\r\n", start);
    if (headerEnd === -1) break;
    const headers = buf.slice(start + 2, headerEnd).toString("utf8");
    const next = buf.indexOf(boundary, headerEnd);
    if (next === -1) break;
    const body = buf.slice(headerEnd + 4, next - 2); // strip trailing CRLF
    const name = /name="([^"]*)"/i.exec(headers)?.[1];
    const filename = /filename="([^"]*)"/i.exec(headers)?.[1];
    const type = /content-type:\s*([^\r\n]+)/i.exec(headers)?.[1]?.trim();
    if (name) fields[name] = filename !== undefined ? { filename, type, data: body } : body.toString("utf8");
    pos = next;
  }
  return fields;
}

const STOP = new Set("the and for are but not you all any can had her was one our out has have this that with from what when where which who why how does into about your they them then than there their these those will would should could also just only very".split(" "));
function terms(text) {
  return (text.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? []).filter((w) => !STOP.has(w));
}
function vector(text) {
  const v = new Map();
  for (const t of terms(text)) v.set(t, (v.get(t) ?? 0) + 1);
  return v;
}
function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  for (const [k, x] of a) { na += x * x; const y = b.get(k); if (y) dot += x * y; }
  for (const y of b.values()) nb += y * y;
  return na && nb ? dot / Math.sqrt(na * nb) : 0;
}

/** Extract text. PDFs: strings inside (...) Tj/TJ operators, one page per "/Type /Page". */
function extract(ext, data) {
  if (ext !== "pdf") return [{ page: null, text: data.toString("utf8") }];
  const raw = data.toString("latin1");
  if (!raw.startsWith("%PDF")) throw Object.assign(new Error("not a pdf"), { code: "corrupt_document" });
  const pages = raw.split(/\/Type\s*\/Page(?!s)/).slice(1);
  const out = (pages.length ? pages : [raw]).map((p, i) => ({
    page: i + 1,
    text: [...p.matchAll(/\(((?:\\.|[^\\)])*)\)\s*T[jJ]/g)].map((x) => x[1].replace(/\\(.)/g, "$1")).join(" "),
  }));
  return out;
}

/** Heading-aware chunking (~500 chars), with char offsets and heading_path. */
function chunk(docId, pages, ext) {
  const chunks = [];
  let offset = 0;
  for (const { page, text } of pages) {
    const path = [];
    let buf = "";
    let bufStart = offset;
    const flush = () => {
      const t = buf.trim();
      if (t) chunks.push({ id: randomUUID(), document_id: docId, ordinal: chunks.length, text: t,
        location: { char_start: bufStart, char_end: bufStart + buf.length, page, heading_path: [...path] } });
      bufStart += buf.length;
      buf = "";
    };
    for (const line of text.split(/(?<=\n)/)) {
      const h = ext === "md" ? /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line.trimEnd()) : null;
      if (h) {
        flush();
        path.length = Math.min(path.length, h[1].length - 1);
        path[h[1].length - 1] = h[2];
        path.length = h[1].length;
        bufStart += line.length;
        continue;
      }
      if (buf.length + line.length > 500 && buf.trim()) flush();
      buf += line;
    }
    flush();
    offset += text.length;
  }
  return chunks;
}

async function ingest(doc, ext, data) {
  doc.status = "processing";
  doc.updated_at = now();
  await sleep(doc.filename.includes("slow") ? 4000 : 500 + Math.min(1500, data.length / 2000));
  if (doc.filename.includes("fail-embed") && !doc._retried) {
    doc.status = "failed";
    doc.error_code = "embedding_failed";
    doc.updated_at = now();
    return;
  }
  const chunks = chunk(doc.id, extract(ext, data), ext);
  chunksByDoc.set(doc.id, chunks.map((c) => ({ ...c, vec: vector(`${c.location.heading_path.join(" ")} ${c.text}`) })));
  Object.assign(doc, { status: "ready", error_code: null, chunk_count: chunks.length, embedding_model: EMBEDDING_MODEL, updated_at: now() });
}

const publicDoc = ({ _retried, ...d }) => d;
const excerpt = (t) => (t.length > 300 ? `${t.slice(0, 297).trimEnd()}…` : t);

/** Contract scope rule: ready, non-deleted, ∩ document_ids. */
export function search(query, { topK = TOP_K, documentIds } = {}) {
  const q = vector(query);
  const scored = [];
  for (const doc of documents.values()) {
    if (doc.status !== "ready") continue;
    if (documentIds?.length && !documentIds.includes(doc.id)) continue;
    for (const c of chunksByDoc.get(doc.id) ?? []) {
      // Squash bag-of-words cosine into a range that looks like real embeddings.
      const raw = cosine(q, c.vec);
      scored.push({ c, doc, score: Math.min(0.97, raw === 0 ? 0.04 + Math.random() * 0.08 : 0.2 + raw * 0.9) });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, topK).map(({ c, doc, score }, i) => ({
    chunk_id: c.id, document_id: doc.id, document_title: doc.title, rank: i + 1,
    score: Math.round(score * 1000) / 1000, excerpt: excerpt(c.text), location: c.location, text: c.text,
  }));
}

/** Refresh `document_deleted` on stored citations (contract: deleted sources keep their excerpt). */
export function withCitationState(message) {
  if (!message.citations?.length) return message;
  return { ...message, citations: message.citations.map((c) => ({ ...c, document_deleted: !documents.has(c.document_id) })) };
}

function firstSentence(text) {
  const t = text.replace(/^#+\s.*$/gm, "").replace(/\s+/g, " ").trim();
  const m = /^(.{20,220}?[.!?])(\s|$)/.exec(t);
  return (m ? m[1] : t.slice(0, 160)).replace(/\[S\d+\]/g, "");
}

/**
 * RAG answer for the mock. Returns { retrieval, sufficient, answer, finalize(text) }.
 * finalize resolves markers against this request's labels, like the backend.
 */
export function ragAnswer(content, documentIds) {
  const hits = search(content, { documentIds });
  const sufficient = hits.some((h) => h.score >= MIN_SCORE);
  const evidence = hits.filter((h) => h.score >= MIN_SCORE);
  const labels = new Map(evidence.map((h, i) => [`S${i + 1}`, h]));
  let answer = INSUFFICIENT_ANSWER;
  if (sufficient) {
    if (content.includes("/nocite")) {
      answer = "From general knowledge, this is usually handled by a configuration flag — but I did not check your documents.";
    } else {
      const lines = evidence.slice(0, 3).map((h, i) => `- ${firstSentence(h.text)} [S${i + 1}]`);
      answer = [
        `Here is what your documents say (mock answer, no model involved):`,
        "",
        ...lines,
        "",
        evidence.length > 1 ? `The first two passages agree [S1][S2].` : `That is the only relevant passage [S1].`,
        content.includes("/invalid") ? "\nA document might claim more [S9], but that label was never provided." : "",
      ].join("\n").trim();
    }
  }
  const finalize = (text) => {
    const cited = [];
    const seen = new Set();
    for (const m of text.matchAll(/\[(S\d{1,3})\]/g)) {
      const label = m[1];
      if (seen.has(label) || !labels.has(label)) continue;
      seen.add(label);
      const h = labels.get(label);
      cited.push({ marker: label, chunk_id: h.chunk_id, document_id: h.document_id, document_title: h.document_title,
        excerpt: h.excerpt, location: h.location, document_deleted: false });
    }
    return {
      citations: cited,
      grounding: !sufficient ? "insufficient_evidence" : cited.length ? "grounded" : "insufficient_evidence",
    };
  };
  const strip = ({ text: _t, ...rest }) => rest;
  return { chunks: hits.map(strip), sufficient, answer, finalize, trace: { chunks: hits.map(strip), top_k: TOP_K, min_score: MIN_SCORE, embedding_model: EMBEDDING_MODEL } };
}

// --- routes ------------------------------------------------------------------

/** Returns true if the request was handled. */
export async function handlePhase2(req, res, parts, rid) {
  if (parts[0] === "messages" && parts.length === 3 && parts[2] === "retrieval" && req.method === "GET") {
    const t = retrievalTraces.get(parts[1]);
    if (!t) return sendErr(res, 404, "not_found", "No retrieval for this message", rid), true;
    return sendJson(res, 200, t, rid), true;
  }
  if (parts[0] === "retrieval" && parts[1] === "search" && req.method === "POST") {
    const body = await readJson(req);
    const q = typeof body?.query === "string" ? body.query.trim() : "";
    if (!q || q.length > 1000) return sendErr(res, 422, "validation_error", "query must be 1..1000 characters", rid), true;
    const items = search(q, { topK: Math.min(20, Math.max(1, Number(body.top_k ?? TOP_K))), documentIds: body.document_ids }).map(({ text: _t, ...r }) => r);
    return sendJson(res, 200, { items, embedding_model: EMBEDDING_MODEL }, rid), true;
  }
  if (parts[0] !== "documents") return false;

  if (parts.length === 1 && req.method === "GET") {
    const items = [...documents.values()].map(publicDoc).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return sendJson(res, 200, { items }, rid), true;
  }
  if (parts.length === 1 && req.method === "POST") {
    const declared = Number(req.headers["content-length"] ?? 0);
    if (declared > MAX_UPLOAD_BYTES + 64 * 1024) {
      req.resume();
      return sendErr(res, 413, "file_too_large", `Files can be at most ${MAX_UPLOAD_BYTES} bytes`, rid), true;
    }
    const fields = parseMultipart(await readBuffer(req), req.headers["content-type"]);
    const file = fields?.file;
    if (!file || typeof file === "string") return sendErr(res, 422, "validation_error", "Field 'file' is required", rid), true;
    const ext = (/\.([^.]+)$/.exec(file.filename)?.[1] ?? "").toLowerCase();
    if (!MEDIA[ext]) return sendErr(res, 415, "unsupported_file_type", "Only .md, .txt and .pdf files are supported", rid), true;
    if (file.data.length > MAX_UPLOAD_BYTES) return sendErr(res, 413, "file_too_large", "File is too large", rid), true;
    if (file.filename.includes("corrupt")) return sendErr(res, 422, "corrupt_document", "The file could not be parsed", rid), true;
    let pages;
    try { pages = extract(ext, file.data); } catch { return sendErr(res, 422, "corrupt_document", "The file could not be parsed", rid), true; }
    if (!pages.some((p) => p.text.trim())) return sendErr(res, 422, "empty_document", "No extractable text", rid), true;

    const checksum = createHash("sha256").update(file.data).digest("hex");
    const existing = [...documents.values()].find((d) => d.checksum_sha256 === checksum);
    if (existing?.status === "ready") return sendJson(res, 200, publicDoc(existing), rid, { "X-Duplicate": "true" }), true;
    if (existing?.status === "failed") {
      existing._retried = true;
      await ingest(existing, ext, sources.get(existing.id));
      return sendJson(res, 200, publicDoc(existing), rid), true;
    }
    const t = now();
    const title = typeof fields.title === "string" && fields.title.trim() ? fields.title.trim().slice(0, 200) : file.filename.replace(/\.[^.]+$/, "");
    const doc = { id: randomUUID(), title, filename: file.filename, media_type: MEDIA[ext], size_bytes: file.data.length,
      checksum_sha256: checksum, status: "pending", error_code: null, chunk_count: 0, embedding_model: null, deprecated: false, created_at: t, updated_at: t };
    documents.set(doc.id, doc);
    sources.set(doc.id, file.data);
    await ingest(doc, ext, file.data);
    return sendJson(res, 201, publicDoc(doc), rid), true;
  }

  const doc = documents.get(parts[1]);
  if (!doc) return sendErr(res, 404, "not_found", "Document not found", rid), true;

  if (parts.length === 2 && req.method === "GET") return sendJson(res, 200, publicDoc(doc), rid), true;
  if (parts.length === 2 && req.method === "DELETE") {
    documents.delete(doc.id);
    chunksByDoc.delete(doc.id);
    sources.delete(doc.id);
    res.writeHead(204, { "X-Request-ID": rid });
    return res.end(), true;
  }
  if (parts.length === 3 && parts[2] === "retry" && req.method === "POST") {
    if (doc.status !== "failed") return sendErr(res, 409, "document_not_ready", "Only failed documents can be retried", rid), true;
    doc._retried = true;
    const ext = (/\.([^.]+)$/.exec(doc.filename)?.[1] ?? "").toLowerCase();
    await ingest(doc, ext, sources.get(doc.id));
    return sendJson(res, 200, publicDoc(doc), rid), true;
  }
  if (parts.length === 4 && parts[2] === "chunks" && req.method === "GET") {
    const c = (chunksByDoc.get(doc.id) ?? []).find((x) => x.id === parts[3]);
    if (!c) return sendErr(res, 404, "not_found", "Chunk not found", rid), true;
    const { vec: _v, ...pub } = c;
    return sendJson(res, 200, pub, rid), true;
  }
  return sendErr(res, 404, "not_found", "Not found", rid), true;
}
