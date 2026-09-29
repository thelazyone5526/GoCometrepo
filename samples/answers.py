"""Answer files: the known correct result for every sample document.

One JSON file sits next to each document (``<name>.answer.json``). It holds:
- for each of the 8 fields: the exact printed text, the expected normalised value and the
  expected verdict (what a perfect pipeline returns);
- the planted errors and the image condition, with the exact degradation parameters;
- the acceptable outcomes. Correct documents: auto-approve when clean; auto-approve or human
  review when degraded. Error documents: amendment request or human review, never auto-approve.

The Pydantic model below is the schema. Its validators also enforce those outcome rules, so
an answer file that would let a wrong document be approved can't even be loaded.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .degrade import CONDITION_DESCRIPTIONS, Degradation
from .shipments import FIELD_NAMES, Invoice, Weight

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


def write_answer(answer: AnswerFile, path: Path) -> None:
    path.write_text(answer.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def load_answer(path: Path) -> AnswerFile:
    return AnswerFile.model_validate_json(path.read_text(encoding="utf-8"))
