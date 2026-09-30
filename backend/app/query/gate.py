"""The safety gate around Gemini-written SQL (design section 5 and 8, Phase 11 task 3).

`run_query` is the only way anything in this codebase should run SQL written by an LLM. Four
independent checks all have to pass before a single row comes back:

1. The connection to `app.db` is opened read-only (SQLite `mode=ro`): even a bug elsewhere in
   this module can't turn into a write.
2. The statement is checked to be a single `SELECT` (or `WITH ... SELECT`) before it ever
   reaches SQLite -- no semicolons, no `INSERT`/`UPDATE`/`DELETE`/`DROP`/`PRAGMA`/`ATTACH`.
3. A SQLite *authorizer* callback runs on every table, column and pragma the statement touches
   while SQLite parses it, and denies anything outside `v_documents` and `v_fields` (and the
   real tables underneath them, but only when they're reached *through* one of those views --
   never a direct read of `runs` or `sqlite_master`).
4. A *progress handler* aborts the query if it runs past a deadline, so a pathological query
   (e.g. a runaway recursive CTE) can't hang the request.

On top of all that, the query is wrapped in `SELECT * FROM (...) LIMIT 200`, so no result set
is unbounded even for a query that would otherwise return everything.

Two outcomes, two different exceptions, because the query layer (`service.py`) treats them
differently:
- `GateViolation`: the statement itself isn't allowed (wrong statement type, multiple
  statements, or the authorizer denied something). This is never sent back to Gemini for a
  retry -- design section 8's failure table treats it as an outright refusal ("query not
  allowed"), not a fixable mistake.
- `QueryExecutionError`: the statement was allowed, but SQLite couldn't run it (a syntax
  error, an unknown column, or the progress handler's timeout). This is the "if the SQL
  fails" case in design section 5 step 3 -- worth exactly one retry with the error shown to
  Gemini, before refusing.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_VIEWS: tuple[str, ...] = ("v_documents", "v_fields")
DEFAULT_ROW_LIMIT = 200
DEFAULT_TIMEOUT_SECONDS = 5.0
# How many virtual-machine instructions SQLite runs between progress-handler checks. Small
# enough that a slow query is caught quickly; large enough not to slow down a fast one.
_PROGRESS_HANDLER_GRANULARITY = 1000


class GateViolation(Exception):
    """The statement isn't allowed at all: wrong statement type, more than one statement, or
    the authorizer denied a table, column or pragma it touched. Never retried."""


class QueryExecutionError(Exception):
    """The statement was allowed, but SQLite couldn't run it: a syntax error, an unknown
    column, or the progress handler's timeout. Worth one retry with Gemini."""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]


def _reject_unless_single_select(sql: str) -> str:
    """Statement-type and statement-count checks that happen before SQLite ever sees the SQL
    (design section 5 step 2: "one statement only" and "a single `SELECT`").

    Returns the trimmed statement with its one optional trailing semicolon removed. Raises
    `GateViolation` for anything else: no semicolon may appear anywhere except at the very
    end, and the first keyword must be `SELECT` or `WITH` (for a `WITH ... SELECT` CTE).
    """
    body = sql.strip()
    if not body:
        raise GateViolation("empty SQL")

    semicolon_count = body.count(";")
    if semicolon_count > 1 or (semicolon_count == 1 and not body.endswith(";")):
        raise GateViolation("only one SQL statement is allowed")
    body = body[:-1].strip() if body.endswith(";") else body
    if not body:
        raise GateViolation("empty SQL")

    first_word = body.split(None, 1)[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise GateViolation(
            f"only a SELECT statement is allowed, not {first_word!r}"
        )
    return body


def _make_authorizer(allowed_views: tuple[str, ...]):
    """A default-deny SQLite authorizer (design section 5 step 2's authorizer, section 8's
    "blocked by the authorizer"): every action SQLite asks about is denied unless it's one of
    the specific things a read-only report query needs.

    `SQLITE_READ` (reading one column of one table or view) is the only action that depends on
    *which* table: allowed when the table itself is one of `allowed_views`, or when `source`
    (the view SQLite is currently expanding) is one of `allowed_views` -- that second case is
    what lets `v_documents` and `v_fields` be defined as ordinary views over the real
    `shipments`/`documents`/`runs`/`field_results` tables without exposing those tables to a
    direct query. Everything else that only a SELECT ever needs -- the top-level `SELECT`
    action itself, calling a SQL function, and a recursive CTE -- is allowed unconditionally.
    Every other action (`PRAGMA`, `ATTACH`, any write, any DDL, reading `sqlite_master`, a
    direct read of an underlying table) falls through to the final `SQLITE_DENY`.

    One SQLite-internal wrinkle, confirmed empirically while building this: planning a query
    over a view that joins several tables (like `v_documents` joining `shipments`,
    `documents` and `runs`) makes SQLite ask a `SQLITE_READ` about that table's bare `rowid`
    (`arg2 == ""`) with `source` set to `None`, as an optimizer check, not a real column read.
    Denying it would break every legitimate multi-table view; allowing it outright would
    reopen the "no direct read of an underlying table" hole. `SQLITE_IGNORE` is the right
    answer for exactly this shape (empty column name): SQLite still runs the statement, but
    substitutes NULL for that value instead of erroring or handing back real data -- and since
    it's the rowid, not any real column, NULL leaks nothing. A genuine direct read of a real
    column (`arg2` non-empty) never takes this branch, so `SELECT * FROM runs` is still denied.
    """

    def authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if arg1 in allowed_views or source in allowed_views:
                return sqlite3.SQLITE_OK
            if arg2 == "":
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorizer


def run_query(
    db_path: str | Path,
    sql: str,
    *,
    allowed_views: tuple[str, ...] = DEFAULT_ALLOWED_VIEWS,
    row_limit: int = DEFAULT_ROW_LIMIT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    """Run one Gemini-written SQL statement against `db_path`, through every check above.

    Raises `GateViolation` if the statement isn't allowed at all, or `QueryExecutionError` if
    it was allowed but SQLite couldn't run it (including the progress handler's timeout).
    """
    body = _reject_unless_single_select(sql)
    wrapped = f"SELECT * FROM ({body}) LIMIT {row_limit}"

    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.set_authorizer(_make_authorizer(allowed_views))

        deadline = time.monotonic() + timeout_seconds

        def _progress_handler() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_progress_handler, _PROGRESS_HANDLER_GRANULARITY)

        try:
            cursor = conn.execute(wrapped)
            columns = tuple(d[0] for d in (cursor.description or ()))
            rows = tuple(cursor.fetchall())
        except sqlite3.DatabaseError as exc:
            # `sqlite3.OperationalError` (a genuine SQL mistake, or the progress handler's
            # abort, which arrives as `OperationalError("interrupted")`) is a subclass of
            # `DatabaseError`, so one `except` catches both; the authorizer's `SQLITE_DENY`
            # surfaces as a `DatabaseError` too, but with a different message depending on
            # what was denied (confirmed empirically): a denied action like `PRAGMA`,
            # `ATTACH` or a write says "not authorized", while a denied `SQLITE_READ` of a
            # real column says "access to <table>.<column> is prohibited". Both mean the
            # same thing here -- the authorizer refused something -- so both are treated as
            # a `GateViolation`, never worth retrying (design section 8: "blocked by the
            # authorizer"). Anything else (a genuine SQL mistake or the progress handler's
            # timeout) is a `QueryExecutionError`, worth one retry (design section 5 step 3).
            message = str(exc)
            if "not authorized" in message or "is prohibited" in message:
                raise GateViolation(message) from exc
            raise QueryExecutionError(message) from exc
        return QueryResult(columns=columns, rows=rows)
    finally:
        conn.close()
