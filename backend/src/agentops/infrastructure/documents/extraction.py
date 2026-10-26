"""Text extraction from original bytes: UTF-8 text as is, PDFs via pypdf (page by page)."""

from __future__ import annotations

import logging
from io import BytesIO

from pypdf import PdfReader

from agentops.domain.documents import (
    CorruptDocument,
    ExtractedSegment,
    FileTooLarge,
    MediaType,
    UnsupportedFileType,
)

logger = logging.getLogger(__name__)


class DefaultTextExtractor:
    def __init__(self, *, max_pdf_pages: int) -> None:
        self._max_pdf_pages = max_pdf_pages

    def extract(self, content: bytes, media_type: MediaType) -> list[ExtractedSegment]:
        if media_type is MediaType.PDF:
            return self._pdf(content)
        try:
            return [ExtractedSegment(text=content.decode("utf-8-sig"))]
        except UnicodeDecodeError:
            raise UnsupportedFileType("Text files must be UTF-8 encoded.") from None

    def _pdf(self, content: bytes) -> list[ExtractedSegment]:
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            encrypted = reader.is_encrypted
            page_count = 0 if encrypted else len(reader.pages)
        except Exception as exc:
            logger.info("extraction.pdf_unreadable", extra={"error_type": type(exc).__name__})
            raise CorruptDocument() from None
        if encrypted:
            raise CorruptDocument("Encrypted or password-protected PDFs are not supported.")
        if page_count > self._max_pdf_pages:
            raise FileTooLarge(f"The PDF has more than {self._max_pdf_pages} pages.")

        segments: list[ExtractedSegment] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                logger.info("extraction.pdf_page_failed", extra={"page": number, "error_type": type(exc).__name__})
                raise CorruptDocument() from None
            segments.append(ExtractedSegment(text=text, page=number))
        return segments
