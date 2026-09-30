"""The PDF text layer, turned into text spans (design section 3.2 and 3.3).

A PDF page counts as digital if it has at least 30 non-space characters of text. Its words
are grouped into lines by PyMuPDF itself (`get_text("dict")` already returns one span per
printed line here, since our invoices don't wrap a paragraph across a line break), and each
line's box is converted from PDF points to pixels of the rendered page image, so it lines up
with what `render.render_pdf_pages` produced at the same DPI.

Confidence is always 1.0: this is the document's own text, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from .render import RENDER_DPI

# A PDF page needs at least this many non-space characters to count as digital (design
# section 3: "a PDF page counts as digital if it has at least 30 non-space characters").
DIGITAL_PAGE_MIN_CHARS = 30

# PDF user-space units are points (1/72 inch). Converting to pixels of a page rendered at
# `dpi` just scales by dpi / 72.
POINTS_PER_INCH = 72.0

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in pixels


@dataclass(frozen=True)
class TextSpan:
    """One line of text, wherever it came from (text layer or OCR)."""

    text: str
    box: Box
    confidence: float


def _non_space_char_count(text: str) -> int:
    return len("".join(text.split()))


def page_char_count(page: pymupdf.Page) -> int:
    """Non-space character count, used to decide whether a page has a usable text layer."""
    return _non_space_char_count(page.get_text())


def is_digital_page(page: pymupdf.Page) -> bool:
    return page_char_count(page) >= DIGITAL_PAGE_MIN_CHARS


def _scale(dpi: int) -> float:
    return dpi / POINTS_PER_INCH


def text_layer_spans(page: pymupdf.Page, *, dpi: int = RENDER_DPI) -> list[TextSpan]:
    """This page's text, as line-level spans with pixel boxes at `dpi`. Confidence 1.0.

    Each PyMuPDF "line" is already one printed line of text (a run of same-baseline spans),
    which matches the line-level granularity OCR returns, so both sources feed the rest of
    the pipeline through the same TextSpan shape.
    """
    scale = _scale(dpi)
    spans: list[TextSpan] = []
    text_dict = page.get_text("dict")
    for block in text_dict["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            if not text.strip():
                continue
            x0, y0, x1, y1 = line["bbox"]
            spans.append(
                TextSpan(
                    text=text,
                    box=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
                    confidence=1.0,
                )
            )
    return spans
