"""Optional Markdown front matter: a leading block of `key: value` lines between two `---` lines.

Only `status` is read. `status: deprecated` (case-insensitive) marks the document deprecated, which demotes its
chunks at retrieval time (`RETRIEVAL_DEPRECATED_PENALTY`). Any other value, and any other key (`supersedes`,
`owner`, …), is ignored. A well-formed block is removed before chunking, so it is never embedded or cited, and
chunk offsets refer to the text after it. Anything else (for example a leading horizontal rule followed by prose)
is left untouched. This is deliberately not a YAML parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentops.application.documents.normalize import NormalizedText

MAX_FRONT_MATTER_LINES = 30

_DELIMITER = "---"
_CLOSING = {"---", "..."}
_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*?)[ \t]*$")


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    deprecated: bool = False


def split_front_matter(prepared: NormalizedText) -> tuple[DocumentMetadata, NormalizedText]:
    """Return the metadata and the text without its front matter (unchanged when there is none)."""
    lines = prepared.text.split("\n")
    if prepared.page_starts or lines[0].rstrip() != _DELIMITER:
        return DocumentMetadata(), prepared
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1 : MAX_FRONT_MATTER_LINES + 1], start=1):
        if line.rstrip() in _CLOSING:
            rest = "\n".join(lines[index + 1 :]).lstrip()
            status = fields.get("status", "").strip("'\"").lower()
            return DocumentMetadata(deprecated=status == "deprecated"), NormalizedText(text=rest)
        if not line.strip():
            continue
        match = _KEY_VALUE.match(line)
        if match is None:
            return DocumentMetadata(), prepared
        fields.setdefault(match.group(1).lower(), match.group(2))
    return DocumentMetadata(), prepared
