"""Weakest-signal confidence (design section 3.3 step 6): a field is only as trustworthy as
its weakest signal, so there are no weights to defend.

confidence = min(model self-rating, grounding score, source confidence), capped at 0.3 if the
value or format check failed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .grounding import GroundingResult

# design section 3.3 step 6: "grounding score (exact 1.0, near 0.5, not found 0.1)".
_GROUNDING_SCORE = {"exact": 1.0, "near": 0.5, "not_found": 0.1, "absent": 1.0}

# design section 3.3 step 6: "A failed value or format check caps it at 0.3."
FAILED_CHECK_CAP = 0.3


@dataclass(frozen=True)
class ConfidenceInputs:
    self_rating: float
    grounding: GroundingResult
    value_check_ok: bool
    format_ok: bool


def grounding_score(status: str) -> float:
    return _GROUNDING_SCORE[status]


def compute_confidence(inputs: ConfidenceInputs) -> float:
    """The field's confidence: the lowest of the model's self-rating, its grounding score,
    and the source's own confidence -- then capped at 0.3 if the value doesn't actually
    follow from the source text, or doesn't fit the field's expected format.

    `inputs.grounding.source_confidence` is None for an absent field (nothing was matched,
    since there was nothing to look for); that's not a limiting signal, so it's treated as
    1.0 rather than dragging every absent field's confidence to 0.
    """
    source_confidence = inputs.grounding.source_confidence
    base = min(
        inputs.self_rating,
        grounding_score(inputs.grounding.status),
        1.0 if source_confidence is None else source_confidence,
    )
    if not inputs.value_check_ok or not inputs.format_ok:
        return min(base, FAILED_CHECK_CAP)
    return base
