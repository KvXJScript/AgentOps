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
# Evidence labels mean nothing inside a tracker issue.
_MARKER = re.compile(r"[ \t]*\[(?:S|R|I)\d{1,3}\]")


class DraftIssueOutput(BaseModel):
    """What the model must reply with. Extra keys (e.g. a `tool` or `target`) are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=ISSUE_TITLE_MIN, max_length=ISSUE_TITLE_MAX)
    ]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ISSUE_BODY_MAX)]
    labels: Annotated[list[LabelName], Field(max_length=ISSUE_LABELS_MAX)]

    @field_validator("title", "body", mode="after")
    @classmethod
    def _strip_markers(cls, value: str) -> str:
        return _MARKER.sub("", value).strip()


def to_draft(output: DraftIssueOutput) -> IssueDraft:
    """Canonical form: labels deduplicated and in the fixed vocabulary order."""
    return validated(IssueDraft(title=output.title, body=output.body, labels=tuple(output.labels)))


def validated(draft: IssueDraft) -> IssueDraft:
    try:
        parsed = DraftIssueOutput.model_validate(
            {"title": draft.title, "body": draft.body, "labels": list(draft.labels)}
        )
    except ValidationError as exc:
        raise InvalidToolArguments("The proposal's arguments are invalid.") from exc
    labels = tuple(label for label in PROPOSAL_LABELS if label in parsed.labels)
    return IssueDraft(title=parsed.title, body=parsed.body, labels=labels)
