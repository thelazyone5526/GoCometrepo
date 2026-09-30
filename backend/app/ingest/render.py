"""Turn an uploaded file into one PNG per page, at a fixed DPI.

A PDF page becomes an image at RENDER_DPI (200), whatever its own content is: a page with a
text layer is rendered exactly like a scanned one, so both take the same path from here on
(design section 3.3, step 1: "every page is rendered to an image... that's what Gemini sees").
A PNG or JPG upload is already one page's image; it's just decoded and re-saved as a PNG at
its native resolution, so the rest of the pipeline only ever deals with PNG bytes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pymupdf
from PIL import Image

from .files import CheckedUpload

RENDER_DPI = 200


@dataclass(frozen=True)
class RenderedPage:
    index: int  # 0-based
    image: np.ndarray  # RGB, HxWx3, uint8
    pdf_page: pymupdf.Page | None  # set for a digital PDF page; used to read its text layer
    dpi: int = RENDER_DPI  # the DPI `image` was rendered at; text_layer_spans needs to match it

    @property
    def width_px(self) -> int:
        return int(self.image.shape[1])

    @property
    def height_px(self) -> int:
        return int(self.image.shape[0])

    def to_png(self) -> bytes:
        return rgb_to_png(self.image)


def _pixmap_to_rgb(pix: pymupdf.Pixmap) -> np.ndarray:
    rows = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    return rows[:, : pix.width * 3].reshape(pix.height, pix.width, 3).copy()


def rgb_to_png(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    mode = "RGB" if rgb.ndim == 3 else "L"
    Image.fromarray(rgb, mode=mode).save(buf, "PNG")
    return buf.getvalue()


def render_pdf_pages(doc: pymupdf.Document, *, dpi: int = RENDER_DPI) -> list[RenderedPage]:
    """One rendered RGB image per page of an already-open PDF, at `dpi`.

    Takes an open `pymupdf.Document` (rather than raw bytes) so the caller can also read each
    page's text layer from the same `pymupdf.Page` object (see `pdf_page`), without opening
    the PDF twice. Used both for the initial 200 DPI render and Phase 5's 300 DPI retry.
    """
    pages: list[RenderedPage] = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        pages.append(RenderedPage(index=i, image=_pixmap_to_rgb(pix), pdf_page=page, dpi=dpi))
    return pages


def render_image_page(data: bytes) -> RenderedPage:
    """A PNG or JPG upload, decoded to RGB. It's already one page, at whatever resolution the
    file was saved at (design section 3: a scan saved as JPG instead of PDF takes the same
    OCR path as a scanned PDF page). It never has a PDF text layer."""
    with Image.open(io.BytesIO(data)) as img:
        rgb = np.array(img.convert("RGB"))
    return RenderedPage(index=0, image=rgb, pdf_page=None)


def render_pages(checked: CheckedUpload, *, dpi: int = RENDER_DPI) -> list[RenderedPage]:
    """Dispatch on the sniffed file type (`app.ingest.files.check_upload`).

    For a PDF this opens the document, renders every page, and closes it again: each
    `RenderedPage.pdf_page` reference is only used by the caller while this function runs
    (`pages.prepare_pages` reads the text layer inline, before returning). Use
    `render_pdf_pages` directly if a page's `pymupdf.Page` needs to stay valid afterwards.
    """
    if checked.file_type == "pdf":
        with pymupdf.open(stream=checked.data, filetype="pdf") as doc:
            return render_pdf_pages(doc, dpi=dpi)
    return [render_image_page(checked.data)]
