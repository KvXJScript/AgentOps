"""Each report node in isolation."""

from __future__ import annotations

import uuid

import pytest

from agentops.application.grounding import format_evidence, label_evidence
from agentops.application.reports import nodes
from agentops.application.reports.nodes import ReportState, RetrievalPolicy, StageContext
from agentops.application.reports.parsing import (
    DraftOutput,
    Finding,
    FindingsOutput,
    OutputParseError,
    parse_json_output,
)
from agentops.domain.errors import LLMAuthFailed
from agentops.domain.models import Role
from agentops.domain.reports import InvalidModelOutput, ReportInputInvalid
from tests.fakes import ScriptedChatModel
from tests.report_fakes import FakeRetriever, chunk, draft_json, findings_json

POLICY = RetrievalPolicy(top_k=5, min_score=0.5, max_evidence_chars=10_000)


def _state_with_evidence(*texts: str) -> ReportState:
    text, included = format_evidence(label_evidence([chunk(t) for t in texts]), max_chars=10_000)
    return {"question": "What is the remote work policy?", "evidence": included, "evidence_text": text}


# validate


async def test_validate_trims_and_dedupes_scope() -> None:
    doc = uuid.uuid4()
    ctx = StageContext()

    update = await nodes.validate({"question": "  What is the policy?  ", "document_ids": [doc, doc]}, ctx)

    assert update == {"question": "What is the policy?", "document_ids": [doc]}
    assert ctx.detail == "Scope: 1 document"


async def test_validate_without_scope_means_all_documents() -> None:
    ctx = StageContext()
    update = await nodes.validate({"question": "What is the policy?", "document_ids": None}, ctx)
    assert update["document_ids"] is None
    assert ctx.detail == "Scope: all ready documents"


@pytest.mark.parametrize(
    "state",
    [
        {"question": "short"},
        {"question": "x" * 1001},
        {"question": "What is the policy?", "document_ids": []},
        {"question": "What is the policy?", "document_ids": [uuid.uuid4() for _ in range(51)]},
    ],
)
async def test_validate_rejects_invalid_input(state: ReportState) -> None:
    with pytest.raises(ReportInputInvalid):
        await nodes.validate(state, StageContext())


# retrieve


async def test_retrieve_labels_relevant_chunks_and_passes_scope() -> None:
    doc = uuid.uuid4()
    retriever = FakeRetriever([chunk("relevant", score=0.9), chunk("weak", score=0.1)])
    ctx = StageContext()

    update = await nodes.retrieve(
        {"question": "What is the policy?", "document_ids": [doc]}, ctx, retriever=retriever, policy=POLICY
    )

    assert retriever.calls == [("What is the policy?", 5, [doc])]
    assert [e.label for e in update["evidence"]] == ["S1"]
    assert '<evidence label="S1"' in update["evidence_text"]
    assert "weak" not in update["evidence_text"]
    assert update["insufficient"] is False
    assert ctx.detail == "2 chunks retrieved, 1 used as evidence"


async def test_retrieve_below_threshold_is_insufficient() -> None:
    ctx = StageContext()
    update = await nodes.retrieve(
        {"question": "q" * 10}, ctx, retriever=FakeRetriever([chunk("x", score=0.2)]), policy=POLICY
    )
    assert update["insufficient"] is True
    assert update["evidence"] == []
    assert ctx.detail == "No relevant evidence (1 chunk below the relevance threshold)"


async def test_retrieve_nothing_found_is_insufficient() -> None:
    ctx = StageContext()
    update = await nodes.retrieve({"question": "q" * 10}, ctx, retriever=FakeRetriever(), policy=POLICY)
    assert update["insufficient"] is True
    assert ctx.detail == "No matching chunks found"


async def test_retrieve_respects_evidence_budget() -> None:
    policy = RetrievalPolicy(top_k=5, min_score=0.0, max_evidence_chars=150)
    update = await nodes.retrieve(
        {"question": "q" * 10},
        StageContext(),
        retriever=FakeRetriever([chunk("a" * 100), chunk("b" * 100)]),
        policy=policy,
    )
    # Only what fits is labelled as evidence, so only it can be cited later.
    assert [e.label for e in update["evidence"]] == ["S1"]


# analyze


async def test_analyze_keeps_only_findings_with_valid_labels() -> None:
    model = ScriptedChatModel(
        [findings_json(("Remote allowed", ["S1", "S9"]), ("Invented", ["S7"]), ("Twice", ["S2", "S2"]))]
    )
    ctx = StageContext()

    update = await nodes.analyze(_state_with_evidence("one", "two"), ctx, model=model)

    assert update["findings"] == [
        Finding(statement="Remote allowed", citations=["S1"]),
        Finding(statement="Twice", citations=["S2"]),
    ]
    assert ctx.attempts == 1
    assert ctx.detail == "2 findings extracted; 1 unsupported finding dropped"
    history, system_prompt = model.calls[0]
    assert "untrusted data" in system_prompt
    assert "<evidence" in history[0].content
    assert '<evidence label="S1"' not in system_prompt


async def test_analyze_with_no_supported_findings_is_insufficient() -> None:
    ctx = StageContext()
    update = await nodes.analyze(_state_with_evidence("one"), ctx, model=ScriptedChatModel(['{"findings": []}']))
    assert update == {"findings": [], "insufficient": True}
    assert ctx.detail == "No findings supported by the evidence"


async def test_invalid_json_gets_one_corrective_retry_then_succeeds() -> None:
    model = ScriptedChatModel(["Sure! Here are the findings:"], ["```json\n" + findings_json(("A", ["S1"])) + "\n```"])
    ctx = StageContext()

    update = await nodes.analyze(_state_with_evidence("one"), ctx, model=model)

    assert len(update["findings"]) == 1
    assert ctx.attempts == 2
    retry_history = model.calls[1][0]
    assert [t.role for t in retry_history] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert retry_history[1].content == "Sure! Here are the findings:"
    assert "could not be used" in retry_history[2].content


async def test_invalid_json_twice_is_invalid_model_output() -> None:
    model = ScriptedChatModel(["nope"], ['{"findings": "not a list"}'], [findings_json(("never used", ["S1"]))])
    ctx = StageContext()

    with pytest.raises(InvalidModelOutput) as info:
        await nodes.analyze(_state_with_evidence("one"), ctx, model=model)

    assert info.value.code == "invalid_model_output"
    assert ctx.attempts == 2
    assert len(model.calls) == 2  # bounded: no third call


async def test_provider_error_is_not_retried_by_the_node() -> None:
    model = ScriptedChatModel([LLMAuthFailed()], [findings_json(("A", ["S1"]))])
    ctx = StageContext()
    with pytest.raises(LLMAuthFailed):
        await nodes.analyze(_state_with_evidence("one"), ctx, model=model)
    assert len(model.calls) == 1


async def test_overlong_reply_counts_as_invalid_output() -> None:
    huge = "x" * (nodes.MAX_REPLY_CHARS + 1)
    model = ScriptedChatModel([huge], [huge])
    with pytest.raises(InvalidModelOutput):
        await nodes.analyze(_state_with_evidence("one"), StageContext(), model=model)
    assert model.calls[1][0][1].content == "(reply omitted)"


# draft


async def test_draft_parses_report_json() -> None:
    state = _state_with_evidence("one", "two")
    state["findings"] = [Finding(statement="Remote allowed", citations=["S1"])]
    model = ScriptedChatModel([draft_json()])
    ctx = StageContext()

    update = await nodes.draft(state, ctx, model=model)

    assert update["draft"].title == "Remote work policy"
    assert ctx.detail == "1 section drafted"
    assert "- Remote allowed [S1]" in model.calls[0][0][0].content


# verify


def _draft(summary: str, *bodies: str) -> DraftOutput:
    return DraftOutput.model_validate(
        {"title": "T", "summary": summary, "sections": [{"heading": f"H{i}", "body": b} for i, b in enumerate(bodies)]}
    )


async def test_verify_strips_invented_markers_and_builds_references() -> None:
    state = _state_with_evidence("Staff may work remotely two days a week.", "Unused evidence")
    state["draft"] = _draft("Remote work is allowed [S1] [S5].", "Two days [S1][S42]. Also [S9] here.")
    ctx = StageContext()

    update = await nodes.verify(state, ctx)

    report = update["report"]
    assert report.summary == "Remote work is allowed [S1]."
    assert report.sections[0].body == "Two days [S1]. Also here."
    assert [r.marker for r in report.references] == ["S1"]
    ref = report.references[0]
    assert ref.excerpt == "Staff may work remotely two days a week."
    assert ref.location.page == 2
    assert ctx.detail == "1 reference verified, 3 invalid references removed"


async def test_verify_without_valid_references_withholds_the_report() -> None:
    state = _state_with_evidence("one")
    state["draft"] = _draft("Ungrounded [S3].", "No markers at all.")
    ctx = StageContext()

    update = await nodes.verify(state, ctx)

    assert update == {"insufficient": True}
    assert ctx.detail == "No valid references; report withheld (1 invalid reference removed)"


async def test_verify_truncates_long_excerpts() -> None:
    state = _state_with_evidence("word " * 200)
    state["draft"] = _draft("Claim [S1].", "Body.")
    update = await nodes.verify(state, StageContext())
    excerpt = update["report"].references[0].excerpt
    assert len(excerpt) <= nodes.EXCERPT_MAX_CHARS
    assert excerpt.endswith("…")


# parsing


def test_parse_accepts_fence_and_surrounding_text() -> None:
    out = parse_json_output('Here:\n{"findings": []}\nThanks', FindingsOutput)
    assert out.findings == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no json here",
        '{"findings": [',
        '{"findings": [{"statement": "x", "citations": []}]}',
        '{"findings": [{"statement": "x", "citations": ["source1"]}]}',
        '{"findings": [], "extra": 1}',
    ],
)
def test_parse_rejects_bad_output(raw: str) -> None:
    with pytest.raises(OutputParseError):
        parse_json_output(raw, FindingsOutput)


def test_parse_error_message_does_not_echo_input() -> None:
    with pytest.raises(OutputParseError) as info:
        parse_json_output('{"title": "SECRET-TEXT", "summary": "", "sections": []}', DraftOutput)
    assert "SECRET-TEXT" not in str(info.value)
