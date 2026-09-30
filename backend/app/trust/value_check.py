"""The value check (design section 3.3 step 4): does the model's `value` actually follow from
its `source_text`, using that field's normaliser?

"If '12,450.00 KGS' becomes 12450 KG, fine. If the model's value doesn't follow from what's
printed, it quietly changed something, and the field becomes uncertain." For fields with a
structured normaliser (HS code, weight, Incoterm, port), the check applies that normaliser to
both strings and compares the results -- so "8471.30" and "847130" agree, but "8471.30" and
"8504.40" don't. For plain text fields, it compares case/punctuation-normalised keys, so
"ACME Electronics" and "acme electronics" agree, but not "ACME Electronics" and "ACEM
Electronics".
"""

from __future__ import annotations

from .normalise import (
    normalise_hs_code,
    normalise_incoterm,
    normalise_port,
    normalise_weight,
    text_comparison_key,
)

_STRUCTURED_NORMALISERS: dict[str, object] = {
    "hs_code": normalise_hs_code,
    "port_of_loading": normalise_port,
    "port_of_discharge": normalise_port,
    "incoterms": normalise_incoterm,
    "gross_weight": normalise_weight,
}

_TEXT_FIELDS = frozenset({"consignee", "goods_description", "invoice_number"})


def value_matches_source(field_name: str, value: str, source_text: str) -> bool:
    """True if `value` is a faithful reading of `source_text` for this field.

    Returns False (never raises) when either string doesn't even parse as this field's
    expected shape -- that's a format failure, not a value-check pass, and the two are kept
    separate so `trust/confidence.py` can report exactly which check failed.
    """
    if field_name in _TEXT_FIELDS:
        return text_comparison_key(value) == text_comparison_key(source_text)

    normaliser = _STRUCTURED_NORMALISERS.get(field_name)
    if normaliser is None:
        return text_comparison_key(value) == text_comparison_key(source_text)

    value_normalised = normaliser(value)
    source_normalised = normaliser(source_text)
    if value_normalised is None or source_normalised is None:
        return False
    return value_normalised == source_normalised
