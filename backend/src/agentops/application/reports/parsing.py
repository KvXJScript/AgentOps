"""Schemas for the model's JSON replies and a tolerant-but-strict parser.

Tolerant: a ```json fence or text around one JSON object is accepted.
Strict: the object itself must match the schema exactly (extra keys rejected).
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Label = Annotated[str, StringConstraints(pattern=r"^S\d{1,3}$")]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Finding(_Strict):
    statement: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    citations: Annotated[list[Label], Field(min_length=1, max_length=8)]


class FindingsOutput(_Strict):
    # Empty means "the evidence does not answer the question".
    findings: Annotated[list[Finding], Field(max_length=12)]


class DraftSection(_Strict):
    heading: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    body: Annotated[_Text, StringConstraints(max_length=6000)]


class DraftOutput(_Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    summary: Annotated[_Text, StringConstraints(max_length=3000)]
    sections: Annotated[list[DraftSection], Field(min_length=1, max_length=8)]


class OutputParseError(ValueError):
    """Carries a short, safe reason that can be fed back to the model."""


def parse_json_output[T: BaseModel](raw: str, schema: type[T]) -> T:
    text = _strip_fence(raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise OutputParseError("the reply did not contain a JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OutputParseError(f"invalid JSON ({exc.msg} at position {exc.pos})") from exc
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'root'}: {err['msg']}" for err in exc.errors()[:5]
        )
        raise OutputParseError(f"JSON did not match the schema ({problems})") from exc


def _strip_fence(text: str) -> str:
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text
