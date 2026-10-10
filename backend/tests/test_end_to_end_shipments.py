"""End-to-end test on the two sample shipments (part2-shipment-workflow tasks.md task 14,
requirements 1.7, 12.1, 12.5).

Copies the real `samples/inbox/01-clean-shipment.eml` and `02-messy-shipment.eml` into a
scratch inbox and drives the whole chain with a scripted `FakeTransport` whose responses
reproduce exactly what the ground truth in `samples/shipments.py` says is printed on each
page: `.eml` -> `emails` -> `documents` -> `runs` -> cross-checks -> shipment outcome ->
draft -> operator send -> `outbox` -> `v_pending_shipments`.

Why the FakeTransport responses echo the ground truth printed text instead of hand-picked
values: the Extractor's grounding step (`app.trust.grounding.ground_field`) searches the
page's real text spans for the claimed `source_text`, and `app.rules.checkers` re-normalises
every value against the real rules. Values that didn't match what is actually printed on
these (undegraded, clean-text) PDFs would trigger the 300 DPI retry path or fail value/format
checks unpredictably -- this test asserts against the deterministic outcome the design
promises, so the inputs have to be the real printed strings, not approximations.

Clean shipment (01-clean-shipment.eml, SHP-1042): three documents, every field matching,
every cross-check consistent -> `auto_approve`, an approval draft.
Messy shipment (02-messy-shipment.eml, SHP-1043): HS code 850440 not on ACME's approved list
(a mismatch on every document that carries it) and the bill of lading's consignee differs
from the invoice and packing list (a cross-document inconsistency) -> `amendment_request`,
an amendment draft naming both.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.route import RouteDecision
from app.agents.schema import ExtractionResult, FieldExtraction
from app.agents.validate import FieldJudgement, ValidationJudgementResult
from app.graph import nodes as graph_nodes
from app.inbox.watcher import scan_inbox
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse
from app.store import repository as repo
from app.store.db import connect

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
SAMPLE_INBOX = SAMPLES_DIR / "inbox"
PRIMARY = "gemini-3.8-flash"

# --- ground truth, read straight from samples/shipments.py and the rendered PDFs' printed
# text (also mirrored in samples/inbox/*.shipment.json) --------------------------------------

# Clean shipment, SHP-1042: every document agrees, every value is inside ACME's rules.
_CLEAN_INVOICE = {
    "consignee": ("ACME Electronics Pte. Ltd.", "ACME Electronics Pte. Ltd."),
    "hs_code": ("847130", "8471.30"),
    "port_of_loading": ("Shanghai", "SHANGHAI, CHINA"),
    "port_of_discharge": ("Singapore", "SINGAPORE"),
    "incoterms": ("CIF", "CIF Singapore"),
    "goods_description": (
        "Portable laptop computers, 14-inch, 16 GB RAM",
        "Portable laptop computers, 14-inch, 16 GB RAM",
    ),
    "gross_weight": ("862.40 KG", "862.40 KGS"),
    "invoice_number": ("INV-2026-00417", "INV-2026-00417"),
}
_CLEAN_BOL = dict(_CLEAN_INVOICE)
_CLEAN_PACKING = dict(_CLEAN_INVOICE)

# Messy shipment, SHP-1043: hs_code is 8504.40 (planted error E1) on every document; the
# bill of lading's consignee is Zenith Trading Holdings (planted error X1, cross-document).
_MESSY_INVOICE = dict(_CLEAN_INVOICE)
_MESSY_INVOICE["hs_code"] = ("850440", "8504.40")
_MESSY_INVOICE["invoice_number"] = ("INV-2026-00604", "INV-2026-00604")

_MESSY_BOL = dict(_MESSY_INVOICE)
_MESSY_BOL["consignee"] = ("Zenith Trading Holdings Pte. Ltd.", "Zenith Trading Holdings Pte. Ltd.")

_MESSY_PACKING = dict(_MESSY_INVOICE)


# Which fields each Doc_Type genuinely prints (samples/shipments.py::EXPECTED_FIELDS). A
# bill of lading never prints an Incoterm or an invoice number; a packing list never prints
# the ports, the Incoterm or the goods description. Asking the fake extraction to report a
# value for a field that isn't really on the page would make `ground_field` search for text
# that doesn't exist, trigger the 300 DPI retry path, and consume an extra queued LLM call --
# so each document type only gets the fields it actually carries; the rest are reported null,
# exactly as a real Extractor reading a real page would.
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
_PRINTED_FIELDS = {
    "commercial_invoice": _ALL_FIELD_NAMES,
    "bill_of_lading": (
        "consignee",
        "hs_code",
        "port_of_loading",
        "port_of_discharge",
        "gross_weight",
    ),
    "packing_list": ("consignee", "hs_code", "gross_weight", "invoice_number"),
}


def _extraction_response(document_type: str, fields: dict[str, tuple[str, str]]) -> RawResponse:
    printed = _PRINTED_FIELDS[document_type]
    field_objs = {}
    for name in _ALL_FIELD_NAMES:
        if name in printed and name in fields:
            value, source = fields[name]
            field_objs[name] = FieldExtraction(
                value=value, source_text=source, page=1, self_rating=0.95
            )
        else:
            field_objs[name] = FieldExtraction(
                value=None, source_text=None, page=None, self_rating=0.0
            )
    result = ExtractionResult(document_type=document_type, **field_objs)
    return RawResponse(
        text=result.model_dump_json(), input_tokens=500, output_tokens=100, thinking_tokens=0
    )


def _judgement_response(*, consignee_matches: bool = True) -> RawResponse:
    result = ValidationJudgementResult(
        consignee=FieldJudgement(
            verdict="match" if consignee_matches else "mismatch",
            reasoning="exact" if consignee_matches else "different company entirely",
            self_rating=1.0,
        ),
        goods_description=FieldJudgement(verdict="match", reasoning="specific", self_rating=1.0),
    )
    return RawResponse(
        text=result.model_dump_json(), input_tokens=200, output_tokens=50, thinking_tokens=0
    )


def _route_response(outcome: str) -> RawResponse:
    reasoning = (
        "Every field matched the customer's rules."
        if outcome == "auto_approve"
        else "See the field verdicts."
    )
    decision = RouteDecision(
        outcome=outcome, reasoning=reasoning, cited_fields=[], amendment_draft=None
    )
    return RawResponse(
        text=decision.model_dump_json(), input_tokens=300, output_tokens=80, thinking_tokens=0
    )


def _queue_clean_shipment(transport: FakeTransport) -> None:
    # Attachment order in 01-clean-shipment.eml: invoice, bill of lading, packing list.
    transport.queue(PRIMARY, _extraction_response("commercial_invoice", _CLEAN_INVOICE))
    transport.queue(PRIMARY, _judgement_response())
    transport.queue(PRIMARY, _route_response("auto_approve"))

    transport.queue(PRIMARY, _extraction_response("bill_of_lading", _CLEAN_BOL))
    transport.queue(PRIMARY, _judgement_response())
    transport.queue(PRIMARY, _route_response("auto_approve"))

    transport.queue(PRIMARY, _extraction_response("packing_list", _CLEAN_PACKING))
    transport.queue(PRIMARY, _judgement_response())
    transport.queue(PRIMARY, _route_response("auto_approve"))


def _queue_messy_shipment(transport: FakeTransport) -> None:
    # Attachment order in 02-messy-shipment.eml: invoice, bill of lading, packing list.
    transport.queue(PRIMARY, _extraction_response("commercial_invoice", _MESSY_INVOICE))
    transport.queue(PRIMARY, _judgement_response(consignee_matches=True))
    transport.queue(PRIMARY, _route_response("amendment_request"))

    transport.queue(PRIMARY, _extraction_response("bill_of_lading", _MESSY_BOL))
    transport.queue(PRIMARY, _judgement_response(consignee_matches=False))
    transport.queue(PRIMARY, _route_response("amendment_request"))

    transport.queue(PRIMARY, _extraction_response("packing_list", _MESSY_PACKING))
    transport.queue(PRIMARY, _judgement_response(consignee_matches=True))
    transport.queue(PRIMARY, _route_response("amendment_request"))


# --- fixtures ---------------------------------------------------------------------------------


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTransport]:
    from app.api import routes as api_routes
    from app.config import settings

    originals = (settings.gemini_model, settings.gemini_fallback_model)
    object.__setattr__(settings, "gemini_model", PRIMARY)
    object.__setattr__(settings, "gemini_fallback_model", "gemini-3.5-flash-lite")
    transport = FakeTransport()
    monkeypatch.setattr(graph_nodes, "transport_factory", lambda: transport)
    monkeypatch.setattr(api_routes, "query_transport_factory", lambda: transport)
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
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


def _copy_sample(name: str, inbox_dir: Path) -> Path:
    destination = inbox_dir / name
    shutil.copyfile(SAMPLE_INBOX / name, destination)
    return destination


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --- the end-to-end test -----------------------------------------------------------------------


def test_clean_shipment_end_to_end_through_api_to_outbox(
    client: TestClient,
    conn: sqlite3.Connection,
    checkpointer: SqliteSaver,
    fake_transport: FakeTransport,
    inbox_dir: Path,
    data_dir: Path,
) -> None:
    """.eml -> emails -> documents -> runs -> cross-checks -> auto_approve -> approval draft
    -> operator send -> outbox -> v_pending_shipments (requirement 12.1, 12.5)."""
    _copy_sample("01-clean-shipment.eml", inbox_dir)
    _queue_clean_shipment(fake_transport)

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(touched) == 1
    shipment_id = touched[0]

    shipment = repo.get_shipment(conn, shipment_id)
    assert shipment["review_state"] == "pending_review"
    assert shipment["customer_id"] == "acme"
    assert shipment["reference"] == "SHP-1042"

    documents = repo.list_shipment_documents(conn, shipment_id)
    assert len(documents) == 3
    assert {d["doc_type"] for d in documents} == {
        "commercial_invoice",
        "bill_of_lading",
        "packing_list",
    }
    conn.close()

    # GET /api/shipments/{id} triggers the lazy orchestration (D7): cross-check, decision,
    # draft generation, all persisted.
    detail = client.get(f"/api/shipments/{shipment_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["outcome"] == "auto_approve"
    assert body["discrepancy_count"] == 0
    assert body["uncertain_count"] == 0
    assert all(f["verdict"] in ("match", "not_applicable") for f in body["field_results"])
    assert all(c["result"] == "consistent" for c in body["cross_checks"])

    draft = client.get(f"/api/shipments/{shipment_id}/draft")
    assert draft.status_code == 200
    assert draft.json()["kind"] == "approval"
    assert draft.json()["sent"] is False

    send = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert send.status_code == 200
    assert send.json()["review_state"] == "approved_sent"

    sent_draft = client.get(f"/api/shipments/{shipment_id}/draft")
    assert sent_draft.json()["sent"] is True

    # v_pending_shipments and GET /api/shipments must agree (requirement 12.4): this
    # shipment is no longer pending, having just been sent.
    conn2 = connect(data_dir=data_dir)
    pending = conn2.execute(
        "SELECT review_state FROM v_pending_shipments WHERE shipment_reference = 'SHP-1042'"
    ).fetchone()
    assert pending["review_state"] == "approved_sent"
    conn2.close()


def test_messy_shipment_end_to_end_names_each_discrepancy(
    client: TestClient,
    conn: sqlite3.Connection,
    checkpointer: SqliteSaver,
    fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    """amendment_request, with the HS code mismatch and the cross-document consignee
    inconsistency both named, each with the value found in each document involved."""
    _copy_sample("02-messy-shipment.eml", inbox_dir)
    _queue_messy_shipment(fake_transport)

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(touched) == 1
    shipment_id = touched[0]
    shipment = repo.get_shipment(conn, shipment_id)
    assert shipment["reference"] == "SHP-1043"
    conn.close()

    detail = client.get(f"/api/shipments/{shipment_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["outcome"] == "amendment_request"
    assert body["discrepancy_count"] >= 2  # the HS code mismatch + the cross-doc consignee

    hs_code_mismatches = [
        f
        for f in body["field_results"]
        if f["field_name"] == "hs_code" and f["verdict"] == "mismatch"
    ]
    assert len(hs_code_mismatches) >= 1

    by_field = {c["field_name"]: c for c in body["cross_checks"]}
    assert by_field["consignee"]["result"] == "inconsistent"
    consignee_values = {row["value"] for row in by_field["consignee"]["compared"]}
    assert "Zenith Trading Holdings Pte. Ltd." in consignee_values
    assert "ACME Electronics Pte. Ltd." in consignee_values
    assert by_field["hs_code"]["result"] == "consistent"  # wrong on every page, but agreeing

    draft = client.get(f"/api/shipments/{shipment_id}/draft")
    assert draft.status_code == 200
    draft_body = draft.json()
    assert draft_body["kind"] == "amendment"
    assert "hs_code" in draft_body["body"]
    assert "Zenith Trading Holdings" in draft_body["body"] or "consignee" in draft_body["body"]

    send = client.post(f"/api/shipments/{shipment_id}/send", json={})
    assert send.status_code == 200
    assert send.json()["review_state"] == "amendment_sent"


def test_the_two_failure_cases_are_recorded_and_start_no_processing(
    conn: sqlite3.Connection,
    checkpointer: SqliteSaver,
    fake_transport: FakeTransport,
    inbox_dir: Path,
) -> None:
    """requirement 1.7: an unknown-customer email and a no-attachments email are both
    recorded failed, with a plain-language reason, and never reach the pipeline."""
    _copy_sample("03-unknown-customer.eml", inbox_dir)
    _copy_sample("04-no-attachments.eml", inbox_dir)

    touched = scan_inbox(conn, checkpointer, inbox_dir=inbox_dir)
    assert len(touched) == 2
    assert len(fake_transport.calls) == 0

    for shipment_id in touched:
        shipment = repo.get_shipment(conn, shipment_id)
        assert shipment["review_state"] == "failed"
        assert shipment["failure_reason"]
