"""Tests for the Part 2 storage layer (part2-shipment-workflow design section 3): the eight new
tables, their indexes, `v_pending_shipments`, and the repository functions that write them.

Two things are being proved here, and the second matters as much as the first:

1. The new tables behave -- and specifically that the three load-bearing unique indexes really
   refuse the writes they are supposed to refuse. `emails.message_id` (the restart guarantee,
   R1.3), `outbox.shipment_id` (the double-send guarantee, R7.2) and
   `shipment_documents(shipment_id, document_id)` (at-most-once in the effective set, R5.3) are
   relied on by code that catches `IntegrityError` instead of pre-checking, so a test that only
   exercised the happy path would let someone drop an index without noticing.

2. Part 1 is untouched (R12.6, R15.4). The column lists of all five Part 1 tables and the two
   existing views are asserted explicitly, so adding Part 2 cannot quietly reshape them.

`settings.data_dir` is pointed at `tmp_path` per test, same `object.__setattr__` approach as
`tests/test_graph.py`'s `data_dir` fixture, so nothing here ever touches the real `data/`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.store import repository as repo
from app.store.db import connect

PART2_TABLES = [
    "emails",
    "shipment_reviews",
    "shipment_attachments",
    "shipment_documents",
    "cross_check_results",
    "drafts",
    "draft_versions",
    "outbox",
]

PART2_INDEXES = [
    "ix_emails_message_id",
    "ix_shipment_reviews_shipment",
    "ix_shipment_attachments_email",
    "ix_shipment_documents_once",
    "ix_cross_check_results_field",
    "ix_drafts_shipment",
    "ix_draft_versions_number",
    "ix_draft_versions_draft",
    "ix_outbox_shipment",
]

# Part 1's tables exactly as design section 4 defines them. Asserted as full ordered lists, not
# "contains", so a dropped or reordered column fails here.
PART1_COLUMNS = {
    "shipments": ["id", "customer_id", "customer_name", "reference", "created_at"],
    "documents": [
        "id",
        "shipment_id",
        "filename",
        "file_hash",
        "mime_type",
        "page_count",
        "has_text_layer",
        "stored_path",
        "doc_type",
        "created_at",
    ],
    "runs": [
        "id",
        "document_id",
        "status",
        "current_step",
        "outcome",
        "reasoning",
        "decision_source",
        "override_reason",
        "escalation_reason",
        "amendment_draft",
        "llm_calls",
        "fallback_used",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "started_at",
        "finished_at",
        "reviewed_by",
        "final_outcome",
        "reviewed_at",
    ],
    "field_results": [
        "id",
        "run_id",
        "field_name",
        "value",
        "source_text",
        "page",
        "box",
        "model_rating",
        "grounding",
        "ocr_reading",
        "format_ok",
        "extraction_confidence",
        "verdict",
        "found",
        "expected",
        "rule_id",
        "verdict_reason",
        "verdict_confidence",
    ],
    "llm_calls": [
        "id",
        "run_id",
        "agent",
        "model",
        "is_fallback",
        "prompt_version",
        "attempt",
        "status",
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "latency_ms",
        "error",
        "created_at",
    ],
}

V_DOCUMENTS_COLUMNS = [
    "run_id",
    "customer_name",
    "doc_type",
    "filename",
    "outcome",
    "reasoning",
    "decision_source",
    "matched_count",
    "mismatched_count",
    "uncertain_count",
    "fallback_used",
    "cost_usd",
    "latency_ms",
    "started_at",
    "finished_at",
]

V_FIELDS_COLUMNS = [
    "field_result_id",
    "run_id",
    "customer_name",
    "filename",
    "doc_type",
    "outcome",
    "field_name",
    "value",
    "found",
    "expected",
    "verdict",
    "verdict_reason",
    "rule_id",
    "extraction_confidence",
    "verdict_confidence",
]

V_PENDING_SHIPMENTS_COLUMNS = [
    "shipment_reference",
    "customer_id",
    "customer_name",
    "subject",
    "received_at",
    "cycle_number",
    "document_count",
    "outcome",
    "discrepancy_count",
    "uncertain_count",
    "review_state",
    "decided_at",
]


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    """A scratch `data/` per test. `Settings` is a frozen dataclass, so `object.__setattr__`
    bypasses that (deliberately, test-only) and is restored afterwards."""
    from app.config import settings

    original = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path)
    try:
        yield tmp_path
    finally:
        object.__setattr__(settings, "data_dir", original)


@pytest.fixture
def conn(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(data_dir=data_dir)
    try:
        yield connection
    finally:
        connection.close()


# --- helpers ----------------------------------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
    return {row["name"] for row in rows}


def _seed_email(conn: sqlite3.Connection, *, message_id: str = "<m-1@su.example>") -> str:
    return repo.record_email(
        conn,
        message_id=message_id,
        eml_path=f"inbox/{message_id}.eml",
        sender="ops@su.example",
        subject="ACME-001 SHP-5501 documents attached",
        received_at="2026-10-10T09:00:00+00:00",
        attachment_count=2,
        attachment_filenames=["invoice.pdf", "bol.pdf"],
    )


def _seed_shipment(
    conn: sqlite3.Connection,
    *,
    email_id: str,
    reference: str = "SHP-5501",
    cycle_number: int = 1,
    review_state: str = "pending_review",
) -> str:
    return repo.create_shipment_for_email(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        reference=reference,
        email_id=email_id,
        cycle_number=cycle_number,
        parent_shipment_id=None,
        review_state=review_state,
    )


def _seed_document(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    filename: str = "invoice.pdf",
    file_hash: str = "hash-invoice",
    doc_type: str | None = "commercial_invoice",
) -> repo.DocumentRecord:
    return repo.create_document_for_shipment(
        conn,
        shipment_id=shipment_id,
        filename=filename,
        file_hash=file_hash,
        mime_type="application/pdf",
        page_count=1,
        has_text_layer=True,
        stored_path=Path("uploads") / file_hash / "original.pdf",
        doc_type=doc_type,
    )


def _seed_draft(conn: sqlite3.Connection, shipment_id: str) -> tuple[str, str]:
    return repo.create_draft(
        conn,
        shipment_id=shipment_id,
        kind="approval",
        render_error=None,
        recipient="ops@su.example",
        subject="Re: ACME-001 SHP-5501 documents attached",
        body="All documents verified. No amendment needed.",
    )


# --- schema -----------------------------------------------------------------------------------


def test_init_db_creates_every_part2_table_and_the_new_view(conn: sqlite3.Connection) -> None:
    tables = _objects(conn, "table")
    assert set(PART2_TABLES) <= tables
    assert "v_pending_shipments" in _objects(conn, "view")


def test_init_db_creates_every_part2_index(conn: sqlite3.Connection) -> None:
    """The three load-bearing ones (emails.message_id, outbox.shipment_id,
    shipment_documents pair) are guarantees other code depends on, so their absence is a
    correctness bug, not a missing optimisation."""
    assert set(PART2_INDEXES) <= _objects(conn, "index")


def test_init_db_is_idempotent(data_dir: Path) -> None:
    """Every statement is `IF NOT EXISTS`, so a second start-up against the same file is a
    no-op -- which is what makes `connect()` safe to call per request."""
    first = connect(data_dir=data_dir)
    try:
        before = _objects(first, "table") | _objects(first, "view") | _objects(first, "index")
    finally:
        first.close()

    second = connect(data_dir=data_dir)
    try:
        after = _objects(second, "table") | _objects(second, "view") | _objects(second, "index")
    finally:
        second.close()

    assert before == after


@pytest.mark.parametrize("table", sorted(PART1_COLUMNS))
def test_part1_tables_are_unchanged(conn: sqlite3.Connection, table: str) -> None:
    """R12.6 / R15.4: Part 2 adds tables, it does not touch Part 1's."""
    assert _table_columns(conn, table) == PART1_COLUMNS[table]


def test_part1_indexes_are_unchanged(conn: sqlite3.Connection) -> None:
    indexes = _objects(conn, "index")
    assert "ix_documents_hash_customer" in indexes
    assert "ix_field_results_run" in indexes
    assert "ix_llm_calls_run" in indexes


def test_existing_views_still_return_their_columns(conn: sqlite3.Connection) -> None:
    """Queried, not just inspected, so a view that parses but no longer resolves against its
    base tables fails here. The query layer reads these by column name."""
    assert _table_columns(conn, "v_documents") == V_DOCUMENTS_COLUMNS
    assert _table_columns(conn, "v_fields") == V_FIELDS_COLUMNS

    documents = conn.execute("SELECT * FROM v_documents").fetchall()
    fields = conn.execute("SELECT * FROM v_fields").fetchall()
    assert documents == [] and fields == []


# --- the load-bearing unique indexes ----------------------------------------------------------


def test_the_same_message_id_cannot_be_recorded_twice(conn: sqlite3.Connection) -> None:
    """R1.3, the restart guarantee: rescanning an inbox after a restart must not create a
    second shipment for an email already processed."""
    _seed_email(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _seed_email(conn)

    assert len(conn.execute("SELECT id FROM emails").fetchall()) == 1


def test_a_second_send_for_the_same_shipment_is_refused(conn: sqlite3.Connection) -> None:
    """R7.2: the double-send guarantee lives in the database, not in an `if`. The single send
    path catches this and reports "already sent"; nothing is written twice."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    draft_id, version_id = _seed_draft(conn, shipment_id)

    repo.insert_outbox(
        conn,
        shipment_id=shipment_id,
        draft_id=draft_id,
        draft_version_id=version_id,
        recipient="ops@su.example",
        subject="Re: documents",
        body="All documents verified.",
    )

    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_outbox(
            conn,
            shipment_id=shipment_id,
            draft_id=draft_id,
            draft_version_id=version_id,
            recipient="ops@su.example",
            subject="Re: documents (again)",
            body="Second attempt.",
        )

    rows = conn.execute("SELECT * FROM outbox WHERE shipment_id = ?", (shipment_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["subject"] == "Re: documents"
    assert repo.get_outbox(conn, shipment_id)["sent_at"]


def test_adding_the_same_document_to_a_shipment_twice_leaves_one_row(
    conn: sqlite3.Connection,
) -> None:
    """R5.3, at-most-once: the effective set is rebuilt after a rerun and after a resume, so
    `add_shipment_document` is called again for pairs already stored."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    document = _seed_document(conn, shipment_id=shipment_id)

    for _ in range(2):
        repo.add_shipment_document(
            conn,
            shipment_id=shipment_id,
            document_id=document.id,
            run_id=None,
            doc_type="commercial_invoice",
            origin="own_email",
            source_shipment_id=None,
            source_cycle=None,
        )

    entries = repo.list_shipment_documents(conn, shipment_id)
    assert len(entries) == 1
    assert entries[0]["origin"] == "own_email"


# --- repository behaviour ---------------------------------------------------------------------


def test_record_email_stores_filenames_as_json_and_is_findable(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)

    found = repo.find_email_by_message_id(conn, "<m-1@su.example>")
    assert found is not None
    assert found["id"] == email_id
    assert json.loads(found["attachment_filenames"]) == ["invoice.pdf", "bol.pdf"]
    assert repo.find_email_by_message_id(conn, "<nope@su.example>") is None


def test_create_shipment_for_email_writes_both_rows(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    shipment = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    assert shipment["reference"] == "SHP-5501"  # the Part 1 column, unused until now

    row = repo.get_shipment(conn, shipment_id)
    assert row is not None
    assert row["review_state"] == "pending_review"
    assert row["cycle_number"] == 1
    assert row["customer_name"] == "ACME Electronics Pte. Ltd."
    assert row["subject"] == "ACME-001 SHP-5501 documents attached"
    assert row["outcome"] is None
    assert row["document_count"] == 0


def test_create_document_for_shipment_writes_doc_type(conn: sqlite3.Connection) -> None:
    """Part 2 design decision 3: the never-written `documents.doc_type` column is filled for
    Part 2 rows, which is what gives the query layer document types."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    document = _seed_document(conn, shipment_id=shipment_id, doc_type="bill_of_lading")

    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document.id,)).fetchone()
    assert row["doc_type"] == "bill_of_lading"
    assert row["shipment_id"] == shipment_id
    assert row["has_text_layer"] == 1


def test_attachments_are_recorded_even_when_rejected(conn: sqlite3.Connection) -> None:
    """R2.1: every attachment is recorded, including the ones that never became documents --
    a rejected file must not simply disappear."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    document = _seed_document(conn, shipment_id=shipment_id)

    repo.record_attachment(
        conn,
        email_id=email_id,
        filename="invoice.pdf",
        file_hash="hash-invoice",
        status="accepted",
        reason=None,
        document_id=document.id,
    )
    repo.record_attachment(
        conn,
        email_id=email_id,
        filename="notes.txt",
        file_hash=None,
        status="rejected",
        reason="Not a PDF.",
        document_id=None,
    )

    rows = repo.list_attachments(conn, email_id)
    assert [(r["filename"], r["status"]) for r in rows] == [
        ("invoice.pdf", "accepted"),
        ("notes.txt", "rejected"),
    ]
    assert rows[1]["reason"] == "Not a PDF."


def test_find_document_by_hash_in_chain(conn: sqlite3.Connection) -> None:
    """R2.2: a byte-identical attachment already somewhere in the resubmission chain is reused
    rather than processed again. An empty chain can match nothing."""
    email_id = _seed_email(conn)
    cycle1 = _seed_shipment(conn, email_id=email_id)
    document = _seed_document(conn, shipment_id=cycle1)

    email2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle2 = _seed_shipment(conn, email_id=email2, cycle_number=2)

    found = repo.find_document_by_hash_in_chain(
        conn, shipment_ids=[cycle2, cycle1], file_hash="hash-invoice"
    )
    assert found is not None and found.id == document.id

    assert (
        repo.find_document_by_hash_in_chain(conn, shipment_ids=[cycle2], file_hash="hash-invoice")
        is None
    )
    assert repo.find_document_by_hash_in_chain(conn, shipment_ids=[], file_hash="x") is None


def test_find_latest_shipment_by_reference_returns_the_newest_cycle(
    conn: sqlite3.Connection,
) -> None:
    email1 = _seed_email(conn)
    _seed_shipment(conn, email_id=email1, cycle_number=1)
    email2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle2 = _seed_shipment(conn, email_id=email2, cycle_number=2)

    row = repo.find_latest_shipment_by_reference(conn, customer_id="acme", reference="SHP-5501")
    assert row is not None
    assert row["shipment_id"] == cycle2
    assert row["cycle_number"] == 2
    assert (
        repo.find_latest_shipment_by_reference(conn, customer_id="acme", reference="SHP-9999")
        is None
    )


def test_a_rerun_replaces_a_cross_check_result_rather_than_appending(
    conn: sqlite3.Connection,
) -> None:
    """R13.3: after a document is rerun the field must end with one current verdict, not two
    rows the screen would have to disambiguate."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    repo.upsert_cross_check_result(
        conn,
        shipment_id=shipment_id,
        field_name="consignee",
        result="inconsistent",
        reason="Invoice and BOL name different entities.",
        compared=[{"document_id": "d1", "value": "ACME Electronics"}],
    )
    repo.upsert_cross_check_result(
        conn,
        shipment_id=shipment_id,
        field_name="consignee",
        result="consistent",
        reason="Corrected invoice now matches the BOL.",
        compared=[{"document_id": "d2", "value": "ACME Electronics Pte. Ltd."}],
    )

    rows = repo.list_cross_check_results(conn, shipment_id)
    assert len(rows) == 1
    assert rows[0]["result"] == "consistent"
    assert rows[0]["reason"] == "Corrected invoice now matches the BOL."
    assert json.loads(rows[0]["compared"])[0]["document_id"] == "d2"


def test_record_operator_decision_leaves_the_system_verdict_alone(
    conn: sqlite3.Connection,
) -> None:
    """R7.6: the operator's call is stored beside the system's, never over it, so the audit
    trail shows both -- including a disagreement."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome="amendment_request",
        reasoning="Consignee differs between the invoice and the bill of lading.",
        discrepancy_count=2,
        uncertain_count=1,
    )
    before = repo.get_shipment(conn, shipment_id)

    repo.record_operator_decision(conn, shipment_id=shipment_id, decision="approved_anyway")

    after = repo.get_shipment(conn, shipment_id)
    assert after["operator_decision"] == "approved_anyway"
    assert after["operator_decided_at"]
    assert after["outcome"] == before["outcome"]
    assert after["reasoning"] == before["reasoning"]
    assert after["discrepancy_count"] == before["discrepancy_count"] == 2
    assert after["uncertain_count"] == before["uncertain_count"] == 1
    assert after["decided_at"] == before["decided_at"]


def test_set_review_state_stores_a_failure_reason(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    repo.set_review_state(
        conn,
        shipment_id=shipment_id,
        review_state="failed",
        failure_reason="The subject names a customer we have no rules for.",
    )

    row = repo.get_shipment(conn, shipment_id)
    assert row["review_state"] == "failed"
    assert row["failure_reason"] == "The subject names a customer we have no rules for."


def test_draft_versions_append_and_keep_the_generated_text(conn: sqlite3.Connection) -> None:
    """R6.6, R11.1: version 1 stays readable, which is what the "edited" markers and the reset
    button compare against."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    draft_id, _version_id = _seed_draft(conn, shipment_id)

    repo.add_draft_version(
        conn,
        draft_id=draft_id,
        recipient="ops@su.example",
        subject="Re: ACME-001 SHP-5501 documents attached",
        body="All documents verified. Thanks -- please proceed.",
        source="operator_edit",
    )

    versions = repo.list_draft_versions(conn, draft_id)
    assert [v["version_number"] for v in versions] == [1, 2]
    assert versions[0]["source"] == "generated"
    assert versions[0]["body"] == "All documents verified. No amendment needed."
    assert versions[-1]["source"] == "operator_edit"

    draft = repo.get_draft(conn, shipment_id)
    assert draft["kind"] == "approval"
    assert draft["render_error"] is None


def test_a_render_failure_still_gets_an_editable_draft(conn: sqlite3.Connection) -> None:
    """R6.5: a template that fails to render leaves an empty draft plus the reason, not a blank
    screen with no explanation."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)

    repo.create_draft(
        conn,
        shipment_id=shipment_id,
        kind="empty",
        render_error="No recipient on the source email.",
        recipient=None,
        subject=None,
        body=None,
    )

    draft = repo.get_draft(conn, shipment_id)
    assert draft["kind"] == "empty"
    assert draft["render_error"] == "No recipient on the source email."
    assert repo.list_draft_versions(conn, draft["id"])[0]["body"] is None
    assert repo.get_outbox(conn, shipment_id) is None


def test_list_shipments_shows_newest_first_and_every_state(conn: sqlite3.Connection) -> None:
    email1 = _seed_email(conn)
    _seed_shipment(conn, email_id=email1, reference="SHP-1", review_state="failed")
    email2 = _seed_email(conn, message_id="<m-2@su.example>")
    second = _seed_shipment(conn, email_id=email2, reference="SHP-2")

    rows = repo.list_shipments(conn)
    assert len(rows) == 2
    assert rows[0]["shipment_id"] == second
    assert {r["review_state"] for r in rows} == {"failed", "pending_review"}
    assert repo.get_shipment(conn, "no-such-shipment") is None


# --- v_pending_shipments ----------------------------------------------------------------------


def test_v_pending_shipments_columns(conn: sqlite3.Connection) -> None:
    assert _table_columns(conn, "v_pending_shipments") == V_PENDING_SHIPMENTS_COLUMNS


def test_v_pending_shipments_returns_a_seeded_shipment(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    document = _seed_document(conn, shipment_id=shipment_id)
    repo.add_shipment_document(
        conn,
        shipment_id=shipment_id,
        document_id=document.id,
        run_id=None,
        doc_type="commercial_invoice",
        origin="own_email",
        source_shipment_id=None,
        source_cycle=None,
    )
    repo.set_shipment_outcome(
        conn,
        shipment_id=shipment_id,
        outcome="human_review",
        reasoning="One field could not be read confidently.",
        discrepancy_count=0,
        uncertain_count=1,
    )

    rows = conn.execute(
        "SELECT * FROM v_pending_shipments WHERE review_state = 'pending_review'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["shipment_reference"] == "SHP-5501"
    assert row["customer_id"] == "acme"
    assert row["customer_name"] == "ACME Electronics Pte. Ltd."
    assert row["subject"] == "ACME-001 SHP-5501 documents attached"
    assert row["received_at"] == "2026-10-10T09:00:00+00:00"
    assert row["cycle_number"] == 1
    assert row["document_count"] == 1
    assert row["outcome"] == "human_review"
    assert row["discrepancy_count"] == 0
    assert row["uncertain_count"] == 1
    assert row["decided_at"]


def test_v_pending_shipments_carries_non_pending_states_too(conn: sqlite3.Connection) -> None:
    """R12.2: the view is deliberately unfiltered. Rows existing with no pending one is how the
    query layer answers "this customer has nothing pending"; no rows at all is how it answers
    "no such customer". Filtering inside the view would collapse those two into one answer."""
    email1 = _seed_email(conn)
    pending = _seed_shipment(conn, email_id=email1, reference="SHP-1")
    email2 = _seed_email(conn, message_id="<m-2@su.example>")
    sent = _seed_shipment(conn, email_id=email2, reference="SHP-2")
    repo.set_review_state(conn, shipment_id=sent, review_state="amendment_sent")

    states = {
        row["shipment_reference"]: row["review_state"]
        for row in conn.execute("SELECT * FROM v_pending_shipments").fetchall()
    }
    assert states == {"SHP-1": "pending_review", "SHP-2": "amendment_sent"}

    repo.set_review_state(conn, shipment_id=pending, review_state="reviewed_no_send")
    still_there = conn.execute("SELECT COUNT(*) AS n FROM v_pending_shipments").fetchone()
    assert still_there["n"] == 2
