"""The shipment decision (design section 2 and 5, requirements 4.1-4.6, 15.3): one
`Shipment_Outcome` for the whole shipment, settled in plain code from the stored per-document
field verdicts, the cross-document check results and each document's own status -- zero model
calls, same spirit as `app.trust.guardrails` one layer up: the Router already decided a safe
outcome *per document*, and this module decides a second, independent thing *per shipment*,
from results that are already on disk.

Why this has to be a pure function, no I/O, no `app.llm` import: it is swept exhaustively
(`test_decision.py`'s property test, the shipment-level mirror of
`test_guardrails.py`'s 6561-case sweep) the same way `allowed_outcomes` is, and that sweep is
only trustworthy if nothing in here can vary between two calls on the same input -- no clock,
no network, no model. Calling it again later on unchanged stored results must give the same
outcome and the same reasoning text (R4.5), which a pure function gives for free.

R4.2's five `auto_approve` conditions, restated as code: at least one document, every document
succeeded, no Doc_Type is `uncertain` (or unknown), every field verdict is `match` or
`not_applicable`, and every cross-check result is `consistent` or `not_applicable`. Anything
short of that is either `human_review` (R4.3: an uncertain verdict or cross-check, a failed
document, an uncertain Doc_Type, or an empty set) or, failing all of those,
`amendment_request` because a real discrepancy exists (R4.4). `not_applicable` never limits the
outcome either way (R3.5, R4.2) -- it is simply skipped by every check below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.shipment.crosscheck import CrossCheckResult

ShipmentOutcome = Literal["auto_approve", "human_review", "amendment_request"]
FieldVerdictName = Literal["match", "mismatch", "uncertain", "not_applicable"]


@dataclass(frozen=True)
class FieldVerdictEntry:
    """One field's Part 1 verdict on one document of the effective set, as much of it as the
    reasoning text needs (design section 3's `field_results` shape, trimmed to what this
    module reads)."""

    field_name: str
    verdict: FieldVerdictName
    found: str | None = None
    expected: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class DocumentEntry:
    """One document of the shipment's effective set (design section 2's `SetEntry`, plus the
    pipeline results `decide_shipment` actually needs). `doc_type` of `None` or `"uncertain"`
    both mean "the Extractor could not name a supported Doc_Type" (R2.3) -- either limits the
    outcome to `human_review` the same way."""

    document_id: str
    filename: str
    doc_type: str | None
    succeeded: bool
    failure_reason: str | None = None
    field_verdicts: tuple[FieldVerdictEntry, ...] = ()


@dataclass(frozen=True)
class ShipmentDecision:
    outcome: ShipmentOutcome
    reasoning: str
    discrepancy_count: int
    uncertain_count: int


def _doc_type_uncertain(doc: DocumentEntry) -> bool:
    return doc.doc_type is None or doc.doc_type == "uncertain"


def _expected_verdicts(doc: DocumentEntry) -> list[FieldVerdictEntry]:
    # design section 3.4 / R4.2: not_applicable never counts toward anything.
    return [v for v in doc.field_verdicts if v.verdict != "not_applicable"]


def decide_shipment(
    documents: list[DocumentEntry],
    cross_checks: dict[str, CrossCheckResult],
) -> ShipmentDecision:
    """R4.1-R4.6: the one shipment-level decision. Pure function -- no I/O, no `app.llm`
    import anywhere in this module (see `test_decision.py`'s static check, mirroring
    `test_crosscheck.py`'s)."""
    failed_docs = [d for d in documents if not d.succeeded]
    uncertain_type_docs = [d for d in documents if d.succeeded and _doc_type_uncertain(d)]

    mismatches: list[tuple[DocumentEntry, FieldVerdictEntry]] = []
    uncertain_fields: list[tuple[DocumentEntry, FieldVerdictEntry]] = []
    for doc in documents:
        if not doc.succeeded:
            continue
        for v in _expected_verdicts(doc):
            if v.verdict == "mismatch":
                mismatches.append((doc, v))
            elif v.verdict == "uncertain":
                uncertain_fields.append((doc, v))

    inconsistent_checks = [
        (name, result) for name, result in cross_checks.items() if result.result == "inconsistent"
    ]
    uncertain_checks = [
        (name, result) for name, result in cross_checks.items() if result.result == "uncertain"
    ]

    discrepancy_count = len(mismatches) + len(inconsistent_checks)
    uncertain_count = len(uncertain_fields) + len(uncertain_checks) + len(uncertain_type_docs)

    has_discrepancy = discrepancy_count > 0
    forces_human_review = (
        bool(failed_docs or uncertain_type_docs or uncertain_fields or uncertain_checks)
        or not documents
    )

    if not forces_human_review and not has_discrepancy:
        outcome: ShipmentOutcome = "auto_approve"
    elif forces_human_review:
        outcome = "human_review"
    else:
        outcome = "amendment_request"

    reasoning = _build_reasoning(
        outcome=outcome,
        documents=documents,
        failed_docs=failed_docs,
        uncertain_type_docs=uncertain_type_docs,
        mismatches=mismatches,
        uncertain_fields=uncertain_fields,
        inconsistent_checks=inconsistent_checks,
        uncertain_checks=uncertain_checks,
    )

    return ShipmentDecision(
        outcome=outcome,
        reasoning=reasoning,
        discrepancy_count=discrepancy_count,
        uncertain_count=uncertain_count,
    )


def _build_reasoning(
    *,
    outcome: ShipmentOutcome,
    documents: list[DocumentEntry],
    failed_docs: list[DocumentEntry],
    uncertain_type_docs: list[DocumentEntry],
    mismatches: list[tuple[DocumentEntry, FieldVerdictEntry]],
    uncertain_fields: list[tuple[DocumentEntry, FieldVerdictEntry]],
    inconsistent_checks: list[tuple[str, CrossCheckResult]],
    uncertain_checks: list[tuple[str, CrossCheckResult]],
) -> str:
    """R4.5: "a code-generated reasoning text that lists each discrepancy, uncertain item and
    failed document" -- built only from the inputs, so re-running on unchanged results gives
    the same text every time."""
    if not documents:
        return "Sent to human review because the shipment has no documents."

    lines: list[str] = []

    for doc in failed_docs:
        reason = doc.failure_reason or "no reason recorded"
        lines.append(f"{doc.filename} failed processing: {reason}")

    for doc in uncertain_type_docs:
        lines.append(f"{doc.filename}'s document type could not be determined")

    for doc, verdict in mismatches:
        lines.append(
            f"{doc.filename}: {verdict.field_name} is a mismatch "
            f"(found {verdict.found!r}, expected {verdict.expected!r})"
        )

    for name, result in inconsistent_checks:
        lines.append(f"cross-document check for {name} is inconsistent: {result.reason}")

    for doc, verdict in uncertain_fields:
        lines.append(f"{doc.filename}: {verdict.field_name} is uncertain")

    for name, result in uncertain_checks:
        lines.append(f"cross-document check for {name} is uncertain: {result.reason}")

    if outcome == "auto_approve":
        return "Every document in the shipment matched. Recommended for approval."

    prefix = (
        "Sent to human review because: "
        if outcome == "human_review"
        else "Sent back for amendment because: "
    )
    return prefix + "; ".join(lines) + "."


__all__ = [
    "DocumentEntry",
    "FieldVerdictEntry",
    "ShipmentDecision",
    "ShipmentOutcome",
    "decide_shipment",
]
