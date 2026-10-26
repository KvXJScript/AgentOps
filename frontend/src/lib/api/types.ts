/** Types mirroring docs/api-contract.md (v1). Keep in sync with the contract. */

export type ContractErrorCode =
  | "validation_error"
  | "not_found"
  | "llm_not_configured"
  | "llm_auth_failed"
  | "llm_rate_limited"
  | "llm_timeout"
  | "llm_unavailable"
  /** Stored on an assistant message when the client disconnected mid-stream; never an HTTP error. */
  | "cancelled"
  | "internal_error"
  // Phase 2 — documents and RAG
  | "unsupported_file_type"
  | "file_too_large"
  | "empty_document"
  | "corrupt_document"
  | "document_not_ready"
  | "embedding_failed"
  // Phase 3 — report runs (stored on a run, except retrieval_not_configured which is a 503)
  | "report_timeout"
  | "interrupted"
  | "invalid_model_output"
  | "retrieval_not_configured"
  // Phase 4 — MCP tracker and approvals
  | "mcp_unavailable"
  | "mcp_timeout"
  | "invalid_tool_output"
  | "report_not_ready"
  | "proposal_not_pending"
  | "payload_mismatch"
  | "proposal_expired";

/**
 * Codes produced by the frontend itself (never sent by the backend):
 * - `backend_unreachable`: the Next.js proxy could not reach BACKEND_URL
 * - `network_error`: the browser could not reach the Next.js server
 * - `stream_interrupted`: the SSE stream ended without a `done`/`error` event
 * - `unknown_error`: a response we could not interpret
 */
export type ClientErrorCode =
  | "backend_unreachable"
  | "network_error"
  | "stream_interrupted"
  | "unknown_error";

export type ErrorCode = ContractErrorCode | ClientErrorCode;

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string;
    details?: unknown;
    retry_after_s?: number;
  };
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export type MessageRole = "user" | "assistant";
export type MessageStatus = "complete" | "incomplete" | "failed";

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  error_code: string | null;
  model: string | null;
  created_at: string;
  /** Phase 2. Optional so a Phase 1 backend (which omits them) still type-checks; absent = "none" / []. */
  grounding?: Grounding;
  citations?: Citation[];
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

export interface ReadyResponse {
  status: "ready" | "not_ready";
  checks: {
    database: "ok" | "error";
    llm: "configured" | "not_configured";
  };
  llm?: { provider: string; model: string };
}

export interface Usage {
  input_tokens: number | null;
  output_tokens: number | null;
}

export type ChatStreamEvent =
  | {
      type: "message.created";
      user_message: Message;
      assistant_message_id: string;
    }
  | { type: "retrieval"; chunks: RetrievedChunk[]; sufficient: boolean }
  | { type: "token"; delta: string }
  | { type: "done"; message: Message; usage: Usage }
  | {
      type: "error";
      code: string;
      message: string;
      retryable: boolean;
      request_id: string | null;
      message_id: string | null;
      retry_after_s?: number;
    };

export const MAX_MESSAGE_LENGTH = 4000;

// ---------------------------------------------------------------------------
// Phase 2 — Documents and RAG
// ---------------------------------------------------------------------------

export type DocumentMediaType = "text/markdown" | "text/plain" | "application/pdf";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface Document {
  id: string;
  title: string;
  filename: string;
  media_type: DocumentMediaType;
  size_bytes: number;
  checksum_sha256: string;
  status: DocumentStatus;
  error_code: string | null;
  chunk_count: number;
  embedding_model: string | null;
  /** From `status: deprecated` front matter; ranked slightly lower in retrieval. */
  deprecated: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChunkLocation {
  char_start: number;
  char_end: number;
  page: number | null;
  heading_path: string[];
}

export interface Chunk {
  id: string;
  document_id: string;
  ordinal: number;
  text: string;
  location: ChunkLocation;
}

export interface RetrievedChunk {
  chunk_id: string;
  document_id: string;
  document_title: string;
  rank: number;
  /** Cosine similarity, higher = better. */
  score: number;
  /** ≤ 300 chars. */
  excerpt: string;
  location: ChunkLocation;
}

export type Grounding = "none" | "grounded" | "insufficient_evidence";

export interface Citation {
  /** "S1" */
  marker: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  excerpt: string;
  location: ChunkLocation;
  document_deleted: boolean;
}

export interface MessageRetrieval {
  chunks: RetrievedChunk[];
  cited_chunk_ids: string[];
  top_k: number;
  min_score: number;
  embedding_model: string;
}

export interface UploadResult {
  document: Document;
  /** `X-Duplicate: true` — the same file was already ingested; no new chunks. */
  duplicate: boolean;
  /** HTTP 201 (new) or 200 (duplicate / idempotent retry of a failed one). */
  status: number;
}

// ---------------------------------------------------------------------------
// Phase 3 — Report workflow
// ---------------------------------------------------------------------------

export type StageName = "validate" | "retrieve" | "analyze" | "draft" | "verify";
export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";

export interface Stage {
  name: StageName;
  status: StageStatus;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  /** Short and safe, e.g. "8 chunks retrieved", "1 invalid reference removed". */
  detail: string | null;
}

export interface ReportSection {
  heading: string;
  /** Markdown; may contain [S1] markers. */
  body: string;
}

export interface Report {
  title: string;
  summary: string;
  sections: ReportSection[];
  references: Citation[];
}

export type ReportRunStatus = "queued" | "running" | "succeeded" | "insufficient_evidence" | "failed";

export interface ReportRun {
  id: string;
  question: string;
  document_ids: string[] | null;
  status: ReportRunStatus;
  /** Always all five, in order. */
  stages: Stage[];
  /** Set only when succeeded; omitted in list items. */
  report?: Report | null;
  error_code: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
}

export const REPORT_QUESTION_MIN = 10;
export const REPORT_QUESTION_MAX = 1000;

// ---------------------------------------------------------------------------
// Phase 4 — MCP integration (local tracker) and approval boundary
// ---------------------------------------------------------------------------

export type IssueStatus = "open" | "closed";

/** Tracker issue. Every text field is untrusted tool output: render as plain text only. */
export interface Issue {
  /** "LT-12" */
  id: string;
  title: string;
  body: string;
  labels: string[];
  status: IssueStatus;
  created_at: string;
}

export interface ToolInfo {
  name: string;
  access: "read" | "write";
  requires_approval: boolean;
  description: string;
}

export interface TrackerInfo {
  server: {
    name: string;
    label: string;
    transport: string;
    status: "connected" | "unavailable";
  };
  tools: ToolInfo[];
}

export interface IssueList {
  items: Issue[];
  truncated: boolean;
}

export const TRACKER_QUERY_MAX = 200;
export const TRACKER_LIMIT_MAX = 50;

export type ProposalLabel = "bug" | "docs" | "follow-up" | "question";
export type ProposalStatus = "pending" | "executing" | "executed" | "rejected" | "expired" | "failed";
export const PROPOSAL_STATUSES: readonly ProposalStatus[] = [
  "pending",
  "executing",
  "executed",
  "rejected",
  "expired",
  "failed",
];

export interface ProposalArguments {
  /** 5..120 chars */
  title: string;
  /** ≤ 4000 chars */
  body: string;
  /** ≤ 3 */
  labels: ProposalLabel[];
}

export type GroundingReason = "unsupported" | "unverified_number" | "low_overlap";

export interface GroundingIssue {
  /** The claim as the model wrote it (markers removed). Model output: render as plain text. */
  claim: string;
  reason: GroundingReason;
  /** Valid [Sn] markers the claim cites (empty for `unsupported`). */
  markers: string[];
  /** For `unverified_number`: the quantities not found in the cited excerpts. */
  numbers: string[];
}

/** Advisory result of the server's deterministic grounding check. Not part of payload_hash. */
export interface ProposalGrounding {
  checked_claims: number;
  supported: number;
  issues: GroundingIssue[];
}

export interface ActionProposal {
  id: string;
  /** Fixed by the server, never chosen by the model. */
  tool: "create_issue";
  target: "local-tracker";
  arguments: ProposalArguments;
  /** One human sentence of what will happen. */
  summary: string;
  /** sha256 hex of canonical JSON {tool, target, arguments}. */
  payload_hash: string;
  status: ProposalStatus;
  source: { type: "report"; id: string };
  /** Read via search_issues while drafting; untrusted, display only. */
  related_issues: Issue[];
  created_at: string;
  /** created_at + 15 min. Pending proposals past this read back as `expired`. */
  expires_at: string;
  decided_at: string | null;
  result: { issue_id: string } | null;
  error_code: string | null;
  /** null for proposals drafted before the grounding check existed. */
  grounding: ProposalGrounding | null;
}
