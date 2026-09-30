"""Assemble one page's `pages` entry (design section 3.2): the stored image, where its text
came from, its spans and quality stats. This is what `graph.prepare` (Phase 8) calls per page.

Per page:
- **Digital PDF page** (text layer with at least 30 non-space characters): the stored image is
  the plain 200 DPI render, and its spans come straight from the text layer, confidence 1.0.
- **Scan, or a PDF page without a usable text layer, or a PNG/JPG upload**: the stored image is
  the *cleaned* image (`ingest.cleanup.clean_scan`: grayscale, straightened, denoised, higher
  contrast), and its spans come from RapidOCR run on that cleaned image. Boxes therefore always
  refer to the stored image, whichever source produced it (design section 3.3), so a highlight
  drawn from a span's box lines up with what's shown on screen.

A PDF that mixes digital and scanned pages is handled page by page: each page's own text is
checked independently, so one scanned page in an otherwise digital PDF still gets OCR.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pymupdf
from rapidocr import RapidOCR

from .cleanup import clean_scan
from .files import CheckedUpload
from .ocr import run_ocr
from .render import RENDER_DPI, RenderedPage, render_image_page, render_pdf_pages
from .textlayer import TextSpan, is_digital_page, text_layer_spans

PageSource = Literal["text_layer", "ocr"]

# A span below this OCR confidence counts as "low confidence" for the page quality stats.
# RapidOCR's own detector already drops anything under 0.5 (its `text_score` setting), so this
# is a stricter, page-quality bar above that floor, not a second detector threshold.
LOW_CONFIDENCE_SPAN_THRESHOLD = 0.8


@dataclass(frozen=True)
class PageQuality:
    avg_confidence: float
    low_confidence_share: float  # share of spans with confidence < LOW_CONFIDENCE_SPAN_THRESHOLD


@dataclass(frozen=True)
class PreparedPage:
    index: int
    image: np.ndarray  # the stored page image: cleaned image for a scan, plain render otherwise
    source: PageSource
    spans: list[TextSpan]
    quality: PageQuality
    prepare_seconds: float

    def to_png(self) -> bytes:
        from .render import rgb_to_png

        return rgb_to_png(self.image)


def page_quality(
    spans: list[TextSpan], *, low_confidence_threshold: float = LOW_CONFIDENCE_SPAN_THRESHOLD
) -> PageQuality:
    """Average confidence and the share of spans below `low_confidence_threshold`.

    A page with no spans at all (a blank page, or OCR finding nothing) is the worst case, not
    an average of zero items: it's reported as zero confidence and entirely low-confidence.
    """
    if not spans:
        return PageQuality(avg_confidence=0.0, low_confidence_share=1.0)
    confidences = [s.confidence for s in spans]
    low = sum(1 for c in confidences if c < low_confidence_threshold)
    return PageQuality(
        avg_confidence=sum(confidences) / len(confidences),
        low_confidence_share=low / len(confidences),
    )


def prepare_page(rendered: RenderedPage, *, ocr_engine: RapidOCR | None = None) -> PreparedPage:
    """Turn one rendered page into its `pages` entry, choosing the text-layer or OCR path."""
    start = time.perf_counter()
    if rendered.pdf_page is not None and is_digital_page(rendered.pdf_page):
        spans = text_layer_spans(rendered.pdf_page, dpi=rendered.dpi)
        image = rendered.image
        source: PageSource = "text_layer"
    else:
        image, _skew_deg = clean_scan(rendered.image)
        spans = run_ocr(image, engine=ocr_engine)
        source = "ocr"
    elapsed = time.perf_counter() - start
    return PreparedPage(
        index=rendered.index,
        image=image,
        source=source,
        spans=spans,
        quality=page_quality(spans),
        prepare_seconds=elapsed,
    )


def prepare_pages(
    checked: CheckedUpload, *, dpi: int = RENDER_DPI, ocr_engine: RapidOCR | None = None
) -> list[PreparedPage]:
    """Every page of one checked upload, ready for the Extractor (design section 3.2 `pages`).

    `dpi` also drives Phase 5's targeted retry: rendering one page again at 300 DPI and calling
    this same pipeline on it gives fresh, higher-resolution spans to re-ground against.
    """
    if checked.file_type == "pdf":
        doc = pymupdf.open(stream=checked.data, filetype="pdf")
        try:
            rendered_pages = render_pdf_pages(doc, dpi=dpi)
            return [prepare_page(page, ocr_engine=ocr_engine) for page in rendered_pages]
        finally:
            doc.close()
    rendered_page = render_image_page(checked.data)
    return [prepare_page(rendered_page, ocr_engine=ocr_engine)]
