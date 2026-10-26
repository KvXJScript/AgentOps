"""Text normalisation. Chunk offsets refer to the normalised text, not to the original bytes."""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from agentops.domain.documents import ExtractedSegment

PAGE_SEPARATOR = "\n\n"

_CONTROL = re.compile("[\\x00-\\x08\\x0b\\x0e-\\x1f\\x7f\\u200b\\ufeff]")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class NormalizedText:
    text: str
    # (char offset where the page starts, 1-based page number), ascending. Empty when pages don't apply.
    page_starts: tuple[tuple[int, int], ...] = ()

    def page_at(self, offset: int) -> int | None:
        if not self.page_starts:
            return None
        index = bisect_right([start for start, _ in self.page_starts], offset) - 1
        return self.page_starts[max(index, 0)][1]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = _CONTROL.sub("", text)
    text = _TRAILING_SPACE.sub("\n", text + "\n")
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def normalize_segments(segments: Sequence[ExtractedSegment]) -> NormalizedText:
    """Normalise each segment and join non-empty ones, remembering where each page starts."""
    parts: list[str] = []
    page_starts: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        text = normalize_text(segment.text)
        if not text:
            continue
        if parts:
            offset += len(PAGE_SEPARATOR)
        if segment.page is not None:
            page_starts.append((offset, segment.page))
        parts.append(text)
        offset += len(text)
    return NormalizedText(text=PAGE_SEPARATOR.join(parts), page_starts=tuple(page_starts))
