"""The contract for `create_issue` arguments, used when drafting and again right before execution."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from agentops.domain.tracker import (
    ISSUE_BODY_MAX,
    ISSUE_LABELS_MAX,
    ISSUE_TITLE_MAX,
    ISSUE_TITLE_MIN,
    PROPOSAL_LABELS,
    InvalidToolArguments,
    IssueDraft,
)

LabelName = Literal["bug", "docs", "follow-up", "question"]
# The report (R1) and tracker issues (I1..) are not sources, so their labels mean nothing in an issue.
# `[Sn]` markers stay in the body: drafting resolves them and the "Sources" footer says what they point to.
_MARKER = re.compile(r"[ \t]*\[(?:S|R|I)\d{1,3}\]")
_NON_SOURCE_MARKER = re.compile(r"[ \t]*\[(?:R|I)\d{1,3}\]")
# Room left in `body` for the server-written "Sources" footer.
SOURCES_FOOTER_RESERVE = 1000
MODEL_BODY_MAX = ISSUE_BODY_MAX - SOURCES_FOOTER_RESERVE


class DraftIssueOutput(BaseModel):
    """What the model must reply with. Extra keys (e.g. a `tool` or `target`) are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=ISSUE_TITLE_MIN, max_length=ISSUE_TITLE_MAX)
    ]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ISSUE_BODY_MAX)]
    labels: Annotated[list[LabelName], Field(max_length=ISSUE_LABELS_MAX)]

    @field_validator("title", mode="after")
    @classmethod
    def _strip_markers(cls, value: str) -> str:
        return _MARKER.sub("", value).strip()

    @field_validator("body", mode="after")
    @classmethod
    def _strip_non_source_markers(cls, value: str) -> str:
        return _NON_SOURCE_MARKER.sub("", value).strip()


class DraftIssueReply(DraftIssueOutput):
    """The model's reply: a shorter body, so the server-written footer always fits in `ISSUE_BODY_MAX`."""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MODEL_BODY_MAX)]


def to_draft(output: DraftIssueOutput, *, body: str | None = None) -> IssueDraft:
    """Canonical form: labels deduplicated and in the fixed vocabulary order. `body` overrides the output's."""
    return validated(
        IssueDraft(title=output.title, body=output.body if body is None else body, labels=tuple(output.labels))
    )


def validated(draft: IssueDraft) -> IssueDraft:
    try:
        parsed = DraftIssueOutput.model_validate(
            {"title": draft.title, "body": draft.body, "labels": list(draft.labels)}
        )
    except ValidationError as exc:
        raise InvalidToolArguments("The proposal's arguments are invalid.") from exc
    labels = tuple(label for label in PROPOSAL_LABELS if label in parsed.labels)
    return IssueDraft(title=parsed.title, body=parsed.body, labels=labels)
