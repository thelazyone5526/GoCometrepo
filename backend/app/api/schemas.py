"""Pydantic response models for the API (design section 6), matching the shapes the frontend
(`frontend/src/api/mockData.js`) already expects, since that session's UI is built against
them and only needs `USE_MOCK` flipped to `false` in `api.js` once this is live.

`field_result_from_row` and `run_detail_from_row` map `app.store.repository`'s sqlite rows
onto these models -- the one place that settles the repository's `run_id`/`id` naming
inconsistency across tables into a single `id` in every response.
"""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel


class CustomerOut(BaseModel):
    customer_id: str
    customer_name: str


class FieldResultOut(BaseModel):
    field_name: str
    value: str | None = None
    source_text: str | None = None
    page: int | None = None
    model_rating: float | None = None
    grounding: str | None = None
    ocr_reading: str | None = None
    format_ok: bool | None = None
    extraction_confidence: float | None = None
    verdict: str | None = None
    found: str | None = None
    expected: str | None = None
    rule_id: str | None = None
    verdict_reason: str | None = None
    verdict_confidence: float | None = None


class RunSummaryOut(BaseModel):
    id: str
    document_id: str
    filename: str
    # `repo.list_runs()` doesn't select `shipments.customer_id` (only `customer_name`), so
    # this is filled in with an extra `repo.get_run()` lookup in the route rather than by
    # editing the repository layer, which is out of this session's scope. See the rough-edge
    # note in `routes.py`.
    customer_id: str | None = None
    customer_name: str
    status: str
    current_step: str | None = None
    outcome: str | None = None
    started_at: str
    finished_at: str | None = None


class RunDetailOut(BaseModel):
    id: str
    document_id: str
    filename: str
    customer_id: str
    customer_name: str
    status: str
    current_step: str | None = None
    outcome: str | None = None
    reasoning: str | None = None
    decision_source: str | None = None
    override_reason: str | None = None
    escalation_reason: str | None = None
    amendment_draft: str | None = None
    llm_calls: int
    fallback_used: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    started_at: str
    finished_at: str | None = None
    field_results: list[FieldResultOut] = []


class LlmCallOut(BaseModel):
    """One row from `llm_calls` (design section 3.6): a single attempt at a single LLM call,
    whichever agent made it. `is_fallback` marks a call made against the fallback model
    rather than the primary one; `attempt` is 1 for a call's first try, 2+ for a retry of
    that same logical call."""

    agent: str
    model: str
    is_fallback: bool
    prompt_version: str
    attempt: int
    status: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: float
    error: str | None
    created_at: str


class CreateRunResponse(BaseModel):
    run_id: str | None
    status: str
    duplicate: bool


class QueryRequest(BaseModel):
    question: str


class QueryAnswerOut(BaseModel):
    """Shape matches `frontend/src/api/mockData.js`'s `mockQuery()` (design section 5 step
    4): a single value or a table's columns/rows, the SQL that produced it, and the model's
    one-sentence explanation -- always present, even on a refusal, so the Ask panel can
    always show what was tried (design section 8's "query not allowed" row)."""

    answer: object | None
    explanation: str
    sql: str | None
    columns: list[str]
    rows: list[list[object]]


def field_result_from_row(row: sqlite3.Row) -> FieldResultOut:
    """`field_results` columns map onto `FieldResultOut` one-for-one except `format_ok`,
    stored as an `INTEGER` (0/1/NULL), which becomes a real `bool | None` here."""
    return FieldResultOut(
        field_name=row["field_name"],
        value=row["value"],
        source_text=row["source_text"],
        page=row["page"],
        model_rating=row["model_rating"],
        grounding=row["grounding"],
        ocr_reading=row["ocr_reading"],
        format_ok=None if row["format_ok"] is None else bool(row["format_ok"]),
        extraction_confidence=row["extraction_confidence"],
        verdict=row["verdict"],
        found=row["found"],
        expected=row["expected"],
        rule_id=row["rule_id"],
        verdict_reason=row["verdict_reason"],
        verdict_confidence=row["verdict_confidence"],
    )


def query_answer_from_result(answer: object) -> QueryAnswerOut:
    """Maps `app.query.service.QueryAnswer` (Phase 11) onto the wire shape above. `answer`
    is typed `object` rather than importing `QueryAnswer` at module level, since `app.query`
    is a sibling package this schema module otherwise has no reason to depend on -- the
    route (`routes.py`) is the only caller, and it already imports the real type."""
    kind = answer.kind
    if kind == "single_value":
        return QueryAnswerOut(
            answer=answer.value, explanation=answer.explanation, sql=answer.sql,
            columns=[], rows=[],
        )
    if kind == "table":
        return QueryAnswerOut(
            answer=None,
            explanation=answer.explanation,
            sql=answer.sql,
            columns=list(answer.columns),
            rows=[list(row) for row in answer.rows],
        )
    # "refused" (design section 8: "query not allowed" / the SQL that failed).
    return QueryAnswerOut(
        answer=answer.error, explanation=answer.explanation, sql=answer.sql, columns=[], rows=[]
    )


def llm_call_from_row(row: sqlite3.Row) -> LlmCallOut:
    return LlmCallOut(
        agent=row["agent"],
        model=row["model"],
        is_fallback=bool(row["is_fallback"]),
        prompt_version=row["prompt_version"],
        attempt=row["attempt"],
        status=row["status"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        thinking_tokens=row["thinking_tokens"],
        latency_ms=row["latency_ms"],
        error=row["error"],
        created_at=row["created_at"],
    )


def run_detail_from_row(row: sqlite3.Row, field_rows: list[sqlite3.Row]) -> RunDetailOut:
    """`repo.get_run()`'s row already joins in `filename`, `customer_name` and `customer_id`,
    so unlike the list endpoint this needs no extra lookup."""
    return RunDetailOut(
        id=row["id"],
        document_id=row["document_id"],
        filename=row["filename"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        status=row["status"],
        current_step=row["current_step"],
        outcome=row["outcome"],
        reasoning=row["reasoning"],
        decision_source=row["decision_source"],
        override_reason=row["override_reason"],
        escalation_reason=row["escalation_reason"],
        amendment_draft=row["amendment_draft"],
        llm_calls=row["llm_calls"],
        fallback_used=bool(row["fallback_used"]),
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        latency_ms=row["latency_ms"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        field_results=[field_result_from_row(r) for r in field_rows],
    )
