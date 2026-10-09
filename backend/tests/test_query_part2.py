"""Tests for Phase 11 task 12: wiring `v_pending_shipments` into the plain-English query
layer (design section 5, R12.1-R12.4).

These prove four things:
1. `gate.py`'s allow-list now includes `v_pending_shipments`, and the authorizer change
   actually lets a real query through end to end (not just that the tuple contains a string).
2. `schema_doc.describe_views()` documents the new view and all 12 of its columns.
3. `query_v2.txt` exists, is well-formed, and `query_v1.txt` was never touched.
4. `answer_question` can tell "no such customer" apart from "this customer has nothing
   pending", using `service.customer_exists`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.fake_transport import FakeTransport
from app.llm.prompt_files import load_prompt
from app.llm.recorder import CallRecorder
from app.llm.transport import RawResponse
from app.query import gate, schema_doc
from app.query.gate import run_query
from app.query.schema import SqlAnswer
from app.query.service import answer_question, customer_exists
from app.store import repository as repo
from app.store.db import connect

PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash-lite"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "app" / "llm" / "prompts"


# --- gate.py ------------------------------------------------------------------------------


def test_v_pending_shipments_is_allowed() -> None:
    assert "v_pending_shipments" in gate.DEFAULT_ALLOWED_VIEWS


def test_run_query_can_select_from_v_pending_shipments(tmp_path: Path) -> None:
    """End to end: seeds a real Part 2 database and proves the authorizer change, not just
    the allow-list tuple, lets a query against the new view through."""
    conn = connect(data_dir=tmp_path)
    try:
        email_id = repo.record_email(
            conn,
            message_id="<m-1@su.example>",
            eml_path="inbox/m-1.eml",
            sender="ops@su.example",
            subject="ACME-001 SHP-5501 documents attached",
            received_at="2026-10-10T09:00:00+00:00",
            attachment_count=1,
            attachment_filenames=["invoice.pdf"],
        )
        shipment_id = repo.create_shipment_for_email(
            conn,
            customer_id="acme",
            customer_name="ACME Electronics Pte. Ltd.",
            reference="SHP-5501",
            email_id=email_id,
            cycle_number=1,
            parent_shipment_id=None,
            review_state="pending_review",
        )
    finally:
        conn.close()

    db_path = tmp_path / "app.db"
    result = run_query(db_path, "SELECT shipment_reference, review_state FROM v_pending_shipments")
    assert result.columns == ("shipment_reference", "review_state")
    assert result.rows == (("SHP-5501", "pending_review"),)
    assert shipment_id  # created successfully


def test_direct_table_read_is_still_denied(tmp_path: Path) -> None:
    """The new view doesn't widen the hole: a direct read of the real table underneath it is
    still refused."""
    connect(data_dir=tmp_path).close()
    db_path = tmp_path / "app.db"
    with pytest.raises(gate.GateViolation):
        run_query(db_path, "SELECT * FROM shipment_reviews")


# --- schema_doc.py -------------------------------------------------------------------------

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


def test_describe_views_mentions_the_new_view_and_all_its_columns() -> None:
    text = schema_doc.describe_views()
    assert "v_pending_shipments" in text
    for column in V_PENDING_SHIPMENTS_COLUMNS:
        assert column in text


def test_v_pending_shipments_in_views_tuple_and_view_names() -> None:
    assert schema_doc.V_PENDING_SHIPMENTS in schema_doc.VIEWS
    assert "v_pending_shipments" in schema_doc.VIEW_NAMES
    # The two existing views are untouched.
    assert schema_doc.V_DOCUMENTS in schema_doc.VIEWS
    assert schema_doc.V_FIELDS in schema_doc.VIEWS


# --- query_v2.txt / query_v1.txt ------------------------------------------------------------


def test_query_v2_prompt_file_is_well_formed() -> None:
    path = PROMPTS_DIR / "query_v2.txt"
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("version: 2")
    assert "\n---\n" in raw
    assert "v_pending_shipments" in raw

    prompt = load_prompt("query_v2")
    assert prompt.version == "2"
    assert "v_pending_shipments" in prompt.text


def test_query_v1_prompt_file_is_untouched() -> None:
    path = PROMPTS_DIR / "query_v1.txt"
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("version: 1")
    # The original two-view wording, never edited for Part 2.
    assert "v_pending_shipments" not in raw


# --- unknown customer vs. no pending shipments ---------------------------------------------


def test_customer_exists_true_for_a_known_rules_file() -> None:
    assert customer_exists("acme") is True
    assert customer_exists("ACME") is True  # case-insensitive, like customer ids elsewhere


def test_customer_exists_false_for_an_unknown_customer() -> None:
    assert customer_exists("globex") is False


def _sql_response(sql: str, explanation: str = "answer") -> RawResponse:
    return RawResponse(
        text=SqlAnswer(sql=sql, explanation=explanation).model_dump_json(),
        input_tokens=100,
        output_tokens=20,
        thinking_tokens=0,
    )


def make_client() -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport()
    client = LLMClient(
        transport=transport,
        primary_model=PRIMARY,
        fallback_model=FALLBACK,
        budget=CallBudget(),
        recorder=CallRecorder(),
        run_id="query-part2-run",
        sleep=lambda _seconds: None,
    )
    return client, transport


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """A real Part 2 database with no shipments at all, so `v_pending_shipments` is empty for
    every customer -- the fixture both answers need."""
    connect(data_dir=tmp_path).close()
    return tmp_path / "app.db"


def test_unknown_customer_is_refused_as_no_such_customer(empty_db: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT * FROM v_pending_shipments WHERE customer_id = 'globex' "
            "AND review_state = 'pending_review'"
        ),
    )

    answer = answer_question(
        question="What shipments are pending review for customer globex?",
        db_path=empty_db,
        client=client,
        today=date(2026, 9, 30),
    )

    assert answer.kind == "refused"
    assert "no such customer" in answer.error


def test_known_customer_with_nothing_pending_is_an_ordinary_empty_answer(empty_db: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT * FROM v_pending_shipments WHERE customer_id = 'acme' "
            "AND review_state = 'pending_review'"
        ),
    )

    answer = answer_question(
        question="What shipments are pending review for customer acme?",
        db_path=empty_db,
        client=client,
        today=date(2026, 9, 30),
    )

    # acme has a rules file, so an empty result here is simply "nothing pending" -- not a
    # refusal, exactly like any other zero-row answer the query layer already supported.
    assert answer.kind == "table"
    assert answer.rows == ()
    assert answer.error is None
