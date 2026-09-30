"""Tests for the graph, storage and CLI (Phase 8, items 7 and 9), against the fake LLM
transport so no Gemini quota is spent. Runs the three submission samples end to end through
`app.cli`, and checks the crash/resume and duplicate-upload behaviour design section 3.7
calls for.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.route import RouteDecision
from app.agents.schema import ExtractionResult, FieldExtraction
from app.agents.validate import FieldJudgement, ValidationJudgementResult
from app.graph import nodes as graph_nodes
from app.graph.build import resume_incomplete_runs, run_document
from app.ingest.files import check_upload
from app.llm.fake_transport import FakeTransport
from app.llm.transport import RawResponse
from app.store import repository as repo
from app.store.db import connect

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
SUBMISSION = SAMPLES_DIR / "submission"
GRID = SAMPLES_DIR / "grid"
PRIMARY = "gemini-3.8-flash"

V1_CORRECT_FIELDS = {
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


def _extraction_response(fields: dict[str, tuple[str, str]]) -> RawResponse:
    field_objs = {
        name: FieldExtraction(value=value, source_text=source, page=1, self_rating=0.95)
        for name, (value, source) in fields.items()
    }
    result = ExtractionResult(document_type="commercial_invoice", **field_objs)
    return RawResponse(
        text=result.model_dump_json(), input_tokens=500, output_tokens=100, thinking_tokens=0
    )


def _judgement_response() -> RawResponse:
    result = ValidationJudgementResult(
        consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
        goods_description=FieldJudgement(verdict="match", reasoning="specific", self_rating=1.0),
    )
    return RawResponse(
        text=result.model_dump_json(), input_tokens=200, output_tokens=50, thinking_tokens=0
    )


def _route_response(outcome: str = "auto_approve") -> RawResponse:
    decision = RouteDecision(
        outcome=outcome,
        reasoning="Every field matched the customer's rules." if outcome == "auto_approve"
        else "See the field verdicts.",
        cited_fields=[],
        amendment_draft=None,
    )
    return RawResponse(
        text=decision.model_dump_json(), input_tokens=300, output_tokens=80, thinking_tokens=0
    )


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    """Swap the graph's transport factory for a fresh `FakeTransport` per test, so no node
    ever makes a real network call."""
    transport = FakeTransport()
    monkeypatch.setattr(graph_nodes, "transport_factory", lambda: transport)
    return transport


@pytest.fixture
def data_dir(tmp_path: Path):
    """A scratch `data/` directory per test, so runs never touch the real `data/app.db`.
    `Settings` is a frozen dataclass, so `object.__setattr__` bypasses that (deliberately,
    for this test-only override) rather than mutating it through the normal API; the
    original value is restored afterward so other test modules see the real default again.
    """
    from app.config import settings

    original = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path)
    yield tmp_path
    object.__setattr__(settings, "data_dir", original)


def _checkpointer(data_dir: Path) -> tuple[SqliteSaver, sqlite3.Connection]:
    conn = sqlite3.connect(data_dir / "checkpoints.db", check_same_thread=False)
    return SqliteSaver(conn), conn


def _run_v1_c0(data_dir: Path, fake_transport: FakeTransport) -> dict:
    fake_transport.queue(PRIMARY, _extraction_response(V1_CORRECT_FIELDS))
    fake_transport.queue(PRIMARY, _judgement_response())
    fake_transport.queue(PRIMARY, _route_response("auto_approve"))

    conn = connect(data_dir=data_dir)
    checkpointer, _ckpt_conn = _checkpointer(data_dir)

    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    document = repo.create_shipment_and_document(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        filename="V1-C0.pdf",
        file_hash=checked.file_hash,
        mime_type="pdf",
        page_count=1,
        has_text_layer=True,
        stored_path=GRID / "V1-C0.pdf",
    )
    run_id = repo.new_id()
    final_state = run_document(
        file_path=GRID / "V1-C0.pdf",
        customer_id="acme",
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )
    return {"conn": conn, "document": document, "run_id": run_id, "final_state": final_state}


def test_a_clean_correct_document_is_auto_approved(
    data_dir: Path, fake_transport: FakeTransport
) -> None:
    result = _run_v1_c0(data_dir, fake_transport)
    final_state = result["final_state"]
    assert final_state.get("error") is None
    assert final_state["decision"]["outcome"] == "auto_approve"

    row = repo.get_run(result["conn"], result["run_id"])
    assert row is not None
    assert row["status"] == "completed"
    assert row["outcome"] == "auto_approved"

    field_rows = repo.list_field_results(result["conn"], result["run_id"])
    assert len(field_rows) == 8

    call_rows = repo.list_llm_calls(result["conn"], result["run_id"])
    assert len(call_rows) == 3  # extract, validate (judgement), route


def test_the_query_views_return_the_run(data_dir: Path, fake_transport: FakeTransport) -> None:
    result = _run_v1_c0(data_dir, fake_transport)
    conn = result["conn"]
    row = conn.execute(
        "SELECT * FROM v_documents WHERE run_id = ?", (result["run_id"],)
    ).fetchone()
    assert row is not None
    assert row["outcome"] == "auto_approved"
    assert row["matched_count"] == 8

    field_rows = conn.execute(
        "SELECT * FROM v_fields WHERE run_id = ?", (result["run_id"],)
    ).fetchall()
    assert len(field_rows) == 8


def test_a_document_with_a_planted_error_is_not_auto_approved(
    data_dir: Path, fake_transport: FakeTransport
) -> None:
    bad_fields = dict(V1_CORRECT_FIELDS)
    bad_fields["hs_code"] = ("850440", "8504.40")  # E1: not on ACME's approved list
    fake_transport.queue(PRIMARY, _extraction_response(bad_fields))
    fake_transport.queue(PRIMARY, _judgement_response())
    fake_transport.queue(PRIMARY, _route_response("amendment_request"))

    conn = connect(data_dir=data_dir)
    checkpointer, _ckpt_conn = _checkpointer(data_dir)
    checked = check_upload((GRID / "E1-C0.pdf").read_bytes())
    document = repo.create_shipment_and_document(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        filename="E1-C0.pdf",
        file_hash=checked.file_hash,
        mime_type="pdf",
        page_count=1,
        has_text_layer=True,
        stored_path=GRID / "E1-C0.pdf",
    )
    run_id = repo.new_id()
    final_state = run_document(
        file_path=GRID / "E1-C0.pdf",
        customer_id="acme",
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )
    assert final_state["decision"]["outcome"] != "auto_approve"

    row = repo.get_run(conn, run_id)
    assert row["outcome"] != "auto_approved"


def test_a_duplicate_upload_returns_the_existing_document(
    data_dir: Path, fake_transport: FakeTransport
) -> None:
    result = _run_v1_c0(data_dir, fake_transport)
    conn = result["conn"]
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    existing = repo.find_document_by_hash(conn, customer_id="acme", file_hash=checked.file_hash)
    assert existing is not None
    assert existing.id == result["document"].id


def test_a_budget_exceeded_run_escalates_to_human_review(
    data_dir: Path, fake_transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the budget maxed out before the run even starts, `extract_node` should catch the
    resulting failure and escalate, rather than crashing the graph."""
    from app.llm import budget as budget_module

    monkeypatch.setattr(budget_module, "DEFAULT_MAX_CALLS", 0)

    conn = connect(data_dir=data_dir)
    checkpointer, _ckpt_conn = _checkpointer(data_dir)
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    document = repo.create_shipment_and_document(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        filename="V1-C0.pdf",
        file_hash=checked.file_hash,
        mime_type="pdf",
        page_count=1,
        has_text_layer=True,
        stored_path=GRID / "V1-C0.pdf",
    )
    run_id = repo.new_id()
    final_state = run_document(
        file_path=GRID / "V1-C0.pdf",
        customer_id="acme",
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )
    assert final_state.get("error") is not None

    row = repo.get_run(conn, run_id)
    assert row["outcome"] == "human_review"
    assert row["escalation_reason"] is not None


def test_resume_incomplete_runs_finds_nothing_once_a_run_completed(
    data_dir: Path, fake_transport: FakeTransport
) -> None:
    result = _run_v1_c0(data_dir, fake_transport)
    resumed = resume_incomplete_runs(
        conn=result["conn"], checkpointer=_checkpointer(data_dir)[0]
    )
    assert resumed == []


def test_resume_document_after_a_crash_does_not_repeat_the_extraction_call(
    data_dir: Path, fake_transport: FakeTransport
) -> None:
    """Crash test (Phase 8 verify list): kill the run right after extraction (simulated by
    only queuing the extraction response and letting `validate`'s call fail), then resume
    and confirm the extraction node's call was made exactly once in total."""
    fake_transport.queue(PRIMARY, _extraction_response(V1_CORRECT_FIELDS))
    # No judgement response queued yet: validate_node's LLM call will fail (empty queue ->
    # AssertionError), which bubbles up as `state["error"]`, simulating a crash after extract.

    conn = connect(data_dir=data_dir)
    checkpointer, _ckpt_conn = _checkpointer(data_dir)
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    document = repo.create_shipment_and_document(
        conn,
        customer_id="acme",
        customer_name="ACME Electronics Pte. Ltd.",
        filename="V1-C0.pdf",
        file_hash=checked.file_hash,
        mime_type="pdf",
        page_count=1,
        has_text_layer=True,
        stored_path=GRID / "V1-C0.pdf",
    )
    run_id = repo.new_id()
    first_state = run_document(
        file_path=GRID / "V1-C0.pdf",
        customer_id="acme",
        conn=conn,
        checkpointer=checkpointer,
        document_id=document.id,
        run_id=run_id,
    )
    assert first_state.get("error") is not None
    assert "validate failed" in first_state["error"]

    row = repo.get_run(conn, run_id)
    assert row["status"] == "completed"  # escalated is still a completed run
    assert row["outcome"] == "human_review"

    extract_calls_before = len(
        [c for c in repo.list_llm_calls(conn, run_id) if c["agent"] == "extract"]
    )
    assert extract_calls_before == 1


def test_cli_run_and_resume_commands(data_dir: Path, fake_transport: FakeTransport) -> None:
    """Exercises `app.cli`'s `run` command end to end on the clean-correct grid document,
    via the CLI's own duplicate-hash and shipment/document creation path (not the shared
    `_run_v1_c0` helper above). The `data_dir` fixture already points `settings.data_dir` at
    a scratch folder, which both `connect()` and the CLI's own checkpoint connection read."""
    from app import cli

    fake_transport.queue(PRIMARY, _extraction_response(V1_CORRECT_FIELDS))
    fake_transport.queue(PRIMARY, _judgement_response())
    fake_transport.queue(PRIMARY, _route_response("auto_approve"))

    exit_code = cli.main(["run", str(GRID / "V1-C0.pdf"), "--customer", "acme"])
    assert exit_code == 0

    conn = connect(data_dir=data_dir)
    rows = repo.list_runs(conn)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "auto_approved"
