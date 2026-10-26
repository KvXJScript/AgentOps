from __future__ import annotations

import pytest

from agentops.application.documents.front_matter import DocumentMetadata, split_front_matter
from agentops.application.documents.normalize import NormalizedText, normalize_segments
from agentops.domain.documents import ExtractedSegment


def _normalized(text: str) -> NormalizedText:
    return normalize_segments([ExtractedSegment(text=text)])


@pytest.mark.parametrize("status", ["deprecated", "Deprecated", "'deprecated'", '"DEPRECATED"'])
def test_deprecated_status_is_read_and_the_block_removed(status: str) -> None:
    prepared = _normalized(f"---\ntitle: Runbook v1\nstatus: {status}\nsupersedes: none\n---\n\n# Runbook\n\nBody.\n")

    metadata, rest = split_front_matter(prepared)

    assert metadata == DocumentMetadata(deprecated=True)
    assert rest.text == "# Runbook\n\nBody."


@pytest.mark.parametrize("status", ["current", "draft", ""])
def test_other_status_values_are_not_deprecated(status: str) -> None:
    metadata, rest = split_front_matter(_normalized(f"---\nstatus: {status}\n...\n# Doc\n"))

    assert metadata == DocumentMetadata(deprecated=False)
    assert rest.text == "# Doc"


@pytest.mark.parametrize(
    "text",
    [
        "# Doc\n\nstatus: deprecated\n",  # not at the start
        "---\n\nA horizontal rule, then prose: not front matter.\n\n---\n\nMore.\n",
        "---\nstatus: deprecated\n\n# Never closed\n",
        "---\n" + "key: value\n" * 40 + "---\nBody\n",  # longer than MAX_FRONT_MATTER_LINES
    ],
)
def test_anything_else_is_left_untouched(text: str) -> None:
    prepared = _normalized(text)

    assert split_front_matter(prepared) == (DocumentMetadata(), prepared)


def test_paged_text_is_never_parsed() -> None:
    prepared = NormalizedText(text="---\nstatus: deprecated\n---\nBody", page_starts=((0, 1),))

    assert split_front_matter(prepared) == (DocumentMetadata(), prepared)
