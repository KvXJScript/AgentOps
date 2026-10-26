# ADR 0003: Server-side citation resolution with per-request S-labels and untrusted evidence blocks

Status: accepted · Date: 2026-10-26

## Context

Grounded answers and reports must cite real chunks. Models invent references, and uploaded documents can contain
prompt-injection text, including fake delimiters and fake labels such as `[S9]`. Citations must also stay readable
after their document is deleted.

## Decision

- Retrieved chunks above `RETRIEVAL_MIN_SCORE` are labelled `S1..Sn` per request (`grounding.label_evidence`).
  `format_evidence` renders them as `<evidence label="S1" source="…">` blocks up to the character budget
  (`RAG_CONTEXT_MAX_CHARS` for chat, `REPORT_MAX_EVIDENCE_CHARS` for reports). It returns the evidence that was
  actually *included*. Look-alike `<evidence>` tags inside document text become `[removed tag]`, and titles are
  attribute-escaped.
- The system prompt says evidence is untrusted data, never instructions, and that the model has no tools
  (`UNTRUSTED_EVIDENCE_RULES`, `GROUNDED_INSTRUCTIONS`). Tracker issues and the report get the same block format
  (`I1…`, `R1`) in proposal drafting.
- The label → chunk mapping never leaves the server. After the stream ends, `finalize_answer` (chat) and the
  deterministic `verify` node (reports) resolve markers against **the included evidence of this request only**.
  Unknown markers are removed from the stored text and counted in logs. Markers from earlier turns are stripped
  from chat history.
- If nothing clears the threshold, chat returns a fixed answer without calling the model, and the report ends
  `insufficient_evidence`. An answer whose markers all fail to resolve is stored as `insufficient_evidence`. A
  report with no valid reference is withheld.
- Resolved citations are stored as JSONB snapshots (ids, title, excerpt of at most 300 chars, location).
  `document_deleted` is computed when they are read.

## Alternatives considered

- **Let the model emit chunk ids or URLs.** Ids are easy to hallucinate and leak internal identifiers, and the
  server would still have to validate them.
- **Client-side resolution.** The browser doesn't have the mapping, and sending it would let a client forge
  citations.
- **Structured/JSON answers for chat.** That breaks token streaming. Inline markers stream naturally and resolve
  afterwards (chips appear at `done`).
- **Foreign keys from citations to chunks.** Deletion would either be blocked or erase the evidence trail.

## Consequences

- Every citation shown points to a chunk the model actually saw. The `citations` and `injection` eval categories
  (10 cases) check this independently of the code under test.
- Prompt injection is contained, not prevented: injected text can still skew wording, but it cannot create
  citations or capabilities.
- Chunks budgeted out of the prompt are visible in the retrieval trace but can never be cited, by design.
