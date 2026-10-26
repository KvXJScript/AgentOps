from __future__ import annotations

import pytest

from agentops.application.documents.validation import default_title, detect_media_type, safe_filename
from agentops.domain.documents import (
    CorruptDocument,
    EmptyDocument,
    FileTooLarge,
    IngestionError,
    MediaType,
    UnsupportedFileType,
)
from agentops.infrastructure.documents.extraction import DefaultTextExtractor
from tests.document_fakes import make_encrypted_pdf, make_pdf

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("notes.md", b"# Hi", MediaType.MARKDOWN),
        ("NOTES.MARKDOWN", b"# Hi", MediaType.MARKDOWN),
        ("notes.txt", "zażółć gęślą jaźń".encode(), MediaType.PLAIN),
        ("bom.txt", b"\xef\xbb\xbfhello", MediaType.PLAIN),
        ("paper.pdf", make_pdf(["hello"]), MediaType.PDF),
        ("C:\\Users\\me\\paper.PDF", make_pdf(["hello"]), MediaType.PDF),
    ],
)
def test_accepted_types(filename: str, content: bytes, expected: MediaType) -> None:
    assert detect_media_type(filename, content) is expected


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("image.png", PNG),
        ("archive.zip", b"PK\x03\x04"),
        ("noextension", b"hello"),
        ("fake.pdf", b"just text pretending"),  # extension says PDF, content doesn't
        ("disguised.txt", make_pdf(["x"])),  # a PDF renamed to .txt
        ("image.md", PNG),  # binary with a text extension
        ("latin1.txt", "café".encode("latin-1")),  # not UTF-8
        ("utf16.txt", "hello".encode("utf-16")),
        ("controls.txt", b"a\x01\x02\x03\x04\x05b"),
    ],
)
def test_rejected_types(filename: str, content: bytes) -> None:
    with pytest.raises(UnsupportedFileType) as excinfo:
        detect_media_type(filename, content)
    assert excinfo.value.code == "unsupported_file_type"


@pytest.mark.parametrize("content", [b"", b"  \n\t "])
def test_empty_upload(content: bytes) -> None:
    with pytest.raises(EmptyDocument):
        detect_media_type("empty.md", content)


def test_safe_filename_and_title() -> None:
    assert safe_filename("../../etc/passwd.md") == "passwd.md"
    assert safe_filename("a\x00b\u202e.txt") == "ab.txt"
    assert safe_filename("") == "document"
    assert len(safe_filename("x" * 400 + ".pdf")) == 255
    assert safe_filename("x" * 400 + ".pdf").endswith(".pdf")
    assert default_title("/tmp/Quarterly report.final.pdf") == "Quarterly report.final"


def test_pdf_text_is_extracted_per_page() -> None:
    segments = DefaultTextExtractor(max_pdf_pages=5).extract(make_pdf(["First page", "Second page"]), MediaType.PDF)

    assert [(s.page, s.text.strip()) for s in segments] == [(1, "First page"), (2, "Second page")]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"%PDF-1.7\n this is not really a pdf", CorruptDocument),
        (make_pdf(["ok"])[:60], CorruptDocument),  # truncated
        (make_encrypted_pdf(), CorruptDocument),
        (make_pdf(["1", "2", "3"]), FileTooLarge),  # over the page cap below
    ],
)
def test_pdf_failures(content: bytes, error: type[IngestionError]) -> None:
    with pytest.raises(error):
        DefaultTextExtractor(max_pdf_pages=2).extract(content, MediaType.PDF)


def test_text_is_decoded_as_utf8_without_bom() -> None:
    [segment] = DefaultTextExtractor(max_pdf_pages=1).extract(b"\xef\xbb\xbfcze\xc5\x9b\xc4\x87", MediaType.PLAIN)

    assert segment.text == "cześć"
    assert segment.page is None
