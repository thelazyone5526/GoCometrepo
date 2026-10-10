"""Tests for the effective-set builder (part2-shipment-workflow design section 2, R5.2-R5.4).

Same scratch-`data_dir` fixture pattern as `test_store_part2.py`: shipments and documents are
seeded directly through the repository, `own_email` rows are materialised by the test itself
(a later task's ingestion step is what does this for real), and `build_effective_set` is the
only thing under test here -- it decides what, if anything, gets carried over, and persists
that decision.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.shipment.effective_set import SetEntry, build_effective_set
from app.store import repository as repo
from app.store.db import connect

# --- fixtures, same pattern as test_store_part2.py --------------------------------------------


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


# --- helpers ------------------------------------------------------------------------------------


def _seed_email(conn: sqlite3.Connection, *, message_id: str) -> str:
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


def _seed_shipment(
    conn: sqlite3.Connection,
    *,
    email_id: str,
    reference: str = "SHP-5501",
    cycle_number: int = 1,
    parent_shipment_id: str | None = None,
) -> str:
    return repo.create_shipment_for_email(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        reference=reference,
        email_id=email_id,
        cycle_number=cycle_number,
        parent_shipment_id=parent_shipment_id,
        review_state="pending_review",
    )


def _seed_document(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    filename: str,
    file_hash: str,
    doc_type: str | None,
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


def _own_email(
    conn: sqlite3.Connection, *, shipment_id: str, filename: str, file_hash: str, doc_type: str
) -> repo.DocumentRecord:
    """Seed one of this shipment's own documents, both the `documents` row and the
    `shipment_documents` row a real ingestion step would already have written before
    `build_effective_set` ever runs."""
    document = _seed_document(
        conn, shipment_id=shipment_id, filename=filename, file_hash=file_hash, doc_type=doc_type
    )
    repo.add_shipment_document(
        conn,
        shipment_id=shipment_id,
        document_id=document.id,
        run_id=None,
        doc_type=doc_type,
        origin="own_email",
        source_shipment_id=None,
        source_cycle=None,
    )
    return document


def _by_type(entries: list[SetEntry]) -> dict[str | None, SetEntry]:
    return {entry.doc_type: entry for entry in entries}


# --- tests --------------------------------------------------------------------------------------


def test_only_own_documents_returns_exactly_those(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn, message_id="<m-1@su.example>")
    shipment_id = _seed_shipment(conn, email_id=email_id)
    _own_email(
        conn,
        shipment_id=shipment_id,
        filename="invoice.pdf",
        file_hash="h-inv",
        doc_type="commercial_invoice",
    )
    _own_email(
        conn,
        shipment_id=shipment_id,
        filename="bol.pdf",
        file_hash="h-bol",
        doc_type="bill_of_lading",
    )
    _own_email(
        conn,
        shipment_id=shipment_id,
        filename="packing.pdf",
        file_hash="h-pack",
        doc_type="packing_list",
    )

    entries = build_effective_set(conn, shipment_id)

    assert len(entries) == 3
    assert all(entry.origin == "own_email" for entry in entries)
    assert {entry.doc_type for entry in entries} == {
        "commercial_invoice",
        "bill_of_lading",
        "packing_list",
    }


def test_cycle_2_carries_bol_and_packing_list_from_parent(conn: sqlite3.Connection) -> None:
    # Cycle 1: full shipment, all three types.
    email_1 = _seed_email(conn, message_id="<m-1@su.example>")
    cycle_1 = _seed_shipment(conn, email_id=email_1, cycle_number=1)
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="invoice.pdf",
        file_hash="h-inv-1",
        doc_type="commercial_invoice",
    )
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="bol.pdf",
        file_hash="h-bol-1",
        doc_type="bill_of_lading",
    )
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="packing.pdf",
        file_hash="h-pack-1",
        doc_type="packing_list",
    )

    # Cycle 2: only a corrected invoice.
    email_2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle_2 = _seed_shipment(conn, email_id=email_2, cycle_number=2, parent_shipment_id=cycle_1)
    _own_email(
        conn,
        shipment_id=cycle_2,
        filename="invoice-v2.pdf",
        file_hash="h-inv-2",
        doc_type="commercial_invoice",
    )

    entries = build_effective_set(conn, cycle_2)
    by_type = _by_type(entries)

    assert len(entries) == 3
    assert by_type["commercial_invoice"].origin == "own_email"
    assert by_type["commercial_invoice"].filename == "invoice-v2.pdf"

    assert by_type["bill_of_lading"].origin == "carried_over"
    assert by_type["bill_of_lading"].source_shipment_id == cycle_1
    assert by_type["bill_of_lading"].source_cycle == 1
    assert by_type["bill_of_lading"].filename == "bol.pdf"

    assert by_type["packing_list"].origin == "carried_over"
    assert by_type["packing_list"].source_shipment_id == cycle_1
    assert by_type["packing_list"].source_cycle == 1


def test_cycle_3_walks_back_through_multiple_ancestors_for_the_nearest_match(
    conn: sqlite3.Connection,
) -> None:
    """R5.2: "latest earlier document" means nearest, not oldest. Cycle 2 carries the BOL
    forward from cycle 1 without re-sending it; cycle 3 sends only a corrected packing list, so
    it must pick up the invoice from cycle 2 (nearest) and the BOL from cycle 1 (where it was
    last actually received), not re-walk past cycle 2 for the BOL."""
    email_1 = _seed_email(conn, message_id="<m-1@su.example>")
    cycle_1 = _seed_shipment(conn, email_id=email_1, cycle_number=1)
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="invoice-v1.pdf",
        file_hash="h-inv-1",
        doc_type="commercial_invoice",
    )
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="bol.pdf",
        file_hash="h-bol-1",
        doc_type="bill_of_lading",
    )

    email_2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle_2 = _seed_shipment(conn, email_id=email_2, cycle_number=2, parent_shipment_id=cycle_1)
    _own_email(
        conn,
        shipment_id=cycle_2,
        filename="invoice-v2.pdf",
        file_hash="h-inv-2",
        doc_type="commercial_invoice",
    )
    # cycle_2's own effective set carries the BOL forward too (what a real run would persist).
    build_effective_set(conn, cycle_2)

    email_3 = _seed_email(conn, message_id="<m-3@su.example>")
    cycle_3 = _seed_shipment(conn, email_id=email_3, cycle_number=3, parent_shipment_id=cycle_2)
    _own_email(
        conn,
        shipment_id=cycle_3,
        filename="packing.pdf",
        file_hash="h-pack-3",
        doc_type="packing_list",
    )

    entries = build_effective_set(conn, cycle_3)
    by_type = _by_type(entries)

    assert len(entries) == 3
    assert by_type["packing_list"].origin == "own_email"

    # Nearest ancestor (cycle 2) actually owns the corrected invoice.
    assert by_type["commercial_invoice"].origin == "carried_over"
    assert by_type["commercial_invoice"].source_shipment_id == cycle_2
    assert by_type["commercial_invoice"].source_cycle == 2
    assert by_type["commercial_invoice"].filename == "invoice-v2.pdf"

    # The BOL was never cycle_2's own document, only carried into it from cycle 1 -- the
    # nearest shipment that actually *owns* a BOL is cycle 1, not cycle 2.
    assert by_type["bill_of_lading"].origin == "carried_over"
    assert by_type["bill_of_lading"].source_shipment_id == cycle_1
    assert by_type["bill_of_lading"].source_cycle == 1
    assert by_type["bill_of_lading"].filename == "bol.pdf"


def test_own_document_is_never_duplicated_as_carried_over(conn: sqlite3.Connection) -> None:
    email_1 = _seed_email(conn, message_id="<m-1@su.example>")
    cycle_1 = _seed_shipment(conn, email_id=email_1, cycle_number=1)
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="bol-v1.pdf",
        file_hash="h-bol-1",
        doc_type="bill_of_lading",
    )

    email_2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle_2 = _seed_shipment(conn, email_id=email_2, cycle_number=2, parent_shipment_id=cycle_1)
    # Cycle 2 resends its own corrected BOL, even though the ancestor also has one.
    _own_email(
        conn,
        shipment_id=cycle_2,
        filename="bol-v2.pdf",
        file_hash="h-bol-2",
        doc_type="bill_of_lading",
    )

    entries = build_effective_set(conn, cycle_2)

    bol_entries = [entry for entry in entries if entry.doc_type == "bill_of_lading"]
    assert len(bol_entries) == 1
    assert bol_entries[0].origin == "own_email"
    assert bol_entries[0].filename == "bol-v2.pdf"


def test_building_twice_is_idempotent(conn: sqlite3.Connection) -> None:
    email_1 = _seed_email(conn, message_id="<m-1@su.example>")
    cycle_1 = _seed_shipment(conn, email_id=email_1, cycle_number=1)
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="invoice.pdf",
        file_hash="h-inv-1",
        doc_type="commercial_invoice",
    )
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="bol.pdf",
        file_hash="h-bol-1",
        doc_type="bill_of_lading",
    )

    email_2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle_2 = _seed_shipment(conn, email_id=email_2, cycle_number=2, parent_shipment_id=cycle_1)
    _own_email(
        conn,
        shipment_id=cycle_2,
        filename="invoice-v2.pdf",
        file_hash="h-inv-2",
        doc_type="commercial_invoice",
    )

    first = build_effective_set(conn, cycle_2)
    second = build_effective_set(conn, cycle_2)

    assert first == second
    rows = repo.list_shipment_documents(conn, cycle_2)
    assert len(rows) == len(first)


def test_doc_type_never_carried_is_simply_absent(conn: sqlite3.Connection) -> None:
    email_1 = _seed_email(conn, message_id="<m-1@su.example>")
    cycle_1 = _seed_shipment(conn, email_id=email_1, cycle_number=1)
    _own_email(
        conn,
        shipment_id=cycle_1,
        filename="invoice.pdf",
        file_hash="h-inv-1",
        doc_type="commercial_invoice",
    )

    email_2 = _seed_email(conn, message_id="<m-2@su.example>")
    cycle_2 = _seed_shipment(conn, email_id=email_2, cycle_number=2, parent_shipment_id=cycle_1)
    _own_email(
        conn,
        shipment_id=cycle_2,
        filename="invoice-v2.pdf",
        file_hash="h-inv-2",
        doc_type="commercial_invoice",
    )

    entries = build_effective_set(conn, cycle_2)

    assert {entry.doc_type for entry in entries} == {"commercial_invoice"}
    assert all(entry.doc_type != "packing_list" for entry in entries)


def test_first_cycle_with_gaps_has_no_carried_over_entries(conn: sqlite3.Connection) -> None:
    email_id = _seed_email(conn, message_id="<m-1@su.example>")
    shipment_id = _seed_shipment(conn, email_id=email_id, cycle_number=1)
    _own_email(
        conn,
        shipment_id=shipment_id,
        filename="invoice.pdf",
        file_hash="h-inv",
        doc_type="commercial_invoice",
    )

    entries = build_effective_set(conn, shipment_id)

    assert len(entries) == 1
    assert entries[0].origin == "own_email"
    assert all(entry.origin != "carried_over" for entry in entries)
