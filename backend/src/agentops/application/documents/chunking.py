"""Deterministic, heading-aware chunking.

1. Markdown is split into sections at ATX headings (`#`..`######`, ignored inside
   fenced code). Each section carries the heading path above it; a heading with no
   body of its own is merged into the section that follows it.
2. Each section is cut into windows of at most `max_chars`, preferring paragraph,
   line, sentence and word boundaries (in that order) in the second half of the
   window. Consecutive windows of one section overlap by about `overlap_chars`,
   starting at a word boundary. Windows never cross a section boundary.

Every chunk's text is exactly `text[char_start:char_end]` of the normalised text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentops.application.documents.normalize import NormalizedText
from agentops.domain.documents import ChunkDraft
from agentops.domain.retrieval import ChunkLocation

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SENTENCE_END = re.compile(r"[.!?…](?=\s)")
_PARAGRAPH, _LINE, _SPACE = "\n\n", "\n", " "


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int = 500
    overlap_chars: int = 80

    def __post_init__(self) -> None:
        if self.max_chars < 50:
            raise ValueError("max_chars must be at least 50")
        if not 0 <= self.overlap_chars < self.max_chars // 2:
            raise ValueError("overlap_chars must be >= 0 and less than half of max_chars")


@dataclass(frozen=True, slots=True)
class Section:
    start: int
    end: int
    heading_path: tuple[str, ...]


def split_sections(text: str) -> list[Section]:
    """Split Markdown at headings. Text before the first heading has an empty heading path."""
    raw: list[tuple[Section, bool]] = []  # (section, has_body)
    stack: list[tuple[int, str]] = []
    start, has_body = 0, False
    path: tuple[str, ...] = ()
    fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\n")
        fence_match = _FENCE.match(content)
        heading = _HEADING.match(content) if fence is None and fence_match is None else None
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        if heading:
            if offset > start:
                raw.append((Section(start, offset, path), has_body))
            level, title = len(heading.group(1)), heading.group(2).strip()
            stack = [(lvl, t) for lvl, t in stack if lvl < level] + [(level, title)]
            start, path, has_body = offset, tuple(t for _, t in stack), False
        elif content.strip():
            has_body = True
        offset += len(line)
    raw.append((Section(start, len(text), path), has_body))

    sections: list[Section] = []
    pending_start: int | None = None
    for index, (section, body) in enumerate(raw):
        if not text[section.start : section.end].strip():
            continue
        if not body and index < len(raw) - 1:
            pending_start = section.start if pending_start is None else pending_start
            continue
        if pending_start is not None:
            sections.append(Section(pending_start, section.end, section.heading_path))
            pending_start = None
        else:
            sections.append(section)
    return sections


def chunk_document(document: NormalizedText, *, markdown: bool, config: ChunkingConfig) -> list[ChunkDraft]:
    text = document.text
    sections = split_sections(text) if markdown else [Section(0, len(text), ())]
    drafts: list[ChunkDraft] = []
    for section in sections:
        for start, end in _windows(text, section.start, section.end, config):
            drafts.append(
                ChunkDraft(
                    ordinal=len(drafts),
                    text=text[start:end],
                    location=ChunkLocation(
                        char_start=start,
                        char_end=end,
                        page=document.page_at(start),
                        heading_path=section.heading_path,
                    ),
                )
            )
    return drafts


def _windows(text: str, start: int, end: int, config: ChunkingConfig) -> list[tuple[int, int]]:
    start, end = _trim(text, start, end)
    windows: list[tuple[int, int]] = []
    while start < end:
        if end - start <= config.max_chars:
            windows.append((start, end))
            break
        cut = _best_break(text, start + config.max_chars // 2, start + config.max_chars)
        windows.append(_trim(text, start, cut))
        next_start = _word_start(text, cut - config.overlap_chars, cut) if config.overlap_chars else cut
        start, _ = _trim(text, max(next_start, start + 1), end)
    return [(s, e) for s, e in windows if e > s]


def _best_break(text: str, lo: int, hi: int) -> int:
    """End offset (exclusive) of the most natural cut inside text[lo:hi]; `hi` if none."""
    for separator in (_PARAGRAPH, _LINE):
        index = text.rfind(separator, lo, hi)
        if index != -1:
            return index + len(separator)
    last_sentence = None
    for last_sentence in _SENTENCE_END.finditer(text, lo, hi):  # noqa: B007 - we want the last match
        pass
    if last_sentence is not None:
        return last_sentence.end()
    index = text.rfind(_SPACE, lo, hi)
    return index + 1 if index != -1 else hi


def _word_start(text: str, position: int, limit: int) -> int:
    """First word start at or after `position` (when the overlap would start mid-word)."""
    if position <= 0 or text[position - 1].isspace():
        return position
    for index in range(position, limit):
        if text[index].isspace():
            return index + 1
    return position


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
