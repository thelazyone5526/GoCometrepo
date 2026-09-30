"""Tests for the query gate's safety checks (design section 5 step 2 and section 8; Phase 11
verify list): read-only connection, single-statement-only, the authorizer, `LIMIT 200`, and
the progress handler. Every test runs against `query_fixture.build_fixture_db`, never a real
`app.db`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.query.gate import (
    DEFAULT_ROW_LIMIT,
    GateViolation,
    QueryExecutionError,
    run_query,
)

from .query_fixture import build_fixture_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return build_fixture_db(tmp_path / "fixture.db")


# --- Ordinary SELECTs are allowed --------------------------------------------------------


def test_a_plain_select_on_v_documents_succeeds(db_path: Path) -> None:
    result = run_query(db_path, "SELECT run_id, outcome FROM v_documents")
    assert result.columns == ("run_id", "outcome")
    assert len(result.rows) == 6


def test_a_select_on_v_fields_succeeds(db_path: Path) -> None:
    result = run_query(db_path, "SELECT field_name FROM v_fields WHERE verdict = 'mismatch'")
    assert len(result.rows) >= 1


def test_a_trailing_semicolon_is_fine(db_path: Path) -> None:
    result = run_query(db_path, "SELECT COUNT(*) FROM v_documents;")
    assert result.rows == ((6,),)


def test_a_with_cte_select_is_allowed(db_path: Path) -> None:
    # 3 runs are human_review in the fixture overall (R2, R4, R6) -- unlike the "this week"
    # sample question, this query has no date filter.
    result = run_query(
        db_path,
        "WITH flagged AS (SELECT * FROM v_documents WHERE outcome = 'human_review') "
        "SELECT COUNT(*) FROM flagged",
    )
    assert result.rows == ((3,),)


def test_result_is_wrapped_with_limit_200(db_path: Path) -> None:
    # Not enough rows in the fixture to prove the cap bites, but this confirms the wrapping
    # doesn't change an ordinary result and stays under the row limit.
    result = run_query(db_path, "SELECT * FROM v_documents")
    assert len(result.rows) <= DEFAULT_ROW_LIMIT


def test_a_lower_row_limit_is_honoured(db_path: Path) -> None:
    result = run_query(db_path, "SELECT * FROM v_documents", row_limit=2)
    assert len(result.rows) == 2


# --- Security: writes, DDL, PRAGMA, ATTACH ------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM runs",
        "DELETE FROM v_documents",
        "DROP TABLE runs",
        "DROP VIEW v_documents",
        "UPDATE runs SET outcome = 'auto_approved'",
        "INSERT INTO runs (id) VALUES (999)",
    ],
)
def test_write_and_ddl_statements_are_refused(db_path: Path, sql: str) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, sql)


def test_pragma_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "PRAGMA table_info(runs)")


def test_pragma_table_valued_function_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT * FROM pragma_table_info('runs')")


def test_attach_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "ATTACH DATABASE ':memory:' AS other")


def test_direct_read_of_runs_is_refused(db_path: Path) -> None:
    """The gate's authorizer only allows reading the underlying tables through the two
    views -- a direct `SELECT * FROM runs` must be denied even though `runs` is real."""
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT * FROM runs")


def test_direct_read_of_field_results_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT * FROM field_results")


def test_sqlite_master_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT * FROM sqlite_master")


def test_two_statements_separated_by_semicolon_are_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT 1; DROP TABLE runs;")


def test_two_selects_separated_by_semicolon_are_also_refused(db_path: Path) -> None:
    """Even two harmless SELECTs count as more than one statement."""
    with pytest.raises(GateViolation):
        run_query(db_path, "SELECT 1; SELECT 2;")


def test_a_non_select_first_word_is_refused_before_reaching_sqlite(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "EXPLAIN SELECT * FROM v_documents")


def test_empty_sql_is_refused(db_path: Path) -> None:
    with pytest.raises(GateViolation):
        run_query(db_path, "   ")


# --- Genuine SQL mistakes are a QueryExecutionError, not a GateViolation -----------------


def test_an_unknown_column_is_a_query_execution_error_not_a_gate_violation(db_path: Path) -> None:
    with pytest.raises(QueryExecutionError):
        run_query(db_path, "SELECT nonexistent_column FROM v_documents")


def test_a_syntax_error_is_a_query_execution_error(db_path: Path) -> None:
    with pytest.raises(QueryExecutionError):
        run_query(db_path, "SELECT * FROM WHERE")


# --- The progress handler stops a slow query ---------------------------------------------


def test_a_slow_query_is_stopped_by_the_progress_handler(db_path: Path) -> None:
    # A recursive CTE that never touches a real table or view still has to pass the
    # authorizer's default-deny (it's allowed: SQLITE_RECURSIVE and SQLITE_FUNCTION are
    # allowed unconditionally), so this exercises the progress handler in isolation.
    slow_sql = (
        "WITH RECURSIVE cnt(x) AS ("
        "SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 100000000"
        ") SELECT COUNT(*) FROM cnt"
    )
    start = time.monotonic()
    with pytest.raises(QueryExecutionError):
        run_query(db_path, slow_sql, timeout_seconds=0.1)
    elapsed = time.monotonic() - start
    # Generous upper bound: proves the query was aborted, not merely slow to finish on its own.
    assert elapsed < 5.0
