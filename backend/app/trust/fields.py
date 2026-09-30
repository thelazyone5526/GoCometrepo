"""The 8 fields every commercial invoice extraction covers (design section 3.2 and 3.3).

Shared by the Extractor schema, the normalisers, grounding, and later the Validator and
Router, so the field list and the document-type enum live in exactly one place.
"""

from __future__ import annotations

from typing import Literal

FIELD_NAMES: tuple[str, ...] = (
    "consignee",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "goods_description",
    "gross_weight",
    "invoice_number",
)

DocumentType = Literal[
    "commercial_invoice", "bill_of_lading", "packing_list", "certificate_of_origin", "other"
]
