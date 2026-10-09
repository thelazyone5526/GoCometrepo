"""Answer files: the known correct result for every sample document.

One JSON file sits next to each document (``<name>.answer.json``). It holds:
- for each of the 8 fields: the exact printed text, the expected normalised value and the
  expected verdict (what a perfect pipeline returns);
- the planted errors and the image condition, with the exact degradation parameters;
- the acceptable outcomes. Correct documents: auto-approve when clean; auto-approve or human
  review when degraded. Error documents: amendment request or human review, never auto-approve.

The Pydantic model below is the schema. Its validators also enforce those outcome rules, so
an answer file that would let a wrong document be approved can't even be loaded.

Part 2 adds a second model for whole shipments (``ShipmentAnswerFile``, at the bottom of this
file), which describes several documents and the comparisons between them. AnswerFile is left
exactly as it was; see the comment there for why the two are separate.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .degrade import CONDITION_DESCRIPTIONS, Degradation
from .shipments import (
    CROSS_CHECKED_FIELDS,
    EXPECTED_FIELDS,
    FIELD_NAMES,
    Invoice,
    SampleEmail,
    ShipmentDocument,
    Weight,
)

SCHEMA_VERSION = 1

FieldName = Literal[
    "consignee",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "goods_description",
    "gross_weight",
    "invoice_number",
]
Condition = Literal["C0", "C1", "C2", "C3"]
Verdict = Literal["match", "mismatch"]
# Router outcome names from design section 3.5
Outcome = Literal["auto_approve", "human_review", "amendment_request"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WeightValue(_Strict):
    amount: float = Field(gt=0)
    unit: Literal["KG", "LB"]


class ExpectedField(_Strict):
    printed: str | None = Field(description="Exact text on the page; null if not on the document")
    value: WeightValue | str | None = Field(description="Expected normalised value")
    verdict: Verdict

    @model_validator(mode="after")
    def _printed_and_value_agree(self) -> ExpectedField:
        if (self.printed is None) != (self.value is None):
            raise ValueError("printed and value must both be null or both be set")
        return self


class PlantedErrorInfo(_Strict):
    code: str = Field(pattern=r"^E[1-5]$")
    field: FieldName
    description: str


class DegradationInfo(_Strict):
    condition: Literal["C1", "C2", "C3"]
    dpi: int
    grayscale: bool
    rotation_deg: float = Field(description="Counter-clockwise positive")
    blur_sigma: float
    noise_sigma: float
    speckle_fraction: float
    jpeg_quality: int
    stamp: bool
    width_px: int
    height_px: int


class AnswerFile(_Strict):
    schema_version: Literal[1]
    doc_id: str
    file: str = Field(description="Document file name, in the same folder as this answer file")
    description: str
    version: str
    base_version: str
    condition: Condition
    condition_description: str
    degradation: DegradationInfo | None
    customer_id: Literal["acme"]
    doc_type: Literal["commercial_invoice"]
    planted_errors: list[PlantedErrorInfo]
    fields: dict[FieldName, ExpectedField]
    acceptable_outcomes: list[Outcome] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> AnswerFile:
        if tuple(self.fields) != FIELD_NAMES:
            raise ValueError(f"fields must be exactly {FIELD_NAMES}, in that order")
        if (self.condition == "C0") != (self.degradation is None):
            raise ValueError("only C0 has no degradation")
        if self.degradation and self.degradation.condition != self.condition:
            raise ValueError("degradation.condition must equal condition")

        mismatched = {name for name, f in self.fields.items() if f.verdict == "mismatch"}
        planted = {e.field for e in self.planted_errors}
        if mismatched != planted:
            raise ValueError(f"mismatch verdicts {mismatched} must equal planted errors {planted}")

        outcomes = set(self.acceptable_outcomes)
        if self.planted_errors:
            if outcomes != {"amendment_request", "human_review"}:
                raise ValueError("error documents: amendment_request or human_review only")
        elif self.condition == "C0":
            if outcomes != {"auto_approve"}:
                raise ValueError("clean correct documents must be auto-approved")
        elif outcomes != {"auto_approve", "human_review"}:
            raise ValueError("degraded correct documents: auto_approve or human_review")
        return self


def acceptable_outcomes(inv: Invoice, condition: str) -> list[Outcome]:
    if inv.planted_errors:
        return ["amendment_request", "human_review"]
    if condition == "C0":
        return ["auto_approve"]
    return ["auto_approve", "human_review"]


def _value(value: str | Weight | None) -> WeightValue | str | None:
    if isinstance(value, Weight):
        return WeightValue(amount=value.amount, unit=value.unit)
    return value


def build_answer(
    inv: Invoice,
    *,
    doc_id: str,
    file: str,
    condition: str,
    description: str,
    degradation: Degradation | None,
) -> AnswerFile:
    planted = {e.field for e in inv.planted_errors}
    return AnswerFile(
        schema_version=SCHEMA_VERSION,
        doc_id=doc_id,
        file=file,
        description=description,
        version=inv.version,
        base_version=inv.base_version,
        condition=condition,
        condition_description=CONDITION_DESCRIPTIONS[condition],
        degradation=DegradationInfo(**asdict(degradation)) if degradation else None,
        customer_id="acme",
        doc_type="commercial_invoice",
        planted_errors=[
            PlantedErrorInfo(code=e.code, field=e.field, description=e.description)
            for e in inv.planted_errors
        ],
        fields={
            name: ExpectedField(
                printed=truth.printed,
                value=_value(truth.value),
                verdict="mismatch" if name in planted else "match",
            )
            for name, truth in inv.fields.as_dict().items()
        },
        acceptable_outcomes=acceptable_outcomes(inv, condition),
    )


def answer_path(document: Path) -> Path:
    return document.with_name(document.stem + ".answer.json")


def write_answer(answer: AnswerFile | ShipmentAnswerFile, path: Path) -> None:
    path.write_text(answer.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def load_answer(path: Path) -> AnswerFile:
    return AnswerFile.model_validate_json(path.read_text(encoding="utf-8"))


# =============================================================================================
# Part 2: the answer for a whole shipment
#
# A shipment answer is a different claim from a document answer. A document answer says "this
# page says X, and a perfect pipeline returns verdict Y for it". A shipment answer has to say
# that *and* what happens between the documents: which fields were compared across which
# documents, which comparisons agree, and the one outcome that falls out of all of it.
#
# That did not fit AnswerFile. AnswerFile is about one page in one image condition: it demands
# all 8 fields with a match-or-mismatch verdict and a degradation block, and its validators
# encode "a document with a planted error must never be auto-approved". Bending it to also
# describe three documents, four cross-checks and a shipment outcome would have weakened those
# validators, and they guard the 32 documents the eval grid depends on. So this is a second
# model, and AnswerFile keeps its exact behaviour.
#
# What carries over is the part that matters: nothing here is typed out. Every verdict, every
# cross-check result and every count is derived from the ground-truth objects in shipments.py,
# and the validators below re-derive them on load. An answer file that let a shipment with a
# discrepancy be approved cannot be loaded.

SHIPMENT_SCHEMA_VERSION = 1

DocType = Literal["commercial_invoice", "bill_of_lading", "packing_list"]
# Part 2 adds the third verdict: a field the document's type doesn't carry. Not a pass.
ShipmentVerdict = Literal["match", "mismatch", "not_applicable"]
# Cross-document results, from design section 3.3
CrossCheckResult = Literal["consistent", "inconsistent", "not_applicable"]


class ShipmentPlantedErrorInfo(_Strict):
    """A planted problem. ``E`` codes are the single-document errors shipments.py already
    declares; ``X`` codes are cross-document ones, which no single page can reveal."""

    code: str = Field(pattern=r"^[EX]\d$")
    field: FieldName
    description: str


class ShipmentFieldExpectation(_Strict):
    printed: str | None = Field(description="Exact text on the page; null if not on the document")
    value: WeightValue | str | None = Field(description="Expected normalised value")
    verdict: ShipmentVerdict

    @model_validator(mode="after")
    def _printed_and_value_agree(self) -> ShipmentFieldExpectation:
        if (self.printed is None) != (self.value is None):
            raise ValueError("printed and value must both be null or both be set")
        return self


class ShipmentDocumentAnswer(_Strict):
    filename: str
    doc_type: DocType
    expected_fields: list[FieldName] = Field(
        description="Fields this Doc_Type carries, from the customer's rules file"
    )
    planted_errors: list[ShipmentPlantedErrorInfo]
    fields: dict[FieldName, ShipmentFieldExpectation]

    @model_validator(mode="after")
    def _verdicts_follow_the_planted_errors(self) -> ShipmentDocumentAnswer:
        if tuple(self.fields) != FIELD_NAMES:
            raise ValueError(f"fields must be exactly {FIELD_NAMES}, in that order")
        expected = set(self.expected_fields)
        if expected != set(EXPECTED_FIELDS[self.doc_type]):
            raise ValueError(f"expected_fields disagrees with the rules for {self.doc_type}")
        for name, field in self.fields.items():
            if (field.verdict == "not_applicable") != (name not in expected):
                raise ValueError(f"{name}: not_applicable must mean 'not an expected field'")
        mismatched = {n for n, f in self.fields.items() if f.verdict == "mismatch"}
        planted = {e.field for e in self.planted_errors}
        if mismatched != planted:
            raise ValueError(f"mismatch verdicts {mismatched} must equal planted errors {planted}")
        return self


class ComparedValue(_Strict):
    """What one document contributed to one cross-document comparison."""

    filename: str
    doc_type: DocType
    printed: str | None
    value: WeightValue | str | None


class CrossCheckExpectation(_Strict):
    field_name: FieldName
    result: CrossCheckResult
    reason: str
    compared: list[ComparedValue]

    @model_validator(mode="after")
    def _result_matches_what_was_compared(self) -> CrossCheckExpectation:
        if (self.result == "not_applicable") != (len(self.compared) < 2):
            raise ValueError("not_applicable must mean 'fewer than two documents carry the field'")
        if self.result == "consistent":
            values = [c.value for c in self.compared]
            if any(v is None for v in values) or any(v != values[0] for v in values):
                raise ValueError(f"{self.field_name}: compared values are not all equal")
        return self


class ShipmentAnswerFile(_Strict):
    schema_version: Literal[1]
    shipment_id: str
    file: str = Field(description=".eml file name, in the same folder as this answer file")
    description: str
    customer_id: str
    reference: str
    sender: str
    recipient: str
    subject: str
    message_id: str
    sent_at: str
    attachment_filenames: list[str]
    documents: list[ShipmentDocumentAnswer] = Field(min_length=1)
    cross_checks: list[CrossCheckExpectation]
    planted_errors: list[ShipmentPlantedErrorInfo]
    discrepancy_fields: list[FieldName]
    discrepancy_count: int = Field(ge=0)
    expected_outcome: Outcome
    acceptable_outcomes: list[Outcome] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> ShipmentAnswerFile:
        if [d.filename for d in self.documents] != self.attachment_filenames:
            raise ValueError("documents and attachment_filenames must agree, in order")
        if tuple(c.field_name for c in self.cross_checks) != CROSS_CHECKED_FIELDS:
            raise ValueError(f"cross_checks must cover exactly {CROSS_CHECKED_FIELDS}, in order")

        derived = {
            n for d in self.documents for n, f in d.fields.items() if f.verdict == "mismatch"
        }
        derived |= {c.field_name for c in self.cross_checks if c.result == "inconsistent"}
        if set(self.discrepancy_fields) != derived:
            raise ValueError(f"discrepancy_fields must be {sorted(derived)}")
        if self.discrepancy_count != len(self.discrepancy_fields):
            raise ValueError("discrepancy_count must be the number of discrepancy_fields")

        outcomes = set(self.acceptable_outcomes)
        if self.expected_outcome not in outcomes:
            raise ValueError("expected_outcome must be one of acceptable_outcomes")
        if self.discrepancy_count:
            # The guarantee this file exists to protect: a shipment with a discrepancy is never
            # approved, by any route, however many documents looked fine on their own.
            if outcomes != {"amendment_request", "human_review"}:
                raise ValueError("shipments with discrepancies: amendment_request or human_review")
        elif outcomes != {"auto_approve"}:
            raise ValueError("a clean shipment must be auto-approved")
        return self


def _compared(doc: ShipmentDocument, field_name: str) -> ComparedValue:
    truth = getattr(doc.fields, field_name)
    return ComparedValue(
        filename=doc.filename,
        doc_type=doc.doc_type,
        printed=truth.printed,
        value=_value(truth.value),
    )


def _cross_check_reason(field_name: str, result: str, compared: list[ComparedValue]) -> str:
    if result == "not_applicable":
        return f"Only {len(compared)} document carries {field_name}; nothing to compare"
    if result == "consistent":
        return f"All {len(compared)} documents carrying {field_name} agree"
    names = ", ".join(f"{c.filename} = {c.printed!r}" for c in compared)
    return f"{field_name} differs between documents: {names}"


def _document_answer(doc: ShipmentDocument) -> ShipmentDocumentAnswer:
    return ShipmentDocumentAnswer(
        filename=doc.filename,
        doc_type=doc.doc_type,
        expected_fields=[n for n in FIELD_NAMES if n in doc.expected_fields],
        planted_errors=[
            ShipmentPlantedErrorInfo(code=e.code, field=e.field, description=e.description)
            for e in doc.planted_errors
        ],
        fields={
            name: ShipmentFieldExpectation(
                printed=truth.printed,
                value=_value(truth.value),
                verdict=doc.verdict(name),
            )
            for name, truth in doc.fields.as_dict().items()
        },
    )


def shipment_acceptable_outcomes(sample: SampleEmail) -> list[Outcome]:
    """A clean shipment may be approved. One with a discrepancy may only ever be sent back for
    amendment or put in front of a person -- never approved, degraded page or not."""
    if sample.discrepancy_fields:
        return ["amendment_request", "human_review"]
    return ["auto_approve"]


def build_shipment_answer(
    sample: SampleEmail, *, file: str, message_id: str, recipient: str
) -> ShipmentAnswerFile:
    """The answer for one sample shipment, derived entirely from its ground truth."""
    if sample.expected_outcome is None or sample.customer_id is None or sample.reference is None:
        raise ValueError(f"{sample.name} is a failure case, not a shipment with an outcome")
    cross_checks = []
    for name in CROSS_CHECKED_FIELDS:
        result, carriers = sample.cross_check(name)
        compared = [_compared(d, name) for d in carriers]
        cross_checks.append(
            CrossCheckExpectation(
                field_name=name,
                result=result,
                reason=_cross_check_reason(name, result, compared),
                compared=compared,
            )
        )
    return ShipmentAnswerFile(
        schema_version=SHIPMENT_SCHEMA_VERSION,
        shipment_id=sample.name,
        file=file,
        description=sample.description,
        customer_id=sample.customer_id,
        reference=sample.reference,
        sender=sample.sender,
        recipient=recipient,
        subject=sample.subject,
        message_id=message_id,
        sent_at=sample.sent_at,
        attachment_filenames=list(sample.attachment_filenames),
        documents=[_document_answer(d) for d in sample.documents],
        cross_checks=cross_checks,
        planted_errors=[
            ShipmentPlantedErrorInfo(code=e.code, field=e.field, description=e.description)
            for e in sample.planted_errors
        ],
        discrepancy_fields=list(sample.discrepancy_fields),
        discrepancy_count=len(sample.discrepancy_fields),
        expected_outcome=sample.expected_outcome,
        acceptable_outcomes=shipment_acceptable_outcomes(sample),
    )


def shipment_answer_path(eml: Path) -> Path:
    """``01-clean-shipment.eml`` -> ``01-clean-shipment.shipment.json``.

    Deliberately not ``.answer.json``: the Part 1 suite collects every ``*.answer.json`` under
    ``samples/`` and loads each one as an AnswerFile, and a shipment answer is a different
    schema. Its own suffix keeps the two sets of answers from colliding.
    """
    return eml.with_name(eml.stem + ".shipment.json")


def load_shipment_answer(path: Path) -> ShipmentAnswerFile:
    return ShipmentAnswerFile.model_validate_json(path.read_text(encoding="utf-8"))
