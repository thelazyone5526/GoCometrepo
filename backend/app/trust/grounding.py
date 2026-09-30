"""Grounding (design section 3.3 step 3): does the model's claimed source text actually
appear on the page?

Searches the page's text spans (`app.ingest.textlayer.TextSpan`, whether they came from the
PDF text layer or OCR) for something matching the model's `source_text`, within one span or
across two adjacent spans, on the page the model named first and then across every page. The
result is one of four grounding statuses, plus (for `exact` and `near`) the merged box of the
matching span(s) and the page's own text at that spot, so the UI can show both readings when
they differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from app.ingest.textlayer import Box, TextSpan

from .normalise import text_comparison_key

GroundingStatus = Literal["exact", "near", "not_found", "absent"]

# design section 3.3: "near: at least 90% similar using RapidFuzz".
NEAR_MATCH_THRESHOLD = 90.0


@dataclass(frozen=True)
class GroundingResult:
    status: GroundingStatus
    box: Box | None = None
    # The page's own text at the matched spot, for the UI to show alongside the model's
    # reading when they differ (always set for "near", since the two by definition differ;
    # also set for "exact", where it necessarily equals the model's source text).
    ocr_reading: str | None = None
    score: float = 0.0  # 0-100, RapidFuzz's similarity score for the best match found
    # The lowest span confidence among the matched span(s): 1.0 for a text-layer match, or
    # the weakest OCR confidence among one or two merged spans (design section 3.3 step 6:
    # "source confidence ... else the lowest OCR confidence among the matched spans"). None
    # for "not_found" or "absent", where nothing was matched.
    source_confidence: float | None = None


def _merge_boxes(boxes: list[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


@dataclass(frozen=True)
class _Candidate:
    text: str
    box: Box
    confidence: float  # the lowest confidence among the span(s) this candidate is built from


def _candidates_for_page(spans: list[TextSpan]) -> list[_Candidate]:
    """Every single span, plus every pair of adjacent spans concatenated (design section 3.3:
    "within one span or across two adjacent spans"), as one flat list of things to compare
    the source text against."""
    candidates = [_Candidate(text=s.text, box=s.box, confidence=s.confidence) for s in spans]
    for i in range(len(spans) - 1):
        a, b = spans[i], spans[i + 1]
        candidates.append(
            _Candidate(
                text=f"{a.text} {b.text}",
                box=_merge_boxes([a.box, b.box]),
                confidence=min(a.confidence, b.confidence),
            )
        )
    return candidates


def _score(source_key: str, candidate_key: str) -> float:
    """100 for a normalised substring match -- the common case where the model's source text
    is a label-stripped piece of a longer span or merged pair (e.g. source "INV-2026-00417"
    inside the span "Invoice No. INV-2026-00417"). Otherwise, `fuzz.ratio`'s whole-string
    similarity, for a near match like "ACME" vs "ACEM".

    Deliberately not `fuzz.partial_ratio` as the fallback: it finds the best-aligned *equal-
    length* window of the longer string, so a short candidate like "No." can score as a
    perfect match against any long, unrelated source that merely contains a similar
    substring somewhere -- exactly the false match grounding exists to catch, not produce.
    """
    if not source_key or not candidate_key:
        return 0.0
    if source_key in candidate_key:
        return 100.0
    return fuzz.ratio(source_key, candidate_key)


def ground_field(
    source_text: str | None,
    *,
    pages_spans: list[list[TextSpan]],
    hinted_page: int | None,
) -> GroundingResult:
    """Ground one field's `source_text` against every page's spans.

    `pages_spans[i]` is page `i + 1`'s spans (1-based page numbers, matching the Extractor's
    schema). `hinted_page` is the page the model itself named; it's searched first, so a tie
    on similarity score prefers the model's own page, but a strictly better match elsewhere
    still wins.
    """
    if source_text is None:
        return GroundingResult(status="absent")

    source_key = text_comparison_key(source_text)
    if not source_key:
        return GroundingResult(status="not_found", score=0.0)

    page_order = list(range(1, len(pages_spans) + 1))
    if hinted_page is not None and hinted_page in page_order:
        page_order.remove(hinted_page)
        page_order.insert(0, hinted_page)

    best_score = -1.0
    best_candidate: _Candidate | None = None
    for page_number in page_order:
        spans = pages_spans[page_number - 1]
        for candidate in _candidates_for_page(spans):
            score = _score(source_key, text_comparison_key(candidate.text))
            if score > best_score:
                best_score, best_candidate = score, candidate
                if score >= 100.0:
                    break
        if best_score >= 100.0:
            break

    if best_candidate is None or best_score < NEAR_MATCH_THRESHOLD:
        return GroundingResult(status="not_found", score=max(best_score, 0.0))

    status: GroundingStatus = "exact" if best_score >= 100.0 else "near"
    return GroundingResult(
        status=status,
        box=best_candidate.box,
        ocr_reading=best_candidate.text,
        score=best_score,
        source_confidence=best_candidate.confidence,
    )
