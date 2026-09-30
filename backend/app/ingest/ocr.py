"""OCR for scans (RapidOCR). Runs on the cleaned page image, returns line-level text spans.

The engine loads its ONNX models once (a few seconds) and is reused for every page, so a
module-level singleton is deliberate here: creating a fresh `RapidOCR()` per page would repeat
that load cost. `get_engine()` is the one place the rest of the app should get an engine from.
"""

from __future__ import annotations

import numpy as np
from rapidocr import RapidOCR

from .textlayer import TextSpan

_engine: RapidOCR | None = None


def get_engine() -> RapidOCR:
    """The shared RapidOCR engine, created on first use and reused after that."""
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def _box_from_quad(quad: np.ndarray) -> tuple[float, float, float, float]:
    """RapidOCR gives each line a 4-point quad (not necessarily axis-aligned, for skewed or
    curved text). The rest of the pipeline draws and merges axis-aligned boxes, so this takes
    the quad's bounding box."""
    xs, ys = quad[:, 0], quad[:, 1]
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def run_ocr(image: np.ndarray, *, engine: RapidOCR | None = None) -> list[TextSpan]:
    """OCR one page image (grayscale or RGB `np.ndarray`), as line-level text spans.

    Returns an empty list rather than raising when the page has no readable text (RapidOCR
    returns an empty result for a blank or unreadable page); an empty span list is a valid,
    if unhelpful, page for the rest of the pipeline to reason about.
    """
    eng = engine if engine is not None else get_engine()
    result = eng(image)
    if result.boxes is None or result.txts is None or result.scores is None:
        return []
    return [
        TextSpan(text=text, box=_box_from_quad(np.asarray(box)), confidence=float(score))
        for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True)
    ]
