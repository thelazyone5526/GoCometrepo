"""Tests for the Part 2 shipment routes (`app.api.shipment_routes`, requirements 5.4, 7.1,
7.2, 7.4, 8-11, 13.3, 13.5).

Shipments are seeded directly through the repository -- same pattern as
`test_effective_set.py` and `test_send.py` -- rather than by running the real pipeline against
sample PDFs: the grounding/retry behaviour in `agents/extract.py` depends on the actual text on
a page, so fabricating field values that don't match what `test_watcher.py`'s samples actually
contain makes the number of LLM calls unpredictable. Seeding `field_results` rows directly gives
exact control over verdicts, which is all these routes need to be exercised against -- the
pipeline itself is already covered by `test_graph.py`/`test_watcher.py`/`test_api_runs.py`.

`TestClient` (`test_api_runs.py`'s pattern) exercises the routes exactly as the real server
would run them, against a scratch `data_dir`/`app.db` the seeding helpers below write into
directly (same connection file, opened fresh by each route).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.store import repository as repo
from app.store.db import connect

# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    from app.config import settings

    original = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path / "data")
    try:
        yield tmp_path / "data"
    finally:
        object.__setattr__(settings, "data_dir", original)


@pytest.fixture
def conn(data_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(data_dir=data_dir)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(data_dir: Path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --- seed helpers ----------------------------------------------------------------------------


def _seed_email(conn: sqlite3.Connection, *, message_id: str, sender: str, subject: str) -> str:
    return repo.record_email(
        conn,
        message_id=message_id,
        eml_path=f"inbox/{message_id}.eml",
        sender=sender,
        subject=subject,
        received_at="2026-10-10T09:00:00+00:00",
        attachment_count=1,
        attachment_filenames=["invoice.pdf"],
    )


def _seed_shipment(
    conn: sqlite3.Connection,
    *,
    email_id: str,
    reference: str = "SHP-5501",
    review_state: str = "pending_review",
) -> str:
    return repo.create_shipment_for_email(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        reference=reference,
        email_id=email_id,
        cycle_number=1,
        parent_shipment_id=None,
        review_state=review_state,
    )


def _seed_document_with_run(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    email_id: str,
    filename: str,
    file_hash: str,
    doc_type: str,
    fields: list[dict],
) -> tuple[str, str]:
    """One document, its run, its field_results, and its `shipment_documents` row, as if the
    Part 1 pipeline had already run on it. Returns `(document_id, run_id)`."""
    document = repo.create_document_for_shipment(
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
    repo.record_attachment(
        conn,
        email_id=email_id,
        filename=filename,
        file_hash=file_hash,
        status="accepted",
        reason=None,
        document_id=document.id,
    )
    run_id = repo.new_id()
    repo.create_run(conn, run_id=run_id, document_id=document.id)
    for f in fields:
        repo.insert_field_result(
            conn,
            run_id=run_id,
            field_name=f["field_name"],
            value=f.get("value"),
            source_text=f.get("source_text"),
            page=f.get("page", 1),
            box=None,
            model_rating=f.get("model_rating", 0.95),
            grounding=f.get("grounding", "exact"),
            ocr_reading=None,
            format_ok=f.get("format_ok", True),
            extraction_confidence=f.get("extraction_confidence", 0.95),
            verdict=f["verdict"],
            found=f.get("found"),
            expected=f.get("expected"),
            rule_id=f.get("rule_id"),
            verdict_reason=f.get("verdict_reason"),
            verdict_confidence=f.get("verdict_confidence", 0.95),
        )
    repo.complete_run(
        conn,
        run_id=run_id,
        outcome="auto_approved",
        reasoning="every field matched",
        decision_source="llm",
        override_reason=None,
        amendment_draft=None,
        llm_calls=3,
        fallback_used=False,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
        latency_ms=10.0,
    )
    repo.update_document_doc_type(conn, document_id=document.id, doc_type=doc_type)
    repo.add_shipment_document(
        conn,
        shipment_id=shipment_id,
        document_id=document.id,
        run_id=run_id,
        doc_type=doc_type,
        origin="own_email",
        source_shipment_id=None,
        source_cycle=None,
    )
    return document.id, run_id


_CLEAN_INVOICE_FIELDS = [
    {"field_name": "consignee", "value": "ACME Electronics Pte. Ltd.", "verdict": "match"},
    {"field_name": "hs_code", "value": "847130", "verdict": "match"},
    {"field_name": "gross_weight", "value": "862.40 KG", "verdict": "match"},
    {"field_name": "invoice_number", "value": "INV-2026-00417", "verdict": "match"},
]

_CLEAN_BOL_FIELDS = [
    {"field_name": "consignee", "value": "ACME Electronics Pte. Ltd.", "verdict": "match"},
    {"field_name": "hs_code", "value": "847130", "verdict": "match"},
    {"field_name": "gross_weight", "value": "862.40 KG", "verdict": "match"},
]

_CLEAN_PACKING_FIELDS = [
    {"field_name": "consignee", "value": "ACME Electronics Pte. Ltd.", "verdict": "match"},
    {"field_name": "hs_code", "value": "847130", "verdict": "match"},
    {"field_name": "gross_weight", "value": "862.40 KG", "verdict": "match"},
    {"field_name": "invoice_number", "value": "INV-2026-00417", "verdict": "match"},
]


def _seed_clean_shipment(conn: sqlite3.Connection) -> str:
    """Three documents, every field matching, every cross-check consistent -> auto_approve."""
    email_id = _seed_email(
        conn,
        message_id="<clean@su.example>",
        sender="ops@su.example",
        subject="SHP-5501 acme documents",
    )
    shipment_id = _seed_shipment(conn, email_id=email_id)
    _seed_document_with_run(
        conn, shipment_id=shipment_id, email_id=email_id, filename="invoice.pdf",
        file_hash="h-inv", doc_type="commercial_invoice", fields=_CLEAN_INVOICE_FIELDS,
    )
    _seed_document_with_run(
        conn, shipment_id=shipment_id, email_id=email_id, filename="bol.pdf",
        file_hash="h-bol", doc_type="bill_of_lading", fields=_CLEAN_BOL_FIELDS,
    )
    _seed_document_with_run(
        conn, shipment_id=shipment_id, email_id=email_id, filename="packing.pdf",
        file_hash="h-pack", doc_type="packing_list", fields=_CLEAN_PACKING_FIELDS,
    )
    return shipment_id


def _seed_messy_shipment(conn: sqlite3.Connection) -> str:
    """A consignee mismatch between the invoice and the bill of lading -> amendment_request."""
    email_id = _seed_email(
        conn,
        message_id="<messy@su.example>",
        sender="ops@su.example",
        subject="SHP-5502 acme documents",
    )
    shipment_id = _seed_shipment(conn, email_id=email_id)
    invoice_fields = [
        {"field_name": "consignee", "value": "ACME Electronics Pte. Ltd.", "verdict": "match"},
        {"field_name": "hs_code", "value": "847130", "verdict": "match"},
        {"field_name": "gross_weight", "value": "862.40 KG", "verdict": "match"},
        {"field_name": "invoice_number", "value": "INV-2026-00417", "verdict": "match"},
    ]
    bol_fields = [
        {
            "field_name": "consignee", "value": "Zenith Trading Holdings", "verdict": "mismatch",
            "found": "Zenith Trading Holdings", "expected": "ACME Electronics Pte. Ltd.",
            "rule_id": "R-CON-1", "verdict_reason": "not the registered consignee",
        },
        {"field_name": "hs_code", "value": "847130", "verdict": "match"},
        {"field_name": "gross_weight", "value": "862.40 KG", "verdict": "match"},
    ]
    _seed_document_with_run(
        conn, shipment_id=shipment_id, email_id=email_id, filename="invoice.pdf",
        file_hash="h-inv-2", doc_type="commercial_invoice", fields=invoice_fields,
    )
    _seed_document_with_run(
        conn, shipment_id=shipment_id, email_id=email_id, filename="bol.pdf",
        file_hash="h-bol-2", doc_type="bill_of_lading", fields=bol_fields,
    )
    return shipment_id


def _seed_failed_shipment(conn: sqlite3.Connection) -> str:
    email_id = _seed_email(
        conn,
        message_id="<unknown@su.example>",
        sender=None,
        subject="no known customer here",
    )
    return repo.create_shipment_for_email(
        conn,
        customer_id="unknown",
        customer_name="Unknown",
        reference=None,
        email_id=email_id,
        cycle_number=1,
        parent_shipment_id=None,
        review_state="failed",
        failure_reason="unknown customer",
    )


# --- GET /api/shipments -------------------------------------------------------------------


def test_get_shipments_lists_the_clean_shipment_as_auto_approve(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()

    response = client.get("/api/shipments")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["shipment_id"] == shipment_id
    assert row["outcome"] == "auto_approve"
    assert row["documents_done"] == 3
    assert row["documents_total"] == 3
    assert row["discrepancy_count"] == 0
    assert row["uncertain_count"] == 0


def test_get_shipments_lists_the_messy_shipment_as_amendment_request(client, conn) -> None:
    _seed_messy_shipment(conn)
    conn.close()

    response = client.get("/api/shipments")
    body = response.json()
    assert len(body) == 1
    assert body[0]["outcome"] == "amendment_request"
    assert body[0]["discrepancy_count"] > 0


def test_failed_shipments_still_appear_in_the_list(client, conn) -> None:
    _seed_failed_shipment(conn)
    conn.close()

    response = client.get("/api/shipments")
    body = response.json()
    assert len(body) == 1
    assert body[0]["review_state"] == "failed"
    assert body[0]["outcome"] is None


# --- GET /api/shipments/{id} --------------------------------------------------------------


def test_get_shipment_detail_includes_every_field_and_cross_check(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()

    response = client.get(f"/api/shipments/{shipment_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "auto_approve"
    assert len(body["documents"]) == 3
    assert len(body["field_results"]) > 0
    assert {cc["field_name"] for cc in body["cross_checks"]} == {
        "consignee", "hs_code", "gross_weight", "invoice_number",
    }
    assert all(cc["result"] == "consistent" for cc in body["cross_checks"])
    assert body["uncertain_items"] == []


def test_get_shipment_detail_on_the_messy_shipment_shows_the_consignee_discrepancy(
    client, conn
) -> None:
    shipment_id = _seed_messy_shipment(conn)
    conn.close()

    response = client.get(f"/api/shipments/{shipment_id}")
    body = response.json()
    assert body["outcome"] == "amendment_request"
    by_field = {cc["field_name"]: cc for cc in body["cross_checks"]}
    assert by_field["consignee"]["result"] == "inconsistent"
    values = {row["value"] for row in by_field["consignee"]["compared"]}
    assert "Zenith Trading Holdings" in values


def test_get_shipment_detail_404_for_unknown_shipment(client, data_dir) -> None:
    response = client.get("/api/shipments/does-not-exist")
    assert response.status_code == 404


def test_shipment_still_processing_has_no_outcome_yet(client, conn) -> None:
    """`ensure_shipment_decided` must not decide a shipment while a document is still
    `processing` (R4.1: "when cross-document validation is complete")."""
    email_id = _seed_email(
        conn, message_id="<pending@su.example>", sender="ops@su.example", subject="SHP-5503"
    )
    shipment_id = _seed_shipment(conn, email_id=email_id)
    document = repo.create_document_for_shipment(
        conn, shipment_id=shipment_id, filename="invoice.pdf", file_hash="h-pending",
        mime_type="application/pdf", page_count=1, has_text_layer=True,
        stored_path=Path("uploads/h-pending/original.pdf"), doc_type=None,
    )
    run_id = repo.new_id()
    repo.create_run(conn, run_id=run_id, document_id=document.id)  # left 'processing'
    repo.add_shipment_document(
        conn, shipment_id=shipment_id, document_id=document.id, run_id=run_id,
        doc_type=None, origin="own_email", source_shipment_id=None, source_cycle=None,
    )
    conn.close()

    response = client.get(f"/api/shipments/{shipment_id}")
    body = response.json()
    assert body["outcome"] is None
    assert response.status_code == 200

    list_response = client.get("/api/shipments")
    list_row = list_response.json()[0]
    assert list_row["documents_done"] == 0
    assert list_row["documents_total"] == 1


# --- draft routes ---------------------------------------------------------------------------


def test_get_draft_for_auto_approve_shipment_is_an_approval(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()

    client.get(f"/api/shipments/{shipment_id}")  # triggers ensure_shipment_decided
    response = client.get(f"/api/shipments/{shipment_id}/draft")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "approval"
    assert body["sent"] is False
    assert body["is_edited"] is False
    assert body["recipient"] == "ops@su.example"


def test_get_draft_for_amendment_lists_the_discrepancy(client, conn) -> None:
    shipment_id = _seed_messy_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")

    response = client.get(f"/api/shipments/{shipment_id}/draft")
    body = response.json()
    assert body["kind"] == "amendment"
    assert "consignee" in body["body"]
    assert "Zenith Trading Holdings" in body["body"]


def test_get_draft_404_before_the_shipment_is_decided(client, conn) -> None:
    email_id = _seed_email(
        conn, message_id="<nodecision@su.example>", sender="ops@su.example", subject="SHP-9"
    )
    shipment_id = _seed_shipment(conn, email_id=email_id)
    conn.close()

    response = client.get(f"/api/shipments/{shipment_id}/draft")
    assert response.status_code == 404


def test_put_draft_stores_an_edit_and_marks_it_edited(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")

    response = client.put(
        f"/api/shipments/{shipment_id}/draft",
        json={"recipient": "ops@su.example", "subject": "edited subject", "body": "edited body"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_edited"] is True
    assert body["body"] == "edited body"
    assert body["version_number"] == 2

    # a second GET must show the edited version, not the regenerated one
    get_again = client.get(f"/api/shipments/{shipment_id}/draft")
    assert get_again.json()["body"] == "edited body"


def test_put_draft_rejected_once_sent(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")
    client.post(f"/api/shipments/{shipment_id}/send", json={"confirmed": True})

    response = client.put(
        f"/api/shipments/{shipment_id}/draft",
        json={"recipient": "ops@su.example", "body": "trying to edit after send"},
    )
    assert response.status_code == 409


def test_put_draft_404_for_unknown_shipment(client, data_dir) -> None:
    response = client.put(
        "/api/shipments/does-not-exist/draft",
        json={"recipient": "a@b.com", "body": "x"},
    )
    assert response.status_code == 404


# --- send ------------------------------------------------------------------------------------


def test_send_approval_without_issues_needs_no_confirmation(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")

    response = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert response.status_code == 200
    assert response.json()["review_state"] == "approved_sent"


def test_second_send_is_rejected(client, conn) -> None:
    shipment_id = _seed_clean_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")
    first = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert first.status_code == 200

    second = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert second.status_code == 409


def test_send_amendment_sets_amendment_sent(client, conn) -> None:
    shipment_id = _seed_messy_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")

    response = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert response.status_code == 200
    assert response.json()["review_state"] == "amendment_sent"


def test_send_404_for_unknown_shipment(client, data_dir) -> None:
    response = client.post("/api/shipments/does-not-exist/send", json={})
    assert response.status_code == 404


def test_reviewed_without_send_sets_review_state_and_sends_nothing(client, conn) -> None:
    shipment_id = _seed_messy_shipment(conn)
    conn.close()
    client.get(f"/api/shipments/{shipment_id}")

    response = client.post(f"/api/shipments/{shipment_id}/reviewed")
    assert response.status_code == 200
    assert response.json()["review_state"] == "reviewed_no_send"

    draft_response = client.get(f"/api/shipments/{shipment_id}/draft")
    assert draft_response.json()["sent"] is False
