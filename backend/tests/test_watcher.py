"""Tests for the inbox trigger's watcher (`inbox/watcher.py::scan_inbox`), against the real
sample `.eml` files under `samples/inbox/` and a fake LLM transport so no network call is ever
made (requirements 1.1-1.6, 2.1-2.3, 5.1, 13.6).

Same `fake_transport` / `data_dir` fixture pattern as `tests/test_graph.py`: `settings.data_dir`
is pointed at `tmp_path`, and `app.graph.nodes.transport_factory` is swapped for a scripted
`FakeTransport`. The sample `.eml` files are copied into a scratch inbox directory rather than
scanned in place, so nothing here ever touches `samples/inbox/` on disk.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.route import RouteDecision
from app.agents.schema import ExtractionResult, FieldExtraction
from app.graph import nodes as graph_nodes
from app.inbox.watcher import scan_inbox
from app.ingest.files import check_upload
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse
from app.store import repository as repo
from app.store.db import connect

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
SAMPLE_INBOX = SAMPLES_DIR / "inbox"
GRID = SAMPLES_DIR / "grid"
PRIMARY = "gemini-3.8-flash"

_ALL_FIELD_NAMES = (
    "consignee",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "goods_description",
    "gross_weight",
    "invoice_number",
)


# --- fixtures, same pattern as test_graph.py ----------------------------------------------


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTransport]:
    from app.config import settings

    originals = (settings.gemini_model, settings.gemini_fallback_model)
    object.__setattr__(settings, "gemini_model", PRIMARY)
    object.__setattr__(settings, "gemini_fallback_model", "gemini-3.5-flash-lite")
    transport = FakeTransport()
    monkeypatch.setattr(graph_nodes, "transport_factory", lambda: transport)
    try:
        yield transport
    finally:
        object.__setattr__(settings, "gemini_model", originals[0])
        object.__setattr__(settings, "gemini_fallback_model", originals[1])


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
def checkpointer(data_dir: Path) -> Iterator[SqliteSaver]:
    ckpt_conn = sqlite3.connect(data_dir / "checkpoints.db", check_same_thread=False)
    try:
        yield SqliteSaver(ckpt_conn)
    finally:
        ckpt_conn.close()


@pytest.fixture
def inbox_dir(tmp_path: Path) -> Path:
    """A scratch inbox, so tests never scan (or risk writing recovered state about)
    `samples/inbox/` directly."""
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def _copy_sample(name: str, inbox_dir: Path) -> Path:
    destination = inbox_dir / name
    shutil.copyfile(SAMPLE_INBOX / name, destination)
    return destination


# --- fake LLM responses -----------------------------------------------------------------------


def _empty_extraction_response(document_type: str) -> RawResponse:
    """Every field null: grounding/verdicts don't matter for these tests, only that
    `document_type` round-trips through the pipeline into `documents.doc_type`."""
    fields = {
        name: FieldExtraction(value=None, source_text=None, page=None, self_rating=0.0)
        for name in _ALL_FIELD_NAMES
    }
    result = ExtractionResult(document_type=document_type, **fields)
    return RawResponse(
        text=result.model_dump_json(), input_tokens=100, output_tokens=20, thinking_tokens=0
    )


def _route_response(outcome: str = "human_review") -> RawResponse:
    decision = RouteDecision(
        outcome=outcome, reasoning="Needs a person to look at it.", cited_fields=[],
        amendment_draft=None,
    )
    return RawResponse(
        text=decision.model_dump_json(), input_tokens=150, output_tokens=40, thinking_tokens=0
    )


def _queue_one_document_run(fake_transport: FakeTransport, document_type: str) -> None:
    """Every accepted attachment costs exactly an extract call and a route call here: every
    field is null, so `validate()` never has a judgement to batch (confidence 0.0 makes every
    expected field 'uncertain' before any rule is even consulted)."""
    fake_transport.queue(PRIMARY, _empty_extraction_response(document_type))
    fake_transport.queue(PRIMARY, _route_response())


# --- the clean shipment ------------------------------------------------------------------------


def test_scanning_the_clean_shipment_creates_one_shipment_with_three_documents(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    _copy_sample("01-clean-shipment.eml", inbox_dir)
    # Attachment order in the sample: invoice, bill of lading, packing list.
    _queue_one_document_run(fake_transport, "commercial_invoice")
    _queue_one_document_run(fake_transport, "bill_of_lading")
    _queue_one_document_run(fake_transport, "packing_list")

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)

    assert len(touched) == 1
    shipment_id = touched[0]
    shipment = repo.get_shipment(conn, shipment_id)
    assert shipment["review_state"] == "pending_review"
    assert shipment["customer_id"] == "acme"
    assert shipment["reference"] == "SHP-1042"

    documents = repo.list_shipment_documents(conn, shipment_id)
    assert len(documents) == 3
    assert {d["status"] for d in repo.list_attachments(conn, shipment["email_id"])} == {
        "accepted"
    }
    doc_types = {d["doc_type"] for d in documents}
    assert doc_types == {"commercial_invoice", "bill_of_lading", "packing_list"}


# --- the two failure cases ----------------------------------------------------------------


def test_scanning_the_unknown_customer_email_fails_with_no_processing(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    _copy_sample("03-unknown-customer.eml", inbox_dir)

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)

    assert len(touched) == 1
    shipment = repo.get_shipment(conn, touched[0])
    assert shipment["review_state"] == "failed"
    assert "unknown customer" in shipment["failure_reason"]

    # requirement 1.5: no attachment of this email was even looked at.
    assert repo.list_attachments(conn, shipment["email_id"]) == []
    assert repo.list_shipment_documents(conn, touched[0]) == []
    assert len(fake_transport.calls) == 0  # the pipeline never ran


def test_scanning_the_no_attachments_email_fails_with_a_reason(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    _copy_sample("04-no-attachments.eml", inbox_dir)

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)

    assert len(touched) == 1
    shipment = repo.get_shipment(conn, touched[0])
    assert shipment["review_state"] == "failed"
    assert "no attachments" in shipment["failure_reason"].lower()
    assert repo.list_shipment_documents(conn, touched[0]) == []
    assert len(fake_transport.calls) == 0


# --- the restart guarantee -----------------------------------------------------------------


def test_scanning_the_same_inbox_twice_creates_no_duplicates(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    _copy_sample("03-unknown-customer.eml", inbox_dir)
    _copy_sample("04-no-attachments.eml", inbox_dir)

    first_pass = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(first_pass) == 2

    shipments_after_first = repo.list_shipments(conn, limit=50)
    assert len(shipments_after_first) == 2

    second_pass = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert second_pass == []  # requirement 1.3: nothing left to process

    shipments_after_second = repo.list_shipments(conn, limit=50)
    assert len(shipments_after_second) == 2


# --- a resubmission ---------------------------------------------------------------------------


def _build_resubmission_eml(*, reference: str, message_id: str) -> bytes:
    """A second email for the same `SHP-` reference as the clean shipment, carrying one
    attachment, built inline with the stdlib `email` package (same tool `app.inbox.eml`
    itself reads, so this test says exactly what it is asserting about rather than depending
    on a sample generator)."""
    msg = EmailMessage()
    msg["From"] = "docs@brightpath-computing.example"
    msg["To"] = "shipments@acme-logistics.example"
    msg["Subject"] = f"{reference} acme - corrected invoice"
    msg["Message-ID"] = message_id
    msg.set_content("Please find the corrected invoice attached.")
    # E1-C0.pdf, not V1-C0.pdf: the sample `.eml`'s own commercial invoice happens to be
    # byte-identical to V1-C0.pdf (both come from the same ground truth), which would make
    # this attachment a legitimate duplicate rather than a genuinely new document -- exactly
    # the wrong fixture for a test about cycle 2 having its *own* new invoice.
    msg.add_attachment(
        (GRID / "E1-C0.pdf").read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="invoice-v2.pdf",
    )
    return msg.as_bytes()


def test_a_resubmission_links_to_the_earlier_cycle(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    _copy_sample("01-clean-shipment.eml", inbox_dir)
    _queue_one_document_run(fake_transport, "commercial_invoice")
    _queue_one_document_run(fake_transport, "bill_of_lading")
    _queue_one_document_run(fake_transport, "packing_list")

    first_touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(first_touched) == 1
    cycle_1_id = first_touched[0]
    cycle_1 = repo.get_shipment(conn, cycle_1_id)
    assert cycle_1["cycle_number"] == 1

    resubmission_path = inbox_dir / "05-resubmission.eml"
    resubmission_path.write_bytes(
        _build_resubmission_eml(reference="SHP-1042", message_id="<resub-1@brightpath.example>")
    )
    _queue_one_document_run(fake_transport, "commercial_invoice")

    second_touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(second_touched) == 1
    cycle_2_id = second_touched[0]
    assert cycle_2_id != cycle_1_id

    cycle_2 = repo.get_shipment(conn, cycle_2_id)
    assert cycle_2["cycle_number"] == 2
    assert cycle_2["parent_shipment_id"] == cycle_1_id
    assert cycle_2["reference"] == "SHP-1042"

    # The effective set carries the BOL and packing list forward from cycle 1 (R5.2-R5.3).
    documents = repo.list_shipment_documents(conn, cycle_2_id)
    assert len(documents) == 3
    origins = {d["doc_type"]: d["origin"] for d in documents}
    assert origins["commercial_invoice"] == "own_email"
    assert origins["bill_of_lading"] == "carried_over"
    assert origins["packing_list"] == "carried_over"


# --- scan_inbox's return value --------------------------------------------------------------


def test_scan_inbox_returns_touched_shipment_ids(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    assert scan_inbox(conn, checkpointer, inbox_dir=inbox_dir) == []  # nothing to scan yet

    _copy_sample("04-no-attachments.eml", inbox_dir)
    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(touched) == 1
    assert all(isinstance(sid, str) and sid for sid in touched)

    assert scan_inbox(conn, checkpointer, inbox_dir=inbox_dir) == []


def test_scan_inbox_on_a_missing_inbox_dir_returns_empty(
    conn: sqlite3.Connection, checkpointer: SqliteSaver, tmp_path: Path,
) -> None:
    assert scan_inbox(conn, checkpointer, inbox_dir=tmp_path / "does-not-exist") == []


# --- sanity: check_upload accepts the real sample grid PDF used above -------------------------


def test_v1_c0_pdf_is_a_valid_single_page_pdf() -> None:
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    assert checked.file_type == "pdf"
    assert checked.page_count == 1
