"""One normaliser per field (design section 3.3 step 4, and the canonical values in design
section 10.1): turns the model's raw `value` string into the normalised form the Validator's
rules compare against.

Every normaliser is a pure function `str -> NormalisedValue | None`, returning `None` when the
raw text doesn't parse as that field's expected shape at all (a format failure, handled by
`trust/formats.py` and folded into confidence, not raised as an exception here).

Text fields (consignee, goods_description, invoice_number) normalise to the printed text
itself, only trimmed and whitespace-collapsed -- design section 10.1's canonical values are
the printed text verbatim, e.g. `"ACME Electronics Pte. Ltd."`, not a casefolded key. Where
two text values need to be *compared* (the value check in `trust/grounding.py`: does the
extracted value follow from the source text?), use `text_comparison_key` instead, which
additionally casefolds and drops punctuation, so "ACME Electronics Pte. Ltd." and "acme
electronics pte ltd" compare equal without changing what gets stored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Unit = Literal["KG", "LB"]

# Canonical weight units and their aliases, as printed on real invoices.
_KG_ALIASES = {"KG", "KGS", "KILOGRAM", "KILOGRAMS"}
_LB_ALIASES = {"LB", "LBS", "POUND", "POUNDS"}

# Port aliases -> the rules file's canonical name (design section 10.1: "Phase 5's alias table
# must map the printed forms 'SHANGHAI, CHINA', 'YANTIAN, CHINA' and 'SINGAPORE' to Shanghai,
# Yantian and Singapore"). Matched case-insensitively, after stripping the country suffix.
_PORT_ALIASES: dict[str, str] = {
    "SHANGHAI": "Shanghai",
    "SHANGHAI, CHINA": "Shanghai",
    "YANTIAN": "Yantian",
    "YANTIAN, CHINA": "Yantian",
    "SINGAPORE": "Singapore",
}


@dataclass(frozen=True)
class Weight:
    amount: float
    unit: Unit


def normalise_text(value: str) -> str:
    """The canonical stored value for a text field: trimmed and whitespace-collapsed, but
    otherwise exactly as printed (design section 10.1: "Text fields: the printed text
    itself"). Case and punctuation are preserved here -- use `text_comparison_key` to compare
    two text values for equivalence."""
    return " ".join(value.split())


def text_comparison_key(value: str) -> str:
    """A normalised key for comparing two text values as equivalent (design section 3.3 step
    4 and 10.1: "compared after normalising case, spacing and punctuation on both sides").

    NFKC first, so visually identical characters compare equal; then punctuation dropped
    entirely (not just whitespace-collapsed), so "Pte. Ltd." and "Pte Ltd" produce the same
    key.
    """
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"[^\w\s]", "", text)
    text = " ".join(text.split())
    return text.casefold()


def normalise_hs_code(value: str) -> str | None:
    """Digits only (design section 10.1: "HS code: digits only (847130)"). None if there
    aren't at least 6 digits -- too short to be a real HS code, a format failure."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 6:
        return None
    return digits


def normalise_weight(value: str) -> Weight | None:
    """A number plus a KG/LB unit (design section 10.1). Accepts thousands separators like
    "12,450.00 KGS". None if no number, or no recognised unit, is found."""
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*([A-Za-z]+)", value)
    if not match:
        return None
    number_text, unit_text = match.groups()
    try:
        amount = float(number_text.replace(",", ""))
    except ValueError:
        return None
    unit_upper = unit_text.upper()
    if unit_upper in _KG_ALIASES:
        return Weight(amount=amount, unit="KG")
    if unit_upper in _LB_ALIASES:
        return Weight(amount=amount, unit="LB")
    return None


def normalise_incoterm(value: str) -> str | None:
    """The Incoterm code, with any place name split off (design section 10.1: "CIF" from
    "CIF Singapore"). The code is the first whitespace-separated token, upper-cased. None if
    that token isn't purely letters (so isn't a plausible 3-letter code)."""
    token = value.strip().split(None, 1)[0] if value.strip() else ""
    if not token.isalpha():
        return None
    return token.upper()


def normalise_port(value: str) -> str | None:
    """The rules file's canonical port name, via the alias table. None if the printed text
    isn't one of the known ports (a real gap the Validator would then see as a mismatch, not
    something this function should silently invent a mapping for)."""
    key = value.strip().upper()
    return _PORT_ALIASES.get(key)


NORMALISERS: dict[str, object] = {
    "consignee": normalise_text,
    "hs_code": normalise_hs_code,
    "port_of_loading": normalise_port,
    "port_of_discharge": normalise_port,
    "incoterms": normalise_incoterm,
    "goods_description": normalise_text,
    "gross_weight": normalise_weight,
    "invoice_number": normalise_text,
}
