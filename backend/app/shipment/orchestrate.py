"""Wires the three pure shipment modules (`crosscheck`, `decision`, `draft`) to the stored
Part 1 results, and persists what they produce (design section 2, requirement wiring for
`api/shipment_routes.py`, task 11).

This is deliberately the one module in `app.shipment` that *does* read and write `conn` --
`crosscheck.py`, `decision.py` and `draft.py` stay pure (R15.3's no-`app.llm`-import tests, and
no I/O at all) so they can be swept exhaustively and unit-tested with plain dataclasses; this
module is the glue that turns `field_results` rows into the dataclasses they expect, and turns
their output back into `cross_check_results` / `shipment_reviews` / `drafts` rows.

Trigger (D7 in `docs/part2-decisions.md`): lazy, on read. `ensure_shipment_decided` is called
by both `GET /api/shipments` and `GET /api/shipments/{id}`, and does nothing (reads cheaply,
returns) once a shipment already has an outcome and every one of its own-email documents is
still in the same finished state -- there is nothing here to make it decide twice for no
reason, but nothing stops a rerun (task 11's rerun route) from clearing the stored outcome and
calling this again either.
"""

from __future__ import annotations

import sqlite3

from app.rules.loader import RuleLoadError, load_rules_for_customer
from app.shipment.crosscheck import CHECKED_FIELDS, DocumentFieldValue, cross_check
from app.shipment.decision import DocumentEntry, FieldVerdictEntry, decide_shipment
from app.shipment.draft import render_draft
from app.store import repository as repo


def _own_email_rows(conn: sqlite3.Connection, shipment_id: str) -> list[sqlite3.Row]:
    return [
        row
        for row in repo.list_shipment_documents(conn, shipment_id)
        if row["origin"] == "own_email"
    ]


def all_own_documents_finished(conn: sqlite3.Connection, shipment_id: str) -> bool:
    """R4.1: "when cross-document validation is complete" -- read as "every document of this
    shipment's own email has a final result". A `run_id` of `None` (a duplicate attachment
    reusing an earlier document, recorded before any pipeline run happened for *this*
    shipment) has nothing to wait for either: the document it reuses already has its own
    finished run from whichever shipment first processed it, which is what the effective set
    actually carries forward. Only a row with a real `run_id` whose run is still `processing`
    blocks the shipment."""
    own_rows = _own_email_rows(conn, shipment_id)
    if not own_rows:
        return False
    for row in own_rows:
        if row["run_id"] is not None and row["run_status"] != "completed":
            return False
    return True


def _field_entries_for_document(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> tuple[list[DocumentFieldValue], list[FieldVerdictEntry]]:
    """One document's `field_results`, mapped onto both `crosscheck.DocumentFieldValue` (one
    per `CHECKED_FIELDS` member the document actually has a row for) and
    `decision.FieldVerdictEntry` (every field, used for the per-document reasoning text)."""
    if row["run_id"] is None:
        return [], []
    field_rows = repo.list_field_results(conn, row["run_id"])
    cross_values = [
        DocumentFieldValue(
            document_id=row["document_id"],
            filename=row["filename"],
            doc_type=row["doc_type"],
            field_name=fr["field_name"],
            value=fr["value"],
            source_text=fr["source_text"],
            page=fr["page"],
            field_result_id=fr["id"],
            uncertain=(fr["verdict"] == "uncertain"),
        )
        for fr in field_rows
        if fr["field_name"] in CHECKED_FIELDS
    ]
    verdicts = [
        FieldVerdictEntry(
            field_name=fr["field_name"],
            verdict=fr["verdict"] or "uncertain",
            found=fr["found"],
            expected=fr["expected"],
            reason=fr["verdict_reason"],
        )
        for fr in field_rows
    ]
    return cross_values, verdicts


def ensure_shipment_decided(conn: sqlite3.Connection, shipment_id: str) -> bool:
    """Run cross-check, the shipment decision and the draft generator for `shipment_id`, and
    persist all three, if (and only if) every document of this shipment's own email now has a
    final result. Safe to call on every read (`GET /api/shipments`, `GET
    /api/shipments/{id}`) -- a shipment still processing, or already decided with nothing new
    to say, is a cheap no-op. Returns whether a decision was (re)computed this call.

    Idempotent by construction: `upsert_cross_check_result` and `set_shipment_outcome` both
    overwrite rather than append, and `create_draft`/`add_draft_version` only append a new
    draft version if a draft already exists and this is a genuine rerun -- see
    `_persist_draft`.
    """
    shipment = repo.get_shipment(conn, shipment_id)
    if shipment is None:
        return False
    if shipment["review_state"] == "failed":
        # R1.5, R1.6: a shipment that never started processing has nothing to decide.
        return False
    if not all_own_documents_finished(conn, shipment_id):
        return False

    effective_rows = repo.list_shipment_documents(conn, shipment_id)

    try:
        rules = load_rules_for_customer(shipment["customer_id"])
        expected_fields_by_doc_type = {
            doc_type: rules.expected_fields_for(doc_type) for doc_type in rules.document_types
        }
    except RuleLoadError:
        expected_fields_by_doc_type = {}

    all_cross_values: list[DocumentFieldValue] = []
    documents: list[DocumentEntry] = []
    for row in effective_rows:
        cross_values, verdicts = _field_entries_for_document(conn, row)
        all_cross_values.extend(cross_values)
        succeeded = row["run_id"] is None or row["run_status"] == "completed"
        documents.append(
            DocumentEntry(
                document_id=row["document_id"],
                filename=row["filename"],
                doc_type=row["doc_type"],
                succeeded=succeeded,
                failure_reason=row["run_escalation_reason"] if succeeded else "processing error",
                field_verdicts=tuple(verdicts),
            )
        )

    cross_checks = cross_check(all_cross_values, expected_fields_by_doc_type)
    for field_name, result in cross_checks.items():
        repo.upsert_cross_check_result(
            conn,
            shipment_id=shipment_id,
            field_name=field_name,
            result=result.result,
            reason=result.reason,
            compared=result.compared,
        )

    decision = decide_shipment(documents, cross_checks)
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome=decision.outcome,
        reasoning=decision.reasoning,
        discrepancy_count=decision.discrepancy_count,
        uncertain_count=decision.uncertain_count,
    )

    rendered = render_draft(
        outcome=decision.outcome,
        sender=shipment["sender"],
        subject=shipment["subject"],
        documents=documents,
        cross_checks=cross_checks,
    )
    _persist_draft(conn, shipment_id, rendered)
    return True


def _persist_draft(conn: sqlite3.Connection, shipment_id: str, rendered) -> None:
    """Write the generated draft. A draft already sent is never touched again -- the
    orchestration can be re-run (for example after `ensure_shipment_decided` is called again
    following a rerun) without clobbering what was actually sent (R7.1-R7.3)."""
    outbox_row = repo.get_outbox(conn, shipment_id)
    if outbox_row is not None:
        return

    existing = repo.get_draft(conn, shipment_id)
    if existing is None:
        repo.create_draft(
            conn,
            shipment_id=shipment_id,
            kind=rendered.kind,
            render_error=rendered.render_error,
            recipient=rendered.recipient,
            subject=rendered.subject,
            body=rendered.body,
        )
        return

    versions = repo.list_draft_versions(conn, existing["id"])
    generated_version = versions[0] if versions else None
    if (
        generated_version is not None
        and generated_version["recipient"] == rendered.recipient
        and generated_version["subject"] == rendered.subject
        and generated_version["body"] == rendered.body
    ):
        # Nothing changed since the first generated version: a plain re-run (another poll,
        # for instance) must not create a spurious new "edited" version.
        return

    repo.add_draft_version(
        conn,
        draft_id=existing["id"],
        recipient=rendered.recipient,
        subject=rendered.subject,
        body=rendered.body,
        source="generated",
    )


__all__ = ["all_own_documents_finished", "ensure_shipment_decided"]
