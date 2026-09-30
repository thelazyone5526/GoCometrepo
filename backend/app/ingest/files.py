"""Upload checks and storage: hash, real file type, size and page limits.

The file type is sniffed from its first bytes, never trusted from a filename or a client-sent
content type. That's what design section 6 means by "PDF, PNG or JPG only": the check looks at
the bytes, not the extension.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.config import settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 5

FileType = str  # "pdf", "png" or "jpg"

# Magic-byte sniffing: enough of each header to tell the three accepted types apart, and to
# reject anything else (docx, gif, bmp...) even if it was renamed to end in .pdf.
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class UploadError(Exception):
    """A rejected upload. `reason` is one of the API's error codes (design section 6)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class UnsupportedFileType(UploadError):
    def __init__(self, message: str = "File is not a PDF, PNG or JPG") -> None:
        super().__init__("unsupported_type", message)


class FileTooLarge(UploadError):
    def __init__(self, message: str = f"File exceeds {MAX_UPLOAD_BYTES} bytes") -> None:
        super().__init__("too_large", message)


class TooManyPages(UploadError):
    def __init__(self, message: str = f"PDF exceeds {MAX_PAGES} pages") -> None:
        super().__init__("too_many_pages", message)


def sniff_file_type(data: bytes) -> FileType | None:
    """The type implied by the file's own bytes, or None if it's none of the three."""
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(_JPEG_MAGIC):
        return "jpg"
    return None


def file_hash(data: bytes) -> str:
    """SHA-256 hex digest, used for the upload folder name and the duplicate check."""
    return hashlib.sha256(data).hexdigest()


def count_pdf_pages(data: bytes) -> int:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


@dataclass(frozen=True)
class CheckedUpload:
    data: bytes
    file_type: FileType
    file_hash: str
    page_count: int  # 1 for PNG/JPG, the PDF's own page count otherwise


def check_upload(data: bytes) -> CheckedUpload:
    """Validate one uploaded file against every limit in design section 6.

    Order matters for a clear error: size first (cheapest check), then type (needs only the
    header), then page count (needs to open the PDF).
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileTooLarge()

    file_type = sniff_file_type(data)
    if file_type is None:
        raise UnsupportedFileType()

    page_count = count_pdf_pages(data) if file_type == "pdf" else 1
    if page_count > MAX_PAGES:
        raise TooManyPages()

    return CheckedUpload(
        data=data, file_type=file_type, file_hash=file_hash(data), page_count=page_count
    )


def upload_dir(hash_: str, *, data_dir: Path | None = None) -> Path:
    """Where an upload's original file and rendered pages live: data/uploads/<hash>/."""
    base = data_dir if data_dir is not None else settings.data_dir
    return base / "uploads" / hash_


def store_upload(checked: CheckedUpload, *, data_dir: Path | None = None) -> Path:
    """Write the original file under data/uploads/<hash>/original.<ext>, if not already there.

    Same hash always means the same bytes, so if the file is already on disk, nothing is
    rewritten. That's what makes the duplicate-upload check (design section 3.7) safe: the
    second upload of the same file reuses this folder instead of overwriting it mid-run.
    """
    folder = upload_dir(checked.file_hash, data_dir=data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"original.{checked.file_type}"
    if not path.exists():
        path.write_bytes(checked.data)
    return path
