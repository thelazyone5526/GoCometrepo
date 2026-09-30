"""Page preparation: file checks, PDF rendering, text layer, image clean-up and OCR.

`prepare_pages` (in `pages.py`) is the entry point the rest of the app should use: it turns one
checked upload into a list of `PreparedPage`, each with its stored image, text spans and
quality stats (design section 3.2's `pages`). The other modules are its building blocks:
`files` (upload checks and storage), `render` (PDF/image to page images), `textlayer` (the
digital-page path), `cleanup` (scan clean-up) and `ocr` (the scan path, RapidOCR).
"""

from .files import CheckedUpload, FileTooLarge, TooManyPages, UnsupportedFileType, check_upload
from .pages import PageQuality, PreparedPage, prepare_pages

__all__ = [
    "CheckedUpload",
    "FileTooLarge",
    "PageQuality",
    "PreparedPage",
    "TooManyPages",
    "UnsupportedFileType",
    "check_upload",
    "prepare_pages",
]
