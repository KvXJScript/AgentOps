"""The report workflow's nodes: one responsibility each, services injected.

Every node takes the current state and a `StageContext` (where it records
model attempts and a short, safe `detail` for the stage timeline) and returns
a partial state update. Nodes never touch persistence; the graph wrapper does.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel

from agentops.application.grounding import LabelledEvidence, format_evidence, label_evidence, resolve_citations
from agentops.application.reports import prompts
from agentops.application.reports.parsing import (
    DraftOutput,
    Finding,
    FindingsOutput,
    OutputParseError,
    parse_json_output,
)
from agentops.domain.models import ChatTurn, Role, TextDelta
from agentops.domain.ports import ChatModel
from agentops.domain.reports import InvalidModelOutput, Report, ReportInputInvalid, ReportSection
from agentops.domain.retrieval import Citation, Retriever

QUESTION_MIN_CHARS = 10
QUESTION_MAX_CHARS = 1000
MAX_DOCUMENT_IDS = 50
EXCERPT_MAX_CHARS = 300
# One corrective retry after an unparsable reply. Provider-level retries live
# in ResilientChatModel; this only covers "the model answered, but badly".
MAX_JSON_ATTEMPTS = 2
MAX_REPLY_CHARS = 40_000
_ECHO_MAX_CHARS = 4_000  # how much of a bad reply is shown back to the model

_MARKER_WITH_SPACE = re.compile(r"[ \t]*\[(S\d{1,3})\]")


class ReportState(TypedDict, total=False):
    question: str
    document_ids: list[UUID] | None
    evidence: list[LabelledEvidence]  # only what was actually shown to the model
    evidence_text: str
    findings: list[Finding]
    draft: DraftOutput
    report: Report
    insufficient: bool


@dataclass(slots=True)
class StageContext:
    attempts: int = 0
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    top_k: int
    min_score: float
    max_evidence_chars: int


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def validate(state: ReportState, ctx: StageContext) -> ReportState:
    question = state.get("question", "").strip()
    if not QUESTION_MIN_CHARS <= len(question) <= QUESTION_MAX_CHARS:
        raise ReportInputInvalid(f"The question must be {QUESTION_MIN_CHARS}..{QUESTION_MAX_CHARS} characters.")
    raw_ids = state.get("document_ids")
    document_ids = list(dict.fromkeys(raw_ids)) if raw_ids is not None else None
    if document_ids is not None and not 1 <= len(document_ids) <= MAX_DOCUMENT_IDS:
        raise ReportInputInvalid(f"document_ids must list 1..{MAX_DOCUMENT_IDS} documents.")
    ctx.detail = (
        "Scope: all ready documents" if document_ids is None else f"Scope: {_plural(len(document_ids), 'document')}"
    )
    return {"question": question, "document_ids": document_ids}


async def retrieve(
    state: ReportState, ctx: StageContext, *, retriever: Retriever, policy: RetrievalPolicy
) -> ReportState:
    chunks = await retriever.search(state["question"], top_k=policy.top_k, document_ids=state.get("document_ids"))
    relevant = [c for c in chunks if c.score >= policy.min_score]
    if not relevant:
        ctx.detail = (
            "No matching chunks found"
            if not chunks
            else f"No relevant evidence ({_plural(len(chunks), 'chunk')} below the relevance threshold)"
        )
        return {"evidence": [], "evidence_text": "", "insufficient": True}
    evidence_text, included = format_evidence(label_evidence(relevant), max_chars=policy.max_evidence_chars)
    ctx.detail = f"{_plural(len(chunks), 'chunk')} retrieved, {len(included)} used as evidence"
    return {"evidence": included, "evidence_text": evidence_text, "insufficient": False}


async def analyze(state: ReportState, ctx: StageContext, *, model: ChatModel) -> ReportState:
    output = await ask_json(
        model,
        FindingsOutput,
        system_prompt=prompts.ANALYZE_SYSTEM_PROMPT,
        request=prompts.analyze_request(state["question"], state["evidence_text"]),
        ctx=ctx,
    )
    valid = {item.label for item in state["evidence"]}
    grounded: list[Finding] = []
    for finding in output.findings:
        labels = [label for label in dict.fromkeys(finding.citations) if label in valid]
        if labels:
            grounded.append(Finding(statement=finding.statement, citations=labels))
    dropped = len(output.findings) - len(grounded)
    suffix = f"; {_plural(dropped, 'unsupported finding')} dropped" if dropped else ""
    if not grounded:
        ctx.detail = "No findings supported by the evidence" + suffix
        return {"findings": [], "insufficient": True}
    ctx.detail = f"{_plural(len(grounded), 'finding')} extracted" + suffix
    return {"findings": grounded, "insufficient": False}


async def draft(state: ReportState, ctx: StageContext, *, model: ChatModel) -> ReportState:
    output = await ask_json(
        model,
        DraftOutput,
        system_prompt=prompts.DRAFT_SYSTEM_PROMPT,
        request=prompts.draft_request(state["question"], state["findings"], state["evidence_text"]),
        ctx=ctx,
    )
    ctx.detail = f"{_plural(len(output.sections), 'section')} drafted"
    return {"draft": output}


async def verify(state: ReportState, ctx: StageContext) -> ReportState:
    """Deterministic: keep only markers that resolve to evidence the model actually saw."""
    evidence = state["evidence"]
    valid = {item.label for item in evidence}
    out = state["draft"]

    removed = 0

    def clean(text: str) -> str:
        nonlocal removed
        cleaned, count = strip_invalid_markers(text, valid)
        removed += count
        return cleaned

    summary = clean(out.summary)
    sections = tuple(ReportSection(heading=s.heading, body=clean(s.body)) for s in out.sections)
    resolved = resolve_citations("\n".join([summary, *(s.body for s in sections)]), evidence)
    references = tuple(_reference(item) for item in resolved.cited)

    removed_note = f"{_plural(removed, 'invalid reference')} removed" if removed else None
    if not references:
        ctx.detail = "No valid references; report withheld" + (f" ({removed_note})" if removed_note else "")
        return {"insufficient": True}
    ctx.detail = f"{_plural(len(references), 'reference')} verified" + (f", {removed_note}" if removed_note else "")
    report = Report(title=out.title, summary=summary, sections=sections, references=references)
    return {"report": report, "insufficient": False}


def strip_invalid_markers(text: str, valid: set[str]) -> tuple[str, int]:
    """Remove `[S#]` markers whose label is not in `valid` (with the space before them)."""
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group(1) in valid:
            return match.group(0)
        removed += 1
        return ""

    return _MARKER_WITH_SPACE.sub(replace, text), removed


def _reference(item: LabelledEvidence) -> Citation:
    chunk = item.chunk
    text = " ".join(chunk.text.split())
    excerpt = text if len(text) <= EXCERPT_MAX_CHARS else text[: EXCERPT_MAX_CHARS - 1].rstrip() + "…"
    return Citation(
        marker=item.label,
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_title=chunk.document_title,
        excerpt=excerpt,
        location=chunk.location,
    )


async def ask_json[T: BaseModel](
    model: ChatModel, schema: type[T], *, system_prompt: str, request: str, ctx: StageContext
) -> T:
    """Ask for a JSON object; on an unusable reply, retry once with a corrective message."""
    history: list[ChatTurn] = [ChatTurn(role=Role.USER, content=request)]
    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        ctx.attempts += 1
        raw = ""
        try:
            raw = await collect_text(model, history, system_prompt=system_prompt)
            return parse_json_output(raw, schema)
        except OutputParseError as exc:
            if attempt == MAX_JSON_ATTEMPTS:
                raise InvalidModelOutput() from exc
            echo = raw[:_ECHO_MAX_CHARS] if raw.strip() else "(reply omitted)"
            history = [
                *history,
                ChatTurn(role=Role.ASSISTANT, content=echo),
                ChatTurn(role=Role.USER, content=prompts.corrective_message(str(exc))),
            ]
    raise AssertionError("unreachable")  # pragma: no cover


async def collect_text(model: ChatModel, history: Sequence[ChatTurn], *, system_prompt: str) -> str:
    """Drain a streamed reply into one string; an over-long reply is an OutputParseError."""
    parts: list[str] = []
    size = 0
    stream = model.stream(history, system_prompt=system_prompt)
    try:
        async for chunk in stream:
            if isinstance(chunk, TextDelta):
                parts.append(chunk.text)
                size += len(chunk.text)
                if size > MAX_REPLY_CHARS:
                    raise OutputParseError("the reply was too long")
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    return "".join(parts)
