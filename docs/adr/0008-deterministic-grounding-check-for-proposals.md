# ADR 0008: Deterministic, advisory grounding check for drafted proposals

Status: accepted · Date: 2026-10-26 · Extends [ADR 0003](0003-server-side-citation-resolution.md) and
[ADR 0005](0005-approval-bound-to-payload-hash-and-writer-isolation.md)

## Context

Reports verify their references (`verify` node), but the issue body the model drafts from a report was not
checked at all. In a live run with `openai/gpt-oss-120b` the drafted body said "SEV2 follows a timed
acknowledgment and triage". The source runbook only says "SEV2 must be acknowledged within one hour". A
reviewer skimming a fluent body has no signal that one sentence has no source.

## Decision

- **The model gets the sources and must cite them.** Drafting passes the report's stored reference excerpts
  (`Report.references`, max 300 characters each, the snapshot the report UI shows) as untrusted `<evidence>`
  blocks under their original `S1..` labels, so the `[Sn]` markers already in the report text (R1) line up. It does
  not re-retrieve: the check is about what the reviewer can see, and the snapshot survives document deletion.
  The prompt requires an `[Sn]` marker on every factual sentence or bullet and puts next steps under
  `### Next step`.
- **A pure function checks the body** (`application/proposals/grounding.py`, `check_proposal_grounding`). It
  splits the body into claims (rule in the module docstring: headings, rules, code, checkbox items, action sections
  and action prefixes, questions and fragments under 3 words are not claims). It resolves markers against the
  included excerpts and flags each claim at most once:
  `unsupported` (no valid marker), `unverified_number` (a number or duration not in the excerpts it cites, with
  number words and digits normalised and units compared), or `low_overlap` (at least 3 content words, fewer than
  half found in the cited excerpts).
- **Flag, don't rewrite.** Claims are never removed: the human must see what the model said. Only invalid markers
  are stripped, as in reports.
- **Sources footer, inside the hash.** The server appends `Sources (cited excerpts …)` with `[Sn] title — heading`
  for every cited label. It is part of the issue, so it is inside `arguments` and the payload hash. The model's
  body is capped at 3000 characters, so the footer always fits in 4000. Titles have their brackets replaced so
  they can't add markers.
- **Advisory result, outside the hash.** `grounding = {checked_claims, supported, issues[]}` is stored on the
  proposal (migration 0005, nullable JSONB; `null` = drafted before the check existed). It is not part of the
  payload hash, because it doesn't change what executes. The review screen shows it, and the approve dialog
  says "N claims are not supported by the cited evidence" when there are issues. Approval stays allowed.

## Alternatives considered

- **Block approval on any issue.** The check is a heuristic with false positives, so a hard block would push
  reviewers to stop reading. The decision stays human.
- **Drop unsupported sentences automatically.** That silently changes the model output the reviewer is judging,
  and it can turn a body into something misleading by omission.
- **An LLM judge.** It isn't deterministic, costs a second call, and can itself be steered by injected
  document text. It could be added later as a second, clearly labelled signal.
- **Re-labelling with `label_evidence`.** That would renumber the labels and break the alignment with the `[Sn]`
  markers already in the report text.

## Consequences

- Caught: unmarked claims (the observed failure), invented or document-supplied markers (`[S9]` from a document
  can't resolve, because labels come only from the server's mapping), changed numbers ("30 minutes" vs "one hour"),
  and a cited excerpt that shares few words with the claim.
- Missed: a wrong claim that reuses the excerpt's words or entities ("SEV2 postmortems are reviewed by the CTO
  [S1]" next to a postmortem excerpt passes); a number that is present but attached to another entity (SEV1's
  "15 minutes" cited for SEV2); identifiers (`LT-3`, `PAGER-7731`) and dates are not verified as entities; facts
  phrased as imperatives or put under `### Next step`.
- False positives: paraphrases with other words or units ("60 minutes" for "1 hour"), numbers that are in the
  chunk but past the 300-character excerpt, and claims taken from the report text (R1) without a source marker.
- The evals (`proposal_grounding`, 4 cases) and unit tests pin the rule.
