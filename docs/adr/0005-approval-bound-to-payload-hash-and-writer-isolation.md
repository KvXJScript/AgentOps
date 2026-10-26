# ADR 0005: Approval bound to a payload hash, with structural isolation of the only write tool

Status: accepted · Date: 2026-10-26

## Context

AgentOps can create issues in the `local-tracker` MCP server, and it is the only side effect visible outside the
app. A model drafts the issue from a report that was itself built from untrusted documents. The MCP server also
advertises a decoy tool (`delete_all_issues`). The risks are: a write without a human decision, a write that
differs from what the human reviewed, a duplicate write on double-click or restart, and a future code path quietly
gaining write access.

## Decision

- **Two ports.** `IssueReader` (`list_issues`, `search_issues`) and `IssueWriter` (`create_issue`). `McpIssueWriter`
  is constructed once in `tracker_wiring.py` and passed only to `ApprovalService`. `ProposalService` (drafting) and
  `TrackerService` get only a reader. The model is never given tools.
- **Enforced by tests, not by convention.** `tests/unit/test_write_boundary.py` parses the AST of every module. It
  fails if `IssueWriter`/`McpIssueWriter` are named outside four allowed files, if `.create_issue(` is called
  anywhere except `application/proposals/approval.py`, or if the writer is passed to anything but
  `ApprovalService`.
- **Tool gate.** `McpTrackerConnection.call` checks the exact-name allowlist (`MCP_TRACKER_ALLOWED_TOOLS`, which can
  only narrow `TOOL_POLICIES`) and the declared access. It validates arguments with strict Pydantic before sending
  and validates results strictly.
- **The model fills only title, body and labels.** These go into a strict schema. The server fixes `tool`,
  `target` and `summary`, and computes `payload_hash` = sha256 of the canonical JSON `{tool, target, arguments}`.
- **Approval.** The client sends back the hash it displayed. `ApprovalService.approve` re-validates the stored
  arguments, recomputes the hash and compares both with `hmac.compare_digest`, checks `expires_at`
  (`PROPOSAL_TTL_S` = 900), then wins an atomic conditional `UPDATE pending → executing` before calling
  `create_issue(idempotency_key = proposal.id)`. The call is shielded from client disconnects. A repeat approval of
  an `executed` proposal with the same hash returns it without a second call. At startup, `recover_executing`
  re-sends stuck proposals with the same key, and the tracker store deduplicates by that key.

## Alternatives considered

- **Model tool-calling with a confirmation prompt.** The model would hold the capability, and injected text could
  steer the arguments after review.
- **Approve by id only.** The stored payload could change between review and approval without anyone noticing.
- **Rely on code review to keep writes in one place.** That doesn't survive refactors. The AST test fails the
  build instead.

## Consequences

- Concurrent approvals execute once. This is tested in unit tests and against Postgres
  (`test_concurrent_approvals_against_postgres_execute_once`).
- There are no user accounts, so "human approval" means "whoever can reach the UI" (see `docs/security.md`).
- A `create_issue` timeout is recorded as `failed` even if the issue was created. A manual re-approval isn't
  possible from `failed`, and an automatic retry by key is not implemented.
- Adding a second write tool means adding a policy, a writer port and an extension of the boundary test,
  deliberately.
- The hash binds what executes, not whether it is true. Drafted bodies get an advisory grounding check whose
  result is stored next to the proposal but outside the hash
  ([ADR 0008](0008-deterministic-grounding-check-for-proposals.md)).
