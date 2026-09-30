"""End-to-end tests for the query layer's entry point, `answer_question` (design section 5,
Phase 11 verify list): design section 5's 6 sample questions, each answered correctly against
the fixture database, plus the one-retry-then-refuse behaviour when Gemini's SQL fails.

Every test uses `FakeTransport` (following `test_llm_client.py`'s pattern): no real Gemini
call is ever made. `FakeTransport` is queued with the exact SQL a real Gemini call would be
expected to return for each question; the point of these tests is that the gate and the answer
shaping run that SQL correctly against the fixture, not that Gemini itself writes good SQL.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.fake_transport import FakeTransport
from app.llm.recorder import CallRecorder
from app.llm.transport import RawResponse
from app.query.schema import SqlAnswer
from app.query.service import answer_question

from .query_fixture import EXPECTED, TODAY, build_fixture_db

PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash-lite"
TODAY_DATE = date.fromisoformat(TODAY)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return build_fixture_db(tmp_path / "fixture.db")


def _sql_response(sql: str, explanation: str = "answer") -> RawResponse:
    return RawResponse(
        text=SqlAnswer(sql=sql, explanation=explanation).model_dump_json(),
        input_tokens=100,
        output_tokens=20,
        thinking_tokens=0,
    )


def make_client(*, run_id: str = "query-run-1") -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport()
    client = LLMClient(
        transport=transport,
        primary_model=PRIMARY,
        fallback_model=FALLBACK,
        budget=CallBudget(),
        recorder=CallRecorder(),
        run_id=run_id,
        sleep=lambda _seconds: None,
    )
    return client, transport


# --- The 6 sample questions (design section 5) --------------------------------------------


def test_how_many_documents_were_flagged_for_review_this_week(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT COUNT(*) FROM v_documents WHERE outcome = 'human_review' "
            "AND finished_at >= '2026-09-28'"
        ),
    )

    answer = answer_question(
        question="How many documents were flagged for review this week?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "single_value"
    assert answer.value == EXPECTED["flagged_this_week_count"]
    assert answer.error is None


def test_which_documents_need_an_amendment_and_why(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT filename, reasoning FROM v_documents WHERE outcome = 'amendment_requested'"
        ),
    )

    answer = answer_question(
        question="Which documents need an amendment, and why?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "table"
    assert answer.columns == ("filename", "reasoning")
    assert [row[0] for row in answer.rows] == [EXPECTED["amendment_documents"][0][0]]


def test_what_is_the_most_common_mismatched_field(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT field_name, COUNT(*) AS mismatch_count FROM v_fields "
            "WHERE verdict = 'mismatch' GROUP BY field_name "
            "ORDER BY mismatch_count DESC LIMIT 1"
        ),
    )

    answer = answer_question(
        question="What is the most common mismatched field?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    # One row, two columns: shaping treats this as a table, not a single value.
    assert answer.kind == "table"
    assert answer.rows[0][0] == EXPECTED["most_common_mismatched_field"]


def test_show_everything_with_an_uncertain_hs_code(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT * FROM v_fields WHERE field_name = 'hs_code' AND verdict = 'uncertain'"
        ),
    )

    answer = answer_question(
        question="Show everything with an uncertain HS code.",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "table"
    run_id_index = answer.columns.index("run_id")
    run_ids = sorted(row[run_id_index] for row in answer.rows)
    assert run_ids == EXPECTED["uncertain_hs_code_run_ids"]


def test_what_was_the_average_cost_per_document(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(PRIMARY, _sql_response("SELECT AVG(cost_usd) AS avg_cost_usd FROM v_documents"))

    answer = answer_question(
        question="What was the average cost per document?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "single_value"
    assert abs(answer.value - EXPECTED["average_cost_usd"]) < 1e-9


def test_which_runs_were_overridden_by_code(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _sql_response(
            "SELECT run_id, filename, reasoning FROM v_documents "
            "WHERE decision_source = 'code_override'"
        ),
    )

    answer = answer_question(
        question="Which runs were overridden by code?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "table"
    run_id_index = answer.columns.index("run_id")
    run_ids = [row[run_id_index] for row in answer.rows]
    assert run_ids == EXPECTED["code_override_run_ids"]


# --- One retry on SQL failure, then a refusal ----------------------------------------------


def test_a_failing_query_is_retried_once_and_then_succeeds(db_path: Path) -> None:
    client, transport = make_client()
    # First attempt references a column that doesn't exist; the retry fixes it.
    transport.queue(PRIMARY, _sql_response("SELECT nonexistent_column FROM v_documents"))
    transport.queue(PRIMARY, _sql_response("SELECT COUNT(*) FROM v_documents"))

    answer = answer_question(
        question="How many documents are there?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "single_value"
    assert answer.value == 6
    assert answer.error is None
    assert len(transport.calls) == 2


def test_a_query_that_fails_twice_is_refused_with_the_error_shown(db_path: Path) -> None:
    client, transport = make_client()
    transport.queue(PRIMARY, _sql_response("SELECT nonexistent_column FROM v_documents"))
    transport.queue(PRIMARY, _sql_response("SELECT another_bad_column FROM v_documents"))

    answer = answer_question(
        question="How many documents are there?",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "refused"
    assert answer.error is not None
    assert answer.sql == "SELECT another_bad_column FROM v_documents"
    assert len(transport.calls) == 2


def test_a_gate_violation_is_refused_without_any_retry(db_path: Path) -> None:
    """A disallowed statement (design section 8: "blocked by the authorizer") is never worth
    retrying -- Gemini asking again for the same forbidden table won't fix anything."""
    client, transport = make_client()
    transport.queue(PRIMARY, _sql_response("DELETE FROM runs"))

    answer = answer_question(
        question="Delete every run.",
        db_path=db_path,
        client=client,
        today=TODAY_DATE,
    )

    assert answer.kind == "refused"
    assert "not allowed" in answer.error
    # Only the first call was made: a GateViolation never triggers the retry call.
    assert len(transport.calls) == 1
