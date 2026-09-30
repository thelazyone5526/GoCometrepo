"""Deterministic trust layer: grounding, normalisers, formats, confidence and guardrails."""

from .confidence import ConfidenceInputs, compute_confidence
from .fields import FIELD_NAMES, DocumentType
from .formats import format_ok
from .grounding import GroundingResult, GroundingStatus, ground_field
from .normalise import NORMALISERS, Weight, normalise_text, text_comparison_key
from .value_check import value_matches_source

__all__ = [
    "FIELD_NAMES",
    "NORMALISERS",
    "ConfidenceInputs",
    "DocumentType",
    "GroundingResult",
    "GroundingStatus",
    "Weight",
    "compute_confidence",
    "format_ok",
    "ground_field",
    "normalise_text",
    "text_comparison_key",
    "value_matches_source",
]
