"""Tests for the demo-only "simulate an incoming email" button (D9 in docs/part2-decisions.md):
`app.inbox.demo_mail` and `POST /api/demo/incoming-email`.

The route only writes a file into the inbox folder; the last test hands that file to the real
`scan_inbox` (with a scripted fake model) to prove the watcher treats it as new mail and creates
a shipment with all three documents. Nothing here touches the real `samples/inbox` or `data/`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.route import RouteDecision
from app.agents.schema import ExtractionResult, FieldExtraction
from app.api import demo_routes
from app.graph import nodes as graph_nodes
from app.inbox.demo_mail import SOURCES, build_demo_email, next_reference
from app.inbox.eml import parse_eml
from app.inbox.subject import parse_subject
from app.inbox.watcher import scan_inbox
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse
from app.store import repository as repo
from app.store.db import connect

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "samples" / "inbox"
PRIMARY = "gemini-3.8-flash"


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
def inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir()
    monkeypatch.setattr(demo_routes, "_inbox_dir", lambda: folder)
    return folder


@pytest.fixture
def client(data_dir: Path):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --- the email builder ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["clean", "messy"])
def test_built_email_is_new_mail_for_acme_with_three_renamed_attachments(kind: str) -> None:
    _, data = build_demo_email(kind, sample_dir=SAMPLE_DIR, reference="SHP-9123")
    parsed = parse_eml(data)
    source = parse_eml((SAMPLE_DIR / SOURCES[kind][0]).read_bytes())

    parts = parse_subject(parsed.subject, ["acme"])
    assert (parts.customer_id, parts.reference) == ("acme", "SHP-9123")
    assert parsed.message_id != source.message_id  # otherwise the watcher would skip it
    assert [a.filename for a in parsed.attachments] == [
        a.filename.replace(SOURCES[kind][1], "SHP-9123") for a in source.attachments
    ]
    # the documents themselves are the sample's finished PDFs, untouched
    assert [a.data for a in parsed.attachments] == [a.data for a in source.attachments]


def test_two_builds_never_share_a_message_id() -> None:
    first = parse_eml(build_demo_email("clean", sample_dir=SAMPLE_DIR, reference="SHP-9001")[1])
    second = parse_eml(build_demo_email("clean", sample_dir=SAMPLE_DIR, reference="SHP-9001")[1])
    assert first.message_id != second.message_id


def test_next_reference_skips_taken_references() -> None:
    taken: set[str] = set()
    first = next_reference(lambda ref: ref in taken)
    taken.add(first)
    assert next_reference(lambda ref: ref in taken) != first


# --- the route --------------------------------------------------------------------------------


def test_post_writes_one_email_into_the_inbox(client, inbox: Path) -> None:
    response = client.post("/api/demo/incoming-email", json={"kind": "messy"})
    assert response.status_code == 201
    body = response.json()

    written = list(inbox.glob("*.eml"))
    assert [p.name for p in written] == [body["file"]]
    assert (
        parse_subject(parse_eml(written[0].read_bytes()).subject, ["acme"]).reference
        == (body["reference"])
    )


def test_two_posts_give_two_different_emails(client, inbox: Path) -> None:
    first = client.post("/api/demo/incoming-email", json={"kind": "clean"}).json()
    second = client.post("/api/demo/incoming-email", json={"kind": "clean"}).json()
    assert first["file"] != second["file"]
    assert len(list(inbox.glob("*.eml"))) == 2


def test_an_unknown_kind_is_rejected_and_writes_nothing(client, inbox: Path) -> None:
    response = client.post("/api/demo/incoming-email", json={"kind": "../../etc/passwd"})
    assert response.status_code == 422
    assert list(inbox.glob("*")) == []


# --- the watcher accepts it as new mail ------------------------------------------------------


def test_the_watcher_turns_the_generated_email_into_a_three_document_shipment(
    client, inbox: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    originals = (settings.gemini_model, settings.gemini_fallback_model)
    object.__setattr__(settings, "gemini_model", PRIMARY)
    object.__setattr__(settings, "gemini_fallback_model", "gemini-3.5-flash-lite")
    transport = FakeTransport()
    monkeypatch.setattr(graph_nodes, "transport_factory", lambda: transport)
    try:
        reference = client.post("/api/demo/incoming-email", json={"kind": "messy"}).json()[
            "reference"
        ]

        empty = {
            name: FieldExtraction(value=None, source_text=None, page=None, self_rating=0.0)
            for name in (
                "consignee",
                "hs_code",
                "port_of_loading",
                "port_of_discharge",
                "incoterms",
                "goods_description",
                "gross_weight",
                "invoice_number",
            )
        }
        for document_type in ("commercial_invoice", "bill_of_lading", "packing_list"):
            result = ExtractionResult(document_type=document_type, **empty)
            transport.queue(
                PRIMARY,
                RawResponse(
                    text=result.model_dump_json(),
                    input_tokens=1,
                    output_tokens=1,
                    thinking_tokens=0,
                ),
                RawResponse(
                    text=RouteDecision(
                        outcome="human_review",
                        reasoning="x",
                        cited_fields=[],
                        amendment_draft=None,
                    ).model_dump_json(),
                    input_tokens=1,
                    output_tokens=1,
                    thinking_tokens=0,
                ),
            )

        conn = connect(data_dir=data_dir)
        checkpoint_conn = sqlite3.connect(data_dir / "checkpoints.db", check_same_thread=False)
        try:
            touched = scan_inbox(conn, SqliteSaver(checkpoint_conn), inbox_dir=inbox)
            assert len(touched) == 1
            shipment = repo.get_shipment(conn, touched[0])
            assert shipment["reference"] == reference
            assert shipment["review_state"] == "pending_review"
            assert len(repo.list_shipment_documents(conn, touched[0])) == 3
            # scanning again finds nothing new: the email is recorded, not reprocessed
            assert scan_inbox(conn, SqliteSaver(checkpoint_conn), inbox_dir=inbox) == []
        finally:
            checkpoint_conn.close()
            conn.close()
    finally:
        object.__setattr__(settings, "gemini_model", originals[0])
        object.__setattr__(settings, "gemini_fallback_model", originals[1])
