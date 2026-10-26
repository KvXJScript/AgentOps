#!/usr/bin/env node
/**
 * DEV-ONLY in-memory mock of the AgentOps API (docs/api-contract.md).
 * Not part of the Next.js app or its build. Use when the real backend is not
 * running:   npm run mock:backend   (listens on MOCK_PORT, default 8000)
 *
 * Message triggers for exercising UI states:
 *   "/rate"     → streams a few tokens then `error` llm_rate_limited (incomplete)
 *   "/fail"     → `error` llm_unavailable before any token (failed)
 *   "/timeout"  → `error` llm_timeout before any token
 *   "/noconf"   → pre-stream 503 llm_not_configured envelope
 *   "/long"     → long markdown answer, slow tokens (test Stop + auto-scroll)
 */
import http from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.MOCK_PORT ?? 8000);
const MODEL = process.env.MOCK_MODEL ?? "fake-mock";
const TOKEN_DELAY_MS = Number(process.env.MOCK_TOKEN_DELAY_MS ?? 40);

/** @type {Map<string, {id:string,title:string,created_at:string,updated_at:string,messages:any[]}>} */
const conversations = new Map();
const now = () => new Date().toISOString();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function send(res, status, body, rid) {
  res.writeHead(status, { "Content-Type": "application/json", "X-Request-ID": rid });
  res.end(body === undefined ? undefined : JSON.stringify(body));
}
function err(res, status, code, message, rid, extra = {}) {
  send(res, status, { error: { code, message, request_id: rid, ...extra } }, rid);
}
const publicConv = ({ messages: _m, ...c }) => c;

async function readJson(req) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return null; }
}

function answerFor(content) {
  if (content.includes("/long")) {
    return [
      "## A longer answer",
      "",
      "This is a **mock** response streamed slowly so you can test the *Stop* button and auto-scroll.",
      "",
      ...Array.from({ length: 12 }, (_, i) => `${i + 1}. Point number ${i + 1} with some \`inline code\` and a [link](https://example.com).`),
      "",
      "```python\ndef hello():\n    return 'world'\n```",
      "",
      "| Column | Value |\n|---|---|\n| a | 1 |\n| b | 2 |",
      "",
      "<script>alert('raw html must not render')</script>",
    ].join("\n");
  }
  return `You said: "${content}".\n\nThis is the **dev mock backend** — no real model is involved.`;
}

const server = http.createServer(async (req, res) => {
  const rid = req.headers["x-request-id"] || randomUUID();
  const url = new URL(req.url ?? "/", `http://localhost:${PORT}`);
  const parts = url.pathname.replace(/^\/api\/v1\/?/, "").split("/").filter(Boolean);

  if (!url.pathname.startsWith("/api/v1")) return err(res, 404, "not_found", "Not found", rid);
  if (req.method === "GET" && parts[0] === "health") return send(res, 200, { status: "ok" }, rid);
  if (req.method === "GET" && parts[0] === "ready") {
    return send(res, 200, { status: "ready", checks: { database: "ok", llm: "configured" }, llm: { provider: "fake", model: MODEL } }, rid);
  }
  if (parts[0] !== "conversations") return err(res, 404, "not_found", "Not found", rid);

  if (parts.length === 1 && req.method === "GET") {
    const items = [...conversations.values()].map(publicConv).sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 100);
    return send(res, 200, { items }, rid);
  }
  if (parts.length === 1 && req.method === "POST") {
    const body = await readJson(req);
    if (body === null || (body.title !== undefined && (typeof body.title !== "string" || !body.title.length || body.title.length > 200))) {
      return err(res, 422, "validation_error", "Invalid title", rid);
    }
    const t = now();
    const c = { id: randomUUID(), title: body.title ?? "New conversation", created_at: t, updated_at: t, messages: [] };
    conversations.set(c.id, c);
    return send(res, 201, publicConv(c), rid);
  }

  const conv = conversations.get(parts[1]);
  if (!conv) return err(res, 404, "not_found", "Conversation not found", rid);

  if (parts.length === 2 && req.method === "GET") return send(res, 200, conv, rid);
  if (parts.length === 2 && req.method === "DELETE") {
    conversations.delete(conv.id);
    res.writeHead(204, { "X-Request-ID": rid });
    return res.end();
  }
  if (parts.length === 3 && parts[2] === "messages" && req.method === "POST") {
    const body = await readJson(req);
    const content = typeof body?.content === "string" ? body.content.trim() : "";
    if (!content || content.length > 4000) return err(res, 422, "validation_error", "Content must be 1..4000 characters", rid);
    if (content.includes("/noconf")) return err(res, 503, "llm_not_configured", "No LLM provider is configured", rid);

    const userMessage = { id: randomUUID(), conversation_id: conv.id, role: "user", content, status: "complete", error_code: null, model: null, created_at: now() };
    conv.messages.push(userMessage);
    if (conv.title === "New conversation") conv.title = content.slice(0, 60);
    conv.updated_at = now();
    const assistantId = randomUUID();

    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Request-ID": rid });
    const write = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    let closed = false;
    res.on("close", () => { closed = true; });

    write("message.created", { user_message: userMessage, assistant_message_id: assistantId });
    const finish = (status, text, error_code) => {
      const m = { id: assistantId, conversation_id: conv.id, role: "assistant", content: text, status, error_code, model: MODEL, created_at: now() };
      conv.messages.push(m);
      conv.updated_at = now();
      return m;
    };

    const failEarly = content.includes("/fail") ? "llm_unavailable" : content.includes("/timeout") ? "llm_timeout" : null;
    if (failEarly) {
      await sleep(400);
      finish("failed", "", failEarly);
      write("error", { code: failEarly, message: "The model provider failed", retryable: true, request_id: rid, message_id: assistantId });
      return res.end();
    }

    const tokens = answerFor(content).match(/\S+\s*|\s+/g) ?? [];
    const delay = content.includes("/long") ? TOKEN_DELAY_MS * 3 : TOKEN_DELAY_MS;
    let text = "";
    for (const [i, tok] of tokens.entries()) {
      if (closed) { finish("incomplete", text, null); return; }
      if (content.includes("/rate") && i === 4) {
        finish("incomplete", text, "llm_rate_limited");
        write("error", { code: "llm_rate_limited", message: "Rate limited", retryable: true, request_id: rid, message_id: assistantId, retry_after_s: 17 });
        return res.end();
      }
      await sleep(delay);
      text += tok;
      write("token", { delta: tok });
    }
    const m = finish("complete", text, null);
    write("done", { message: m, usage: { input_tokens: content.length, output_tokens: tokens.length } });
    return res.end();
  }
  return err(res, 404, "not_found", "Not found", rid);
});

server.listen(PORT, () => console.log(`[mock-backend] listening on http://localhost:${PORT} (model ${MODEL})`));
