"""Upload validation: file type by extension AND content, before anything is stored."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from agentops.domain.documents import EmptyDocument, MediaType, UnsupportedFileType

EXTENSIONS: dict[str, MediaType] = {
    ".md": MediaType.MARKDOWN,
    ".markdown": MediaType.MARKDOWN,
    ".txt": MediaType.PLAIN,
    ".pdf": MediaType.PDF,
}
PDF_MAGIC = b"%PDF-"
# The PDF spec tolerates a little junk before the header; readers look in the first KiB.
PDF_HEADER_WINDOW = 1024
# More than this share of C0 control characters (other than tab/newline/CR/form feed) means "binary".
MAX_CONTROL_RATIO = 0.01

FILENAME_MAX_CHARS = 255
TITLE_MAX_CHARS = 200

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def detect_media_type(filename: str, content: bytes) -> MediaType:
    """Return the media type, checking the extension and sniffing the content.

    Raises `UnsupportedFileType` for unknown extensions, PDFs without the PDF
    header, text files that are not UTF-8 (or look binary), and content that
    contradicts its extension. Raises `EmptyDocument` for zero-byte files.
    """
    media_type = EXTENSIONS.get(PurePosixPath(safe_filename(filename)).suffix.lower())
    if media_type is None:
        raise UnsupportedFileType()
    if not content.strip():
        raise EmptyDocument()

    looks_like_pdf = PDF_MAGIC in content[:PDF_HEADER_WINDOW]
    if media_type is MediaType.PDF:
        if not looks_like_pdf:
            raise UnsupportedFileType("The file has a .pdf extension but is not a PDF.")
        return media_type

    if looks_like_pdf or b"\x00" in content:
        raise UnsupportedFileType("The file looks binary. Text files must be UTF-8 encoded text.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnsupportedFileType("Text files must be UTF-8 encoded.") from None
    if len(_CONTROL.findall(text)) > MAX_CONTROL_RATIO * len(text):
        raise UnsupportedFileType("The file looks binary. Text files must be UTF-8 encoded text.")
    return media_type


def safe_filename(filename: str) -> str:
    """Basename only, without control characters, capped in length."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(name) > FILENAME_MAX_CHARS:
        suffix = PurePosixPath(name).suffix[:16]
        name = name[: FILENAME_MAX_CHARS - len(suffix)] + suffix
    return name or "document"


def default_title(filename: str) -> str:
    stem = PurePosixPath(safe_filename(filename)).stem.strip()
    return (stem or "Untitled document")[:TITLE_MAX_CHARS]
