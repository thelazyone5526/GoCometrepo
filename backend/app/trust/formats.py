"""Format checks (design section 3.3 step 5): does the extracted value look like a value of
that type at all, independent of whether it's grounded or correct.

"HS code has at least 6 digits; the Incoterm is one of the 11 Incoterms 2020 rules; the weight
is a number plus a unit; the invoice number isn't empty." Each check returns a plain bool;
`trust/confidence.py` is the one place a failed check turns into a confidence cap.
"""

from __future__ import annotations

from .normalise import Weight, normalise_hs_code, normalise_incoterm, normalise_weight

# The 11 Incoterms 2020 rules (design section 3.3 step 5).
INCOTERMS_2020 = frozenset(
    {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"}
)


def hs_code_format_ok(value: str) -> bool:
    """At least 6 digits."""
    return normalise_hs_code(value) is not None


def incoterm_format_ok(value: str) -> bool:
    """One of the 11 Incoterms 2020 codes."""
    code = normalise_incoterm(value)
    return code is not None and code in INCOTERMS_2020


def weight_format_ok(value: str) -> bool:
    """A number plus a KG or LB unit."""
    return normalise_weight(value) is not None


def invoice_number_format_ok(value: str) -> bool:
    """Not empty (once whitespace is trimmed)."""
    return bool(value.strip())


def port_format_ok(value: str) -> bool:
    """Non-empty text; the alias table (`normalise_port`) is what decides whether it's a
    *known* port, which is the Validator's job (an unknown port is a mismatch, not a bad
    format)."""
    return bool(value.strip())


def text_format_ok(value: str) -> bool:
    """Any non-empty text field (consignee, goods_description)."""
    return bool(value.strip())


FORMAT_CHECKS: dict[str, object] = {
    "consignee": text_format_ok,
    "hs_code": hs_code_format_ok,
    "port_of_loading": port_format_ok,
    "port_of_discharge": port_format_ok,
    "incoterms": incoterm_format_ok,
    "goods_description": text_format_ok,
    "gross_weight": weight_format_ok,
    "invoice_number": invoice_number_format_ok,
}


def format_ok(field_name: str, value: str) -> bool:
    """Run `field_name`'s format check. Fields with no dedicated check (there are none
    currently, but future document types may add optional fields) default to non-empty."""
    check = FORMAT_CHECKS.get(field_name, text_format_ok)
    return bool(check(value))


__all__ = [
    "FORMAT_CHECKS",
    "INCOTERMS_2020",
    "Weight",
    "format_ok",
    "hs_code_format_ok",
    "incoterm_format_ok",
    "invoice_number_format_ok",
    "port_format_ok",
    "text_format_ok",
    "weight_format_ok",
]
