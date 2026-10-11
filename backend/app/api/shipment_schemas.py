"""Pydantic response/request models for the Part 2 shipment routes (design section 2,
requirements 8-11). Kept in their own module, parallel to `api/schemas.py`, so the Part 1
response models are never touched by this work (design section 2: "a new file; `api/routes.py`
is not edited").
"""

from __future__ import annotations

from pydantic import BaseModel


class DocumentStatusOut(BaseModel):
    """One document of a shipment's effective set, as state 1's progress list and state 2's
    per-document view both need it (requirements 8.2, 9.2, 5.4)."""

    document_id: str
    filename: str
    doc_type: str | None
    origin: str  # own_email, carried_over
    source_cycle: int | None
    run_id: str | None
    run_status: str | None  # waiting (no run yet), processing, completed
    # Which Part 1 pipeline step the run is on: prepare, extract, validate, route, persist
    # (D10); None until a run exists.
    run_current_step: str | None = None
    run_outcome: str | None
    run_escalation_reason: str | None


class FieldResultOut(BaseModel):
    """One field verdict on one document (requirement 9.2, 10.1): the same shape
    `api.schemas.FieldResultOut` already exposes per-run, carried here per-document so state 2
    can show every field of every document in the effective set in one payload."""

    document_id: str
    filename: str
    field_name: str
    value: str | None = None
    source_text: str | None = None
    page: int | None = None
    box: list[float] | None = None
    extraction_confidence: float | None = None
    verdict: str | None = None
    found: str | None = None
    expected: str | None = None
    rule_id: str | None = None
    verdict_reason: str | None = None
    verdict_confidence: float | None = None


class CrossCheckOut(BaseModel):
    """One cross-document check result (requirement 9.3, 10.3)."""

    field_name: str
    result: str  # consistent, inconsistent, uncertain, not_applicable
    reason: str | None
    compared: list[dict]


class AttachmentOut(BaseModel):
    """One attachment of the shipment's email, including rejected and duplicate ones
    (requirement 2.1, 2.2 -- shown so the operator sees everything that arrived)."""

    filename: str
    status: str  # accepted, duplicate, rejected
    reason: str | None
    document_id: str | None


class ShipmentSummaryOut(BaseModel):
    """State 1's list row (requirement 8.1-8.4, 13.5)."""

    shipment_id: str
    customer_id: str
    customer_name: str
    sender: str | None
    subject: str | None
    received_at: str | None
    attachment_count: int
    reference: str | None
    cycle_number: int
    documents_done: int
    documents_total: int
    outcome: str | None
    review_state: str
    failure_reason: str | None
    discrepancy_count: int
    uncertain_count: int


class ShipmentDetailOut(BaseModel):
    """Everything states 2 and 3 need in one payload (requirement 5.4, 9, 10, 13.5)."""

    shipment_id: str
    customer_id: str
    customer_name: str
    sender: str | None
    subject: str | None
    received_at: str | None
    reference: str | None
    cycle_number: int
    outcome: str | None
    reasoning: str | None
    review_state: str
    failure_reason: str | None
    discrepancy_count: int
    uncertain_count: int
    operator_decision: str | None
    operator_decided_at: str | None
    documents: list[DocumentStatusOut]
    field_results: list[FieldResultOut]
    cross_checks: list[CrossCheckOut]
    attachments: list[AttachmentOut]
    uncertain_items: list[str]
    # How many documents the email carried, and their file names as they arrived (recorded
    # before processing starts). Lets the screen list every expected file, including those the
    # pipeline has not reached yet, instead of only the ones finished so far (D10).
    documents_done: int = 0
    documents_total: int = 0
    attachment_filenames: list[str] = []


class DraftOut(BaseModel):
    """`GET`/`PUT /api/shipments/{id}/draft` (requirement 6.4, 11.1-11.4)."""

    kind: str  # approval, amendment, empty
    recipient: str
    subject: str | None
    body: str
    version_number: int
    is_edited: bool
    render_error: str | None
    sent: bool
    sent_at: str | None


class DraftUpdateIn(BaseModel):
    recipient: str
    subject: str | None = None
    body: str


class SendRequestIn(BaseModel):
    confirmed: bool = False


class SendResponseOut(BaseModel):
    review_state: str
    sent_at: str


class ReviewedResponseOut(BaseModel):
    review_state: str


class RerunResponseOut(BaseModel):
    run_id: str
    outcome: str | None


__all__ = [
    "AttachmentOut",
    "CrossCheckOut",
    "DocumentStatusOut",
    "DraftOut",
    "DraftUpdateIn",
    "FieldResultOut",
    "RerunResponseOut",
    "ReviewedResponseOut",
    "SendRequestIn",
    "SendResponseOut",
    "ShipmentDetailOut",
    "ShipmentSummaryOut",
]
