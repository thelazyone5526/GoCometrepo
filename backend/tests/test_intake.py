"""Tests for `inbox/intake.py::accept_attachment` (requirements 2.1, 2.2).

Same scratch-`data_dir` fixture pattern as `test_store_part2.py` and `test_effective_set.py`:
a shipment and its email are seeded directly through the repository, and `accept_attachment` is
the only thing under test.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.inbox.intake import AttachmentOutcome, accept_attachment
from app.store import repository as repo
from app.store.db import connect

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
GRID = SAMPLES_DIR / "grid"
GOOD_PDF = GRID / "V1-C0.pdf"


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
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


class _Attachment:
    """A stand-in for `app.inbox.eml.Attachment` -- same three fields, so intake needs
    nothing more than that shape."""

    def __init__(self, filename: str, data: bytes, content_type: str = "application/pdf"):
        self.filename = filename
        self.data = data
        self.content_type = content_type


def _seed_email(conn: sqlite3.Connection, *, message_id: str = "<m-1@su.example>") -> str:
    return repo.record_email(
        conn,
        message_id=message_id,
        eml_path=f"inbox/{message_id}.eml",
        sender="ops@su.example",
        subject="ACME-001 SHP-5501 documents attached",
        received_at="2026-10-10T09:00:00+00:00",
        attachment_count=1,
        attachment_filenames=["invoice.pdf"],
    )


def _seed_shipment(conn: sqlite3.Connection, *, email_id: str) -> str:
    return repo.create_shipment_for_email(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        reference="SHP-5501",
        email_id=email_id,
        cycle_number=1,
        parent_shipment_id=None,
        review_state="pending_review",
    )


# --- the good case --------------------------------------------------------------------------


def test_a_good_pdf_is_accepted_and_a_document_is_created(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    attachment = _Attachment("invoice.pdf", GOOD_PDF.read_bytes())

    outcome = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=attachment,
    )

    assert outcome == AttachmentOutcome("accepted", outcome.document_id, None)
    assert outcome.document_id is not None

    row = conn.execute("SELECT * FROM documents WHERE id = ?", (outcome.document_id,)).fetchone()
    assert row is not None
    assert row["shipment_id"] == shipment_id
    assert row["filename"] == "invoice.pdf"
    assert row["doc_type"] is None  # filled in later, after the Part 1 pipeline runs

    stored_path = Path(row["stored_path"])
    assert stored_path.exists()
    assert stored_path.read_bytes() == GOOD_PDF.read_bytes()


# --- duplicates ------------------------------------------------------------------------------


def test_a_byte_identical_duplicate_reuses_the_existing_document(
    conn: sqlite3.Connection,
) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    data = GOOD_PDF.read_bytes()

    first = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("invoice.pdf", data),
    )

    documents_before = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    stored_path_before = conn.execute(
        "SELECT stored_path FROM documents WHERE id = ?", (first.document_id,)
    ).fetchone()["stored_path"]
    mtime_before = Path(stored_path_before).stat().st_mtime

    second = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("invoice-resend.pdf", data),
    )

    assert second.status == "duplicate"
    assert second.document_id == first.document_id
    assert second.reason is not None

    documents_after = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert documents_after == documents_before  # no second document row

    mtime_after = Path(stored_path_before).stat().st_mtime
    assert mtime_after == mtime_before  # the file was not rewritten


def test_a_duplicate_outside_the_chain_is_not_treated_as_a_duplicate(
    conn: sqlite3.Connection,
) -> None:
    """`shipment_chain_ids` scopes the dedup check: a byte-identical file belonging to an
    unrelated shipment is a brand new document here, not a reuse."""
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    other_email_id = _seed_email(conn, message_id="<m-2@su.example>")
    other_shipment_id = _seed_shipment(conn, email_id=other_email_id)
    data = GOOD_PDF.read_bytes()

    accept_attachment(
        conn,
        email_id=other_email_id,
        shipment_id=other_shipment_id,
        shipment_chain_ids=[other_shipment_id],
        attachment=_Attachment("invoice.pdf", data),
    )

    outcome = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("invoice.pdf", data),
    )

    assert outcome.status == "accepted"


# --- rejections ------------------------------------------------------------------------------


def test_a_file_failing_check_upload_is_rejected_with_a_reason(
    conn: sqlite3.Connection,
) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    attachment = _Attachment("notes.txt", b"not a pdf, png or jpg", content_type="text/plain")

    outcome = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=attachment,
    )

    assert outcome.status == "rejected"
    assert outcome.document_id is None
    assert outcome.reason

    documents = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
    assert documents == 0


def test_a_too_large_file_is_rejected(conn: sqlite3.Connection) -> None:
    from app.ingest.files import MAX_UPLOAD_BYTES

    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    oversized = b"%PDF-" + b"0" * (MAX_UPLOAD_BYTES + 1)
    attachment = _Attachment("huge.pdf", oversized)

    outcome = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=attachment,
    )

    assert outcome.status == "rejected"
    assert "exceeds" in outcome.reason or "large" in outcome.reason.lower()


# --- every outcome is recorded ----------------------------------------------------------------


def test_every_outcome_is_recorded_in_shipment_attachments(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn)
    shipment_id = _seed_shipment(conn, email_id=email_id)
    data = GOOD_PDF.read_bytes()

    accepted = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("invoice.pdf", data),
    )
    duplicate = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("invoice-again.pdf", data),
    )
    rejected = accept_attachment(
        conn,
        email_id=email_id,
        shipment_id=shipment_id,
        shipment_chain_ids=[shipment_id],
        attachment=_Attachment("notes.txt", b"plain text"),
    )

    rows = repo.list_attachments(conn, email_id)
    by_filename = {row["filename"]: row for row in rows}

    assert by_filename["invoice.pdf"]["status"] == "accepted"
    assert by_filename["invoice.pdf"]["document_id"] == accepted.document_id

    assert by_filename["invoice-again.pdf"]["status"] == "duplicate"
    assert by_filename["invoice-again.pdf"]["document_id"] == duplicate.document_id

    assert by_filename["notes.txt"]["status"] == "rejected"
    assert by_filename["notes.txt"]["document_id"] is None
    assert by_filename["notes.txt"]["reason"] == rejected.reason
