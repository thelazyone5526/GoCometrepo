"""Part 2's HTTP routes (design section 2, requirements 8-13): a new router, in a new file --
`api/routes.py` is not edited, so every Part 1 route keeps its exact behaviour (design section
2's "a new file; `api/routes.py` is not edited").

Orchestration trigger (D7 in `docs/part2-decisions.md`): lazy, on read. `GET /api/shipments`
and `GET /api/shipments/{id}` both call `app.shipment.orchestrate.ensure_shipment_decided`
before building their response, so a shipment whose documents just finished gets its
cross-check, decision and draft the moment anyone next looks at it -- the UI's 2 s polling
(design section 4) means that is, in practice, within 2 seconds of the last document
finishing.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import settings
from app.graph.build import run_document
from app.inbox.watcher import document_type_from_state
from app.shipment.orchestrate import ensure_shipment_decided
from app.shipment.send import AlreadySentError, SendError, mark_reviewed_no_send, send_draft
from app.store import repository as repo
from app.store.db import connect

from .shipment_schemas import (
    AttachmentOut,
    CrossCheckOut,
    DocumentStatusOut,
    DraftOut,
    DraftUpdateIn,
    FieldResultOut,
    RerunResponseOut,
    ReviewedResponseOut,
    SendRequestIn,
    SendResponseOut,
    ShipmentDetailOut,
    ShipmentSummaryOut,
)

router = APIRouter(prefix="/api")

CHECKPOINTS_FILENAME = "checkpoints.db"


def _checkpoint_connection():
    base = settings.data_dir
    base.mkdir(parents=True, exist_ok=True)
    import sqlite3

    return sqlite3.connect(base / CHECKPOINTS_FILENAME, check_same_thread=False)


def _documents_done_total(conn, shipment_id: str) -> tuple[int, int]:
    rows = [
        row
        for row in repo.list_shipment_documents(conn, shipment_id)
        if row["origin"] == "own_email"
    ]
    total = len(rows)
    done = sum(1 for row in rows if row["run_id"] is None or row["run_status"] == "completed")
    return done, total


def _shipment_summary(conn, row) -> ShipmentSummaryOut:
    done, total = _documents_done_total(conn, row["shipment_id"])
    return ShipmentSummaryOut(
        shipment_id=row["shipment_id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        sender=row["sender"],
        subject=row["subject"],
        received_at=row["received_at"],
        attachment_count=row["attachment_count"],
        reference=row["reference"],
        cycle_number=row["cycle_number"],
        documents_done=done,
        documents_total=total,
        outcome=row["outcome"],
        review_state=row["review_state"],
        failure_reason=row["failure_reason"],
        discrepancy_count=row["discrepancy_count"],
        uncertain_count=row["uncertain_count"],
    )


@router.get("/shipments", response_model=list[ShipmentSummaryOut])
def get_shipments() -> list[ShipmentSummaryOut]:
    """State 1's list (requirement 8.1-8.4)."""
    conn = connect()
    try:
        rows = repo.list_shipments(conn)
        summaries = []
        for row in rows:
            ensure_shipment_decided(conn, row["shipment_id"])
            refreshed = repo.get_shipment(conn, row["shipment_id"])
            summaries.append(_shipment_summary(conn, refreshed if refreshed else row))
        return summaries
    finally:
        conn.close()


def _box_from_json(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    return json.loads(raw)


def _document_status(row) -> DocumentStatusOut:
    run_status = row["run_id"] and row["run_status"]
    return DocumentStatusOut(
        document_id=row["document_id"],
        filename=row["filename"],
        doc_type=row["doc_type"],
        origin=row["origin"],
        source_cycle=row["source_cycle"],
        run_id=row["run_id"],
        run_status=run_status if row["run_id"] else "waiting",
        run_outcome=row["run_outcome"],
        run_escalation_reason=row["run_escalation_reason"],
    )


def _shipment_detail(conn, shipment_id: str) -> ShipmentDetailOut | None:
    shipment = repo.get_shipment(conn, shipment_id)
    if shipment is None:
        return None

    effective_rows = repo.list_shipment_documents(conn, shipment_id)
    documents = [_document_status(row) for row in effective_rows]

    field_results: list[FieldResultOut] = []
    for row in effective_rows:
        if row["run_id"] is None:
            continue
        for fr in repo.list_field_results(conn, row["run_id"]):
            field_results.append(
                FieldResultOut(
                    document_id=row["document_id"],
                    filename=row["filename"],
                    field_name=fr["field_name"],
                    value=fr["value"],
                    source_text=fr["source_text"],
                    page=fr["page"],
                    box=_box_from_json(fr["box"]),
                    extraction_confidence=fr["extraction_confidence"],
                    verdict=fr["verdict"],
                    found=fr["found"],
                    expected=fr["expected"],
                    rule_id=fr["rule_id"],
                    verdict_reason=fr["verdict_reason"],
                    verdict_confidence=fr["verdict_confidence"],
                )
            )

    cross_check_rows = repo.list_cross_check_results(conn, shipment_id)
    cross_checks = [
        CrossCheckOut(
            field_name=row["field_name"],
            result=row["result"],
            reason=row["reason"],
            compared=json.loads(row["compared"]) if row["compared"] else [],
        )
        for row in cross_check_rows
    ]

    attachments = [
        AttachmentOut(
            filename=row["filename"],
            status=row["status"],
            reason=row["reason"],
            document_id=row["document_id"],
        )
        for row in repo.list_attachments(conn, shipment["email_id"])
    ]

    # requirement 13.4, 13.5: uncertain items are always surfaced -- field-level uncertain
    # verdicts, uncertain cross-checks, and documents whose own doc_type is uncertain/unknown.
    uncertain_items: list[str] = []
    for fr in field_results:
        if fr.verdict == "uncertain":
            uncertain_items.append(f"{fr.filename}: {fr.field_name} is uncertain")
    for cc in cross_checks:
        if cc.result == "uncertain":
            uncertain_items.append(f"cross-document check for {cc.field_name} is uncertain")
    for doc in documents:
        if doc.origin == "own_email" and doc.doc_type in (None, "uncertain"):
            uncertain_items.append(f"{doc.filename}'s document type could not be determined")

    return ShipmentDetailOut(
        shipment_id=shipment["shipment_id"],
        customer_id=shipment["customer_id"],
        customer_name=shipment["customer_name"],
        sender=shipment["sender"],
        subject=shipment["subject"],
        received_at=shipment["received_at"],
        reference=shipment["reference"],
        cycle_number=shipment["cycle_number"],
        outcome=shipment["outcome"],
        reasoning=shipment["reasoning"],
        review_state=shipment["review_state"],
        failure_reason=shipment["failure_reason"],
        discrepancy_count=shipment["discrepancy_count"],
        uncertain_count=shipment["uncertain_count"],
        operator_decision=shipment["operator_decision"],
        operator_decided_at=shipment["operator_decided_at"],
        documents=documents,
        field_results=field_results,
        cross_checks=cross_checks,
        attachments=attachments,
        uncertain_items=uncertain_items,
    )


@router.get("/shipments/{shipment_id}", response_model=ShipmentDetailOut)
def get_shipment(shipment_id: str) -> ShipmentDetailOut:
    """States 2 and 3's payload, in one call (requirement 5.4, 9, 10, 13.5)."""
    conn = connect()
    try:
        ensure_shipment_decided(conn, shipment_id)
        detail = _shipment_detail(conn, shipment_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"No such shipment: {shipment_id}")
        return detail
    finally:
        conn.close()


def _draft_out(conn, shipment_id: str) -> DraftOut | None:
    draft = repo.get_draft(conn, shipment_id)
    if draft is None:
        return None
    versions = repo.list_draft_versions(conn, draft["id"])
    if not versions:
        return None
    generated = versions[0]
    current = versions[-1]
    outbox_row = repo.get_outbox(conn, shipment_id)
    return DraftOut(
        kind=draft["kind"],
        recipient=current["recipient"] or "",
        subject=current["subject"],
        body=current["body"] or "",
        version_number=current["version_number"],
        is_edited=(
            current["recipient"] != generated["recipient"]
            or current["subject"] != generated["subject"]
            or current["body"] != generated["body"]
        ),
        render_error=draft["render_error"],
        sent=outbox_row is not None,
        sent_at=outbox_row["sent_at"] if outbox_row is not None else None,
    )


@router.get("/shipments/{shipment_id}/draft", response_model=DraftOut)
def get_draft(shipment_id: str) -> DraftOut:
    """Requirement 6.4, 11.1-11.3."""
    conn = connect()
    try:
        draft = _draft_out(conn, shipment_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"No draft for shipment: {shipment_id}")
        return draft
    finally:
        conn.close()


@router.put("/shipments/{shipment_id}/draft", response_model=DraftOut)
def put_draft(shipment_id: str, body: DraftUpdateIn) -> DraftOut:
    """Requirement 6.6, 11.1: store an operator edit as a new version. Rejected once sent."""
    conn = connect()
    try:
        draft = repo.get_draft(conn, shipment_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"No draft for shipment: {shipment_id}")
        if repo.get_outbox(conn, shipment_id) is not None:
            raise HTTPException(status_code=409, detail="draft has already been sent")

        repo.add_draft_version(
            conn,
            draft_id=draft["id"],
            recipient=body.recipient,
            subject=body.subject,
            body=body.body,
            source="operator_edit",
        )
        result = _draft_out(conn, shipment_id)
        assert result is not None
        return result
    finally:
        conn.close()


@router.post("/shipments/{shipment_id}/send", response_model=SendResponseOut)
def post_send(shipment_id: str, body: SendRequestIn) -> SendResponseOut:
    """The operator's Send (requirement 7.1, 7.2, 7.4)."""
    conn = connect()
    try:
        shipment = repo.get_shipment(conn, shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail=f"No such shipment: {shipment_id}")

        draft = _draft_out(conn, shipment_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"No draft for shipment: {shipment_id}")

        # requirement 7.4: an approval draft on a shipment with discrepancies or uncertain
        # items needs an explicit confirmation first.
        has_issues = shipment["discrepancy_count"] > 0 or shipment["uncertain_count"] > 0
        if draft.kind == "approval" and has_issues and not body.confirmed:
            raise HTTPException(
                status_code=409,
                detail=(
                    "this shipment has discrepancies or uncertain items; "
                    "resend with confirmed=true to proceed"
                ),
            )

        try:
            result = send_draft(
                conn,
                shipment_id=shipment_id,
                recipient=draft.recipient,
                subject=draft.subject,
                body=draft.body,
            )
        except AlreadySentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return SendResponseOut(review_state=result.review_state, sent_at=result.sent_at)
    finally:
        conn.close()


@router.post("/shipments/{shipment_id}/reviewed", response_model=ReviewedResponseOut)
def post_reviewed(shipment_id: str) -> ReviewedResponseOut:
    """Requirement 7.5, 7.6: reviewed without sending."""
    conn = connect()
    try:
        shipment = repo.get_shipment(conn, shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail=f"No such shipment: {shipment_id}")
        mark_reviewed_no_send(conn, shipment_id=shipment_id)
        return ReviewedResponseOut(review_state="reviewed_no_send")
    finally:
        conn.close()


@router.post(
    "/shipments/{shipment_id}/documents/{document_id}/rerun", response_model=RerunResponseOut
)
def post_rerun(shipment_id: str, document_id: str) -> RerunResponseOut:
    """Requirement 13.3: rerun one document, then repeat cross-check and the decision over
    the effective set. Reruns through the unchanged Part 1 pipeline (`run_document`), exactly
    as `cli resume-one` does for a single run -- this route is the equivalent for a document
    whose own shipment-level context the operator is looking at."""
    conn = connect()
    try:
        shipment = repo.get_shipment(conn, shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail=f"No such shipment: {shipment_id}")

        rows = {
            row["document_id"]: row for row in repo.list_shipment_documents(conn, shipment_id)
        }
        row = rows.get(document_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"document {document_id} is not part of shipment {shipment_id}",
            )

        stored_path = Path(row["stored_path"])
        new_run_id = repo.new_id()
        checkpoint_conn = _checkpoint_connection()
        checkpointer = SqliteSaver(checkpoint_conn)
        final_state = run_document(
            file_path=stored_path,
            customer_id=shipment["customer_id"],
            conn=conn,
            checkpointer=checkpointer,
            document_id=document_id,
            run_id=new_run_id,
        )
        doc_type = document_type_from_state(final_state)
        repo.update_document_doc_type(conn, document_id=document_id, doc_type=doc_type)
        repo.update_shipment_document_run(
            conn,
            shipment_id=shipment_id,
            document_id=document_id,
            run_id=new_run_id,
            doc_type=doc_type,
        )

        ensure_shipment_decided(conn, shipment_id)
        refreshed = repo.get_shipment(conn, shipment_id)
        return RerunResponseOut(
            run_id=new_run_id, outcome=refreshed["outcome"] if refreshed else None
        )
    finally:
        conn.close()


__all__ = ["router"]
