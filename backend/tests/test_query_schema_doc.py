"""Tests for `app.query.schema_doc` (Phase 11 task 1): the view descriptions fed into the
`query_v1` prompt. These stay in sync with the fixture's `CREATE VIEW` column lists
(`query_fixture.py`) so a column renamed in one and not the other is caught here rather than
only showing up as a confusing wrong-answer test failure elsewhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.query.schema_doc import V_DOCUMENTS, V_FIELDS, VIEWS, describe_views

from .query_fixture import build_fixture_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return build_fixture_db(tmp_path / "fixture.db")


def _actual_view_columns(db_path: Path, view_name: str) -> tuple[str, ...]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"SELECT * FROM {view_name} LIMIT 0")
        return tuple(d[0] for d in cursor.description)
    finally:
        conn.close()


def test_v_documents_columns_match_the_fixtures_view(db_path: Path) -> None:
    assert V_DOCUMENTS.column_names() == _actual_view_columns(db_path, "v_documents")


def test_v_fields_columns_match_the_fixtures_view(db_path: Path) -> None:
    assert V_FIELDS.column_names() == _actual_view_columns(db_path, "v_fields")


def test_describe_views_mentions_every_column_of_both_views() -> None:
    text = describe_views()
    for view in VIEWS:
        assert view.name in text
        for column in view.columns:
            assert column.name in text


def test_describe_views_is_deterministic() -> None:
    assert describe_views() == describe_views()
