"""Grounding tests (Phase 5 verify list): an invented value gives not found; "ACME" read from
a page printing "ACEM" gives near, with both readings shown; "12,450.00 KGS" gives 12450 KG
(that last one via the value check, exercised together with grounding in test_extract.py).
"""

from __future__ import annotations

from app.ingest.textlayer import TextSpan
from app.trust.grounding import NEAR_MATCH_THRESHOLD, ground_field


def _span(text: str, confidence: float = 1.0) -> TextSpan:
    return TextSpan(text=text, box=(0.0, 0.0, 100.0, 20.0), confidence=confidence)


def test_an_invented_value_gives_not_found() -> None:
    pages_spans = [[_span("ACME Electronics Pte. Ltd."), _span("Invoice No. INV-2026-00417")]]
    result = ground_field("Something Nobody Printed", pages_spans=pages_spans, hinted_page=1)
    assert result.status == "not_found"
    assert result.box is None


def test_acme_read_from_a_page_printing_acem_gives_near_with_both_readings() -> None:
    pages_spans = [[_span("ACEM Electronics Pte. Ltd.")]]
    result = ground_field("ACME Electronics Pte. Ltd.", pages_spans=pages_spans, hinted_page=1)
    assert result.status == "near"
    assert result.ocr_reading == "ACEM Electronics Pte. Ltd."
    assert result.score >= NEAR_MATCH_THRESHOLD
    assert result.box is not None


def test_an_exact_match_within_one_span() -> None:
    pages_spans = [[_span("Invoice No. INV-2026-00417")]]
    result = ground_field("INV-2026-00417", pages_spans=pages_spans, hinted_page=1)
    assert result.status == "exact"
    assert result.score == 100.0
    assert result.source_confidence == 1.0


def test_an_exact_match_across_two_adjacent_spans() -> None:
    pages_spans = [[_span("Portable laptop computers,"), _span("14-inch, 16 GB RAM")]]
    result = ground_field(
        "Portable laptop computers, 14-inch, 16 GB RAM", pages_spans=pages_spans, hinted_page=1
    )
    assert result.status == "exact"


def test_a_null_source_text_is_absent() -> None:
    result = ground_field(None, pages_spans=[[_span("anything")]], hinted_page=None)
    assert result.status == "absent"
    assert result.box is None
    assert result.source_confidence is None


def test_search_prefers_the_hinted_page_but_a_better_match_elsewhere_still_wins() -> None:
    pages_spans = [
        [_span("ACEM Electronics Pte. Ltd.")],  # page 1: a near match
        [_span("ACME Electronics Pte. Ltd.")],  # page 2: an exact match
    ]
    result = ground_field("ACME Electronics Pte. Ltd.", pages_spans=pages_spans, hinted_page=1)
    assert result.status == "exact"


def test_ocr_confidence_carries_through_as_source_confidence() -> None:
    pages_spans = [[_span("INV-2026-00417", confidence=0.62)]]
    result = ground_field("INV-2026-00417", pages_spans=pages_spans, hinted_page=1)
    assert result.status == "exact"
    assert result.source_confidence == 0.62


def test_merged_box_across_two_spans_covers_both() -> None:
    span_a = TextSpan(
        text="Portable laptop computers,", box=(10.0, 10.0, 60.0, 20.0), confidence=1.0
    )
    span_b = TextSpan(text="14-inch", box=(65.0, 10.0, 90.0, 20.0), confidence=1.0)
    result = ground_field(
        "Portable laptop computers, 14-inch", pages_spans=[[span_a, span_b]], hinted_page=1
    )
    assert result.box == (10.0, 10.0, 90.0, 20.0)
