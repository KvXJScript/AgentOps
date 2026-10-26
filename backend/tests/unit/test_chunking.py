from __future__ import annotations

import itertools

import pytest

from agentops.application.documents.chunking import ChunkingConfig, chunk_document, split_sections
from agentops.application.documents.normalize import NormalizedText, normalize_segments, normalize_text
from agentops.domain.documents import ChunkDraft, ExtractedSegment

SMALL = ChunkingConfig(max_chars=120, overlap_chars=30)


def _chunks(text: str, *, markdown: bool = True, config: ChunkingConfig = SMALL) -> list[ChunkDraft]:
    return chunk_document(NormalizedText(text), markdown=markdown, config=config)


def _prose(sentences: int, word: str = "lorem") -> str:
    return " ".join(f"{word.capitalize()} ipsum dolor sit amet number {i}." for i in range(sentences))


def test_markdown_sections_carry_their_heading_path() -> None:
    text = "# Guide\n\nIntro text.\n\n## Install\n\nRun it.\n\n### Linux\n\nUse apt.\n\n## Usage\n\nCall it."

    chunks = _chunks(text, config=ChunkingConfig(max_chars=500, overlap_chars=50))

    assert [c.location.heading_path for c in chunks] == [
        ("Guide",),
        ("Guide", "Install"),
        ("Guide", "Install", "Linux"),
        ("Guide", "Usage"),
    ]
    assert chunks[2].text == "### Linux\n\nUse apt."


def test_heading_without_body_is_merged_into_the_next_section() -> None:
    text = "# Title\n\n## Part\n\nBody text."

    [chunk] = _chunks(text)

    assert chunk.text == text
    assert chunk.location.heading_path == ("Title", "Part")
    assert chunk.location.char_start == 0


def test_text_before_first_heading_has_empty_path_and_trailing_heading_is_kept() -> None:
    sections = split_sections("Preamble.\n\n# Only heading")

    assert [s.heading_path for s in sections] == [(), ("Only heading",)]


def test_headings_inside_fenced_code_are_ignored() -> None:
    text = "# Real\n\n```bash\n# not a heading\necho hi\n```\n\nAfter."

    [chunk] = _chunks(text, config=ChunkingConfig(max_chars=500, overlap_chars=50))

    assert chunk.location.heading_path == ("Real",)


def test_closing_atx_hashes_are_not_part_of_the_heading() -> None:
    assert split_sections("## Setup ##\n\nbody")[0].heading_path == ("Setup",)


def test_plain_text_ignores_markdown_headings() -> None:
    chunks = _chunks("# not a heading in txt\n\nbody", markdown=False)

    assert all(c.location.heading_path == () for c in chunks)


def test_chunks_are_bounded_exact_slices_and_cover_the_text() -> None:
    text = "# Doc\n\n" + _prose(30) + "\n\n## Next\n\n" + _prose(20, "dolor")

    chunks = _chunks(text)

    covered: set[int] = set()
    for chunk in chunks:
        start, end = chunk.location.char_start, chunk.location.char_end
        assert text[start:end] == chunk.text
        assert 0 < len(chunk.text) <= SMALL.max_chars
        assert chunk.text == chunk.text.strip()
        covered.update(range(start, end))
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_consecutive_windows_overlap_and_start_on_word_boundaries() -> None:
    text = _prose(20)

    chunks = _chunks(text, markdown=False)

    assert len(chunks) > 3
    for previous, current in itertools.pairwise(chunks):
        overlap = previous.location.char_end - current.location.char_start
        assert 0 < overlap <= SMALL.overlap_chars
        assert text[current.location.char_start - 1].isspace()


def test_windows_never_cross_sections() -> None:
    text = "# A\n\n" + _prose(10) + "\n\n# B\n\n" + _prose(10, "dolor")

    chunks = _chunks(text)
    boundary = text.index("# B")

    assert all(c.location.char_end <= boundary or c.location.char_start >= boundary for c in chunks)
    assert {c.location.heading_path for c in chunks} == {("A",), ("B",)}


def test_prefers_paragraph_breaks() -> None:
    # Longer than half a window, so the paragraph break is a candidate cut.
    first = "First paragraph sentence one. Sentence two is here, and it goes on a bit."
    second = "Second paragraph " + "word " * 30
    chunks = _chunks(f"{first}\n\n{second}", markdown=False)

    assert chunks[0].text == first


def test_hard_cut_when_there_is_no_whitespace() -> None:
    text = "x" * 300

    chunks = _chunks(text, markdown=False, config=ChunkingConfig(max_chars=100, overlap_chars=0))

    assert [len(c.text) for c in chunks] == [100, 100, 100]


def test_chunking_is_deterministic() -> None:
    text = "# Doc\n\n" + _prose(40)

    assert _chunks(text) == _chunks(text)


def test_pdf_pages_are_tracked_by_offset() -> None:
    normalized = normalize_segments(
        [
            ExtractedSegment("Page one text.", page=1),
            ExtractedSegment("   ", page=2),
            ExtractedSegment("Third.", page=3),
        ]
    )

    chunks = chunk_document(normalized, markdown=False, config=ChunkingConfig(max_chars=50, overlap_chars=0))

    assert normalized.text == "Page one text.\n\nThird."
    assert [c.location.page for c in chunks] == [1]  # one chunk starting on page 1
    assert normalized.page_at(normalized.text.index("Third")) == 3
    assert NormalizedText("plain").page_at(0) is None


def test_normalize_text() -> None:
    raw = "Zażółć\r\nline  \t\r\n\n\n\n\nnext\x00\x07\u200bword﻿\f"

    assert normalize_text(raw) == "Zażółć\nline\n\nnextword"


@pytest.mark.parametrize(("max_chars", "overlap"), [(40, 0), (100, 50), (100, -1)])
def test_invalid_chunking_config(max_chars: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="must"):
        ChunkingConfig(max_chars=max_chars, overlap_chars=overlap)
