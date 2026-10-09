"""The plain-English query layer's entry point (design section 5, Phase 11).

`answer_question` is the one function the API (`POST /api/query`, Phase 9) calls. It does the
three steps design section 5 lists:

1. Ask Gemini for SQL: the question, today's date, the Monday rule, the view descriptions
   (`schema_doc.describe_views`, now covering `v_documents`, `v_fields` and
   `v_pending_shipments`, design section 5 Phase 11 task 12) and the worked examples all live
   in the `query_v2` prompt file; only the question and date change per call. Goes through the
   same `LLMClient` every other agent uses (design section 3.6) -- this module never touches a
   transport directly.
2. Run it through the gate (`gate.run_query`): read-only, single-statement, authorizer,
   progress handler, `LIMIT 200`.
3. If the gate raises `QueryExecutionError` (the SQL was allowed but SQLite couldn't run it),
   retry exactly once: send the same question back to Gemini with the error message, and try
   whatever SQL comes back. A second failure -- or a `GateViolation`, which is never worth
   retrying -- ends in a refusal that still shows the SQL and the error, per design section 8
   ("query not allowed" / the SQL that failed).

No second LLM call ever rewrites the database's own result (design section 5 step 4): the
answer shown is exactly what SQLite returned, shaped by `_shape_answer` alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from app.llm.client import LLMClient
from app.llm.prompt_files import load_prompt
from app.rules.loader import DEFAULT_RULES_DIR, RuleLoadError, load_rules_for_customer

from .gate import GateViolation, QueryExecutionError, QueryResult, run_query
from .schema import SqlAnswer
from .schema_doc import describe_views

QUERY_AGENT = "query"
QUERY_PROMPT_NAME = "query_v2"

# R12.2: a question like "...for customer <id>..." names a customer by id. Used only to tell
# "no such customer" apart from "no shipments pending for this customer" when the SQL Gemini
# wrote comes back with zero rows -- it never changes what SQL gets run.
_CUSTOMER_TOKEN_RE = re.compile(r"\bcustomer\s+([a-zA-Z0-9_-]+)", re.IGNORECASE)


def customer_exists(customer_id: str, *, rules_dir: Path = DEFAULT_RULES_DIR) -> bool:
    """Whether a rules file exists for `customer_id` at all (R12.2's "no such customer"
    case). Pure: just a filesystem check through `app.rules.loader`, nothing in `app.db`. True
    even before any shipment has ever arrived for this customer, which is exactly the
    distinction R12.2 wants from "this customer has nothing pending" (a row-count question
    against `v_pending_shipments`, answered by the SQL itself)."""
    try:
        load_rules_for_customer(customer_id.lower(), rules_dir=rules_dir)
    except RuleLoadError:
        return False
    return True


def _customer_token(question: str) -> str | None:
    """The customer id named in a question of the form "...customer <id>...", or None if the
    question doesn't name one this way. Deliberately simple: finding the customer the
    question is about is Gemini's job (it's the one writing the `WHERE customer_id = ...`
    SQL); this only recognises the common phrasing well enough to pick between the two
    answers R12.2 asks for when that SQL comes back empty."""
    match = _CUSTOMER_TOKEN_RE.search(question)
    return match.group(1) if match else None


@dataclass(frozen=True)
class QueryAnswer:
    """What `answer_question` returns. `sql` and `explanation` are always present, even on a
    refusal, so the UI can always show what was tried (design section 5 step 4 and section
    8's "Ask panel" row)."""

    kind: Literal["single_value", "table", "refused"]
    sql: str
    explanation: str
    value: object | None = None  # set when kind == "single_value"
    columns: tuple[str, ...] = ()  # set when kind == "table"
    rows: tuple[tuple, ...] = ()  # set when kind == "table"
    error: str | None = None  # set when kind == "refused"


def _shape_answer(
    result: QueryResult,
) -> tuple[Literal["single_value", "table"], object | None, tuple, tuple]:
    """Design section 5 step 4: "a single value is shown as the answer; several rows are
    shown as a table." A single row with a single column is the only case narrow enough to
    read as one value -- anything wider or taller is a table, so a one-row, multi-column
    result (e.g. the "most common mismatched field" example, which returns a field name and
    its count) is still shown as a table rather than silently dropping a column."""
    if len(result.rows) == 1 and len(result.columns) == 1:
        return "single_value", result.rows[0][0], (), ()
    return "table", None, result.columns, result.rows


def _ask_gemini(
    *,
    client: LLMClient,
    prompt_text_extra: str,
    question: str,
    today: date,
) -> SqlAnswer:
    prompt = load_prompt(QUERY_PROMPT_NAME)
    view_docs = describe_views()
    text = (
        f"Today's date: {today.isoformat()}\n\n"
        f"Views you can query:\n{view_docs}\n\n"
        f"Question: {question}"
        f"{prompt_text_extra}"
    )
    result = client.generate(
        agent=QUERY_AGENT, prompt=prompt, text=text, response_schema=SqlAnswer
    )
    return result.value


def answer_question(
    *,
    question: str,
    db_path: str | Path,
    client: LLMClient,
    today: date | None = None,
) -> QueryAnswer:
    """Answer one plain-English question against `db_path` (design section 5, all 4 steps)."""
    today = today or date.today()

    sql_answer = _ask_gemini(client=client, prompt_text_extra="", question=question, today=today)

    try:
        result = run_query(db_path, sql_answer.sql)
    except GateViolation as exc:
        return QueryAnswer(
            kind="refused",
            sql=sql_answer.sql,
            explanation=sql_answer.explanation,
            error=f"query not allowed: {exc}",
        )
    except QueryExecutionError as exc:
        retry_extra = (
            f"\n\nYour previous SQL failed with this database error:\n{exc}\n"
            f"Previous SQL:\n{sql_answer.sql}\n"
            "Fix the SQL so it runs."
        )
        retry_answer = _ask_gemini(
            client=client, prompt_text_extra=retry_extra, question=question, today=today
        )
        try:
            result = run_query(db_path, retry_answer.sql)
        except GateViolation as retry_exc:
            return QueryAnswer(
                kind="refused",
                sql=retry_answer.sql,
                explanation=retry_answer.explanation,
                error=f"query not allowed: {retry_exc}",
            )
        except QueryExecutionError as retry_exc:
            return QueryAnswer(
                kind="refused",
                sql=retry_answer.sql,
                explanation=retry_answer.explanation,
                error=str(retry_exc),
            )
        sql_answer = retry_answer

    if len(result.rows) == 0:
        token = _customer_token(question)
        if token is not None and not customer_exists(token):
            return QueryAnswer(
                kind="refused",
                sql=sql_answer.sql,
                explanation=sql_answer.explanation,
                error=f"no such customer exists: {token!r}",
            )

    kind, value, columns, rows = _shape_answer(result)
    return QueryAnswer(
        kind=kind,
        sql=sql_answer.sql,
        explanation=sql_answer.explanation,
        value=value,
        columns=columns,
        rows=rows,
    )
