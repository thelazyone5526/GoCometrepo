"""The Extractor's output schema (design section 3.3, item 1): what Gemini returns.

Kept flat and simple, as the design asks: one `FieldExtraction` per field, as named
attributes rather than a dict (Gemini's structured output handles a fixed set of named
fields more reliably than an open-ended mapping), plus the document type. Every field is
optional at the schema level -- "not on this document" is a real, valid answer (`value` and
`source_text` both null), never something the model has to force a value for.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.trust.fields import DocumentType


class FieldExtraction(BaseModel):
    """What Gemini reports for one field: the value it read, the exact text it read it from,
    which page, and how sure it is. Grounding, format and value checks all happen afterwards,
    in code (`trust/`) -- this schema only carries what the model itself claims."""

    value: str | None = Field(
        description="The field's value, exactly as it should be used downstream (not "
        "necessarily normalised). Null if the field does not appear on this document."
    )
    source_text: str | None = Field(
        description="The exact text as printed on the page that this value came from, "
        "character for character -- never corrected, never paraphrased. Null if `value` is "
        "null."
    )
    page: int | None = Field(
        description="1-based page number the source text was found on. Null if `value` is null."
    )
    self_rating: float = Field(
        ge=0.0,
        le=1.0,
        description="How confident you are in this reading, from 0 (guessing) to 1 (certain "
        "and clearly printed). Rate 0 if `value` is null.",
    )


class ExtractionResult(BaseModel):
    """The full response for one document: its type, and all 8 fields."""

    document_type: DocumentType

    consignee: FieldExtraction
    hs_code: FieldExtraction
    port_of_loading: FieldExtraction
    port_of_discharge: FieldExtraction
    incoterms: FieldExtraction
    goods_description: FieldExtraction
    gross_weight: FieldExtraction
    invoice_number: FieldExtraction

    def field(self, name: str) -> FieldExtraction:
        """Look up one field's extraction by name (`app.trust.fields.FIELD_NAMES`)."""
        return getattr(self, name)

    def fields(self) -> dict[str, FieldExtraction]:
        from app.trust.fields import FIELD_NAMES

        return {name: self.field(name) for name in FIELD_NAMES}


class RetryExtractionResult(BaseModel):
    """The 300 DPI targeted retry's response (design section 3.3 step 7): only the fields
    that were re-requested are expected back, so every field here is optional -- a field the
    model wasn't asked about, or still can't find, is simply absent."""

    consignee: FieldExtraction | None = None
    hs_code: FieldExtraction | None = None
    port_of_loading: FieldExtraction | None = None
    port_of_discharge: FieldExtraction | None = None
    incoterms: FieldExtraction | None = None
    goods_description: FieldExtraction | None = None
    gross_weight: FieldExtraction | None = None
    invoice_number: FieldExtraction | None = None

    def field(self, name: str) -> FieldExtraction | None:
        return getattr(self, name)
