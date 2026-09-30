"""Confidence tests (Phase 5 verify list): weakest-signal confidence, and the 0.3 cap on a
failed value or format check."""

from __future__ import annotations

import pytest

from app.trust.confidence import FAILED_CHECK_CAP, ConfidenceInputs, compute_confidence
from app.trust.grounding import GroundingResult


def _inputs(
    *,
    self_rating: float = 1.0,
    grounding: GroundingResult,
    value_check_ok: bool = True,
    format_ok: bool = True,
) -> ConfidenceInputs:
    return ConfidenceInputs(
        self_rating=self_rating,
        grounding=grounding,
        value_check_ok=value_check_ok,
        format_ok=format_ok,
    )


def test_confidence_is_the_lowest_of_self_rating_grounding_and_source() -> None:
    grounding = GroundingResult(status="exact", source_confidence=0.7)
    result = compute_confidence(_inputs(self_rating=0.95, grounding=grounding))
    assert result == pytest.approx(0.7)


def test_a_near_grounding_caps_confidence_at_its_score() -> None:
    grounding = GroundingResult(status="near", source_confidence=1.0)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding))
    assert result == pytest.approx(0.5)


def test_not_found_grounding_caps_confidence_low() -> None:
    grounding = GroundingResult(status="not_found", source_confidence=None)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding))
    assert result == pytest.approx(0.1)


def test_an_absent_field_is_not_penalised_by_a_missing_source_confidence() -> None:
    grounding = GroundingResult(status="absent", source_confidence=None)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding))
    assert result == pytest.approx(1.0)


def test_a_failed_value_check_caps_confidence_at_0_3() -> None:
    grounding = GroundingResult(status="exact", source_confidence=1.0)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding, value_check_ok=False))
    assert result == FAILED_CHECK_CAP


def test_a_failed_format_check_caps_confidence_at_0_3() -> None:
    grounding = GroundingResult(status="exact", source_confidence=1.0)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding, format_ok=False))
    assert result == FAILED_CHECK_CAP


def test_the_cap_never_raises_an_already_lower_confidence() -> None:
    grounding = GroundingResult(status="near", source_confidence=0.2)
    result = compute_confidence(_inputs(self_rating=1.0, grounding=grounding, value_check_ok=False))
    assert result == pytest.approx(0.2)
