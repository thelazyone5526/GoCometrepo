"""The repository (design section 4 and 9): every read and write to `app.db` goes through
here, so nothing else in the codebase writes raw SQL against the tables directly.

Kept as plain functions taking a connection, not a class wrapping one -- there's no per-call
state to hold onto, and it keeps the CLI (and, later, the API) free to manage connection
lifetime however fits their own context (one per script run, one per request).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Status = Literal["processing", "completed"]
Outcome = Literal["auto_approved", "human_review", "amendment_requested"]

# design section 10.1: outcome names differ between design §3.5 (Router) and §4 (`runs.outcome`).
# This is the one place that mapping happens.
_OUTCOME_MAP: dict[str, Outcome] = {
    "auto_approve": "auto_approved",
    "human_review": "human_review",
    "amendment_request": "amendment_requested",
}


def to_stored_outcome(router_outcome: str) -> Outcome:
    """Map a Router outcome name (design section 3.5) to the stored `runs.outcome` name
    (design section 4). See design section 10.1's open item, settled here."""
    return _OUTCOME_MAP[router_outcome]


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- shipments and documents ---------------------------------------------------------------


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    shipment_id: str
    file_hash: str
    stored_path: str


def find_document_by_hash(
    conn: sqlite3.Connection, *, customer_id: str, file_hash: str
) -> DocumentRecord | None:
    """design section 3.7: "File hash + customer ID: re-uploading the same file returns the
    existing run, unless the request asks for a rerun." This is the lookup that check feeds."""
    row = conn.execute(
        """
        SELECT d.id, d.shipment_id, d.file_hash, d.stored_path
        FROM documents d
        JOIN shipments s ON s.id = d.shipment_id
        WHERE s.customer_id = ? AND d.file_hash = ?
        ORDER BY d.created_at DESC
        LIMIT 1
        """,
        (customer_id, file_hash),
    ).fetchone()
    if row is None:
        return None
    return DocumentRecord(
        id=row["id"],
        shipment_id=row["shipment_id"],
        file_hash=row["file_hash"],
        stored_path=row["stored_path"],
    )


def find_latest_run_for_document(conn: sqlite3.Connection, *, document_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM runs WHERE document_id = ? ORDER BY started_at DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    return row["id"] if row else None


def create_shipment_and_document(
    conn: sqlite3.Connection,
    *,
    customer_id: str,
    customer_name: str,
    filename: str,
    file_hash: str,
    mime_type: str,
    page_count: int,
    has_text_layer: bool,
    stored_path: Path,
) -> DocumentRecord:
    """Part 1 always creates one shipment per document (design section 4: "each upload
    creates one shipment with one document. Part 2 attaches several documents to one
    shipment, with no schema change")."""
    shipment_id = new_id()
    document_id = new_id()
    now = now_iso()
    conn.execute(
        "INSERT INTO shipments (id, customer_id, customer_name, reference, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (shipment_id, customer_id, customer_name, now),
    )
    conn.execute(
        """
        INSERT INTO documents
            (id, shipment_id, filename, file_hash, mime_type, page_count, has_text_layer,
             stored_path, doc_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            document_id,
            shipment_id,
            filename,
            file_hash,
            mime_type,
            page_count,
            int(has_text_layer),
            str(stored_path),
            now,
        ),
    )
    conn.commit()
    return DocumentRecord(
        id=document_id, shipment_id=shipment_id, file_hash=file_hash, stored_path=str(stored_path)
    )


# --- runs -------------------------------------------------------------------------------------


def create_run(conn: sqlite3.Connection, *, run_id: str, document_id: str) -> None:
    conn.execute(
        """
        INSERT INTO runs (id, document_id, status, current_step, llm_calls, fallback_used,
                          input_tokens, output_tokens, cost_usd, latency_ms, started_at)
        VALUES (?, ?, 'processing', 'prepare', 0, 0, 0, 0, 0, 0, ?)
        """,
        (run_id, document_id, now_iso()),
    )
    conn.commit()


def update_run_step(conn: sqlite3.Connection, *, run_id: str, step: str) -> None:
    conn.execute("UPDATE runs SET current_step = ? WHERE id = ?", (step, run_id))
    conn.commit()


def complete_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    outcome: Outcome,
    reasoning: str,
    decision_source: str,
    override_reason: str | None,
    amendment_draft: str | None,
    llm_calls: int,
    fallback_used: bool,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: float,
) -> None:
    conn.execute(
        """
        UPDATE runs SET
            status = 'completed', current_step = 'persist', outcome = ?, reasoning = ?,
            decision_source = ?, override_reason = ?, amendment_draft = ?, llm_calls = ?,
            fallback_used = ?, input_tokens = ?, output_tokens = ?, cost_usd = ?,
            latency_ms = ?, finished_at = ?
        WHERE id = ?
        """,
        (
            outcome,
            reasoning,
            decision_source,
            override_reason,
            amendment_draft,
            llm_calls,
            int(fallback_used),
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            now_iso(),
            run_id,
        ),
    )
    conn.commit()


def escalate_run(conn: sqlite3.Connection, *, run_id: str, reason: str) -> None:
    """design section 3.1: any node that hits an error jumps to `escalate`, which always
    means human_review with the reason recorded -- never a silent failure."""
    conn.execute(
        """
        UPDATE runs SET
            status = 'completed', current_step = 'escalate', outcome = 'human_review',
            escalation_reason = ?, decision_source = 'fallback', finished_at = ?
        WHERE id = ?
        """,
        (reason, now_iso(), run_id),
    )
    conn.commit()


def list_processing_runs(conn: sqlite3.Connection) -> list[str]:
    """design section 3.7 / Phase 8: `resume_incomplete_runs()`'s lookup -- every run still
    marked `processing`, to be continued from its last checkpoint on start-up."""
    rows = conn.execute("SELECT id FROM runs WHERE status = 'processing'").fetchall()
    return [row["id"] for row in rows]


def list_runs(conn: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, d.filename, s.customer_name
        FROM runs r
        JOIN documents d ON d.id = r.document_id
        JOIN shipments s ON s.id = d.shipment_id
        ORDER BY r.started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.*, d.filename, d.stored_path, d.doc_type, s.customer_name, s.customer_id
        FROM runs r
        JOIN documents d ON d.id = r.document_id
        JOIN shipments s ON s.id = d.shipment_id
        WHERE r.id = ?
        """,
        (run_id,),
    ).fetchone()


# --- field_results -----------------------------------------------------------------------------


def insert_field_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    field_name: str,
    value: str | None,
    source_text: str | None,
    page: int | None,
    box: tuple[float, float, float, float] | None,
    model_rating: float | None,
    grounding: str | None,
    ocr_reading: str | None,
    format_ok: bool | None,
    extraction_confidence: float | None,
    verdict: str | None,
    found: str | None,
    expected: str | None,
    rule_id: str | None,
    verdict_reason: str | None,
    verdict_confidence: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO field_results
            (id, run_id, field_name, value, source_text, page, box, model_rating, grounding,
             ocr_reading, format_ok, extraction_confidence, verdict, found, expected, rule_id,
             verdict_reason, verdict_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            run_id,
            field_name,
            value,
            source_text,
            page,
            json.dumps(list(box)) if box is not None else None,
            model_rating,
            grounding,
            ocr_reading,
            None if format_ok is None else int(format_ok),
            extraction_confidence,
            verdict,
            found,
            expected,
            rule_id,
            verdict_reason,
            verdict_confidence,
        ),
    )
    conn.commit()


def list_field_results(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM field_results WHERE run_id = ?", (run_id,)
    ).fetchall()


# --- llm_calls ----------------------------------------------------------------------------------


def insert_llm_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    agent: str,
    model: str,
    is_fallback: bool,
    prompt_version: str,
    attempt: int,
    status: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int,
    latency_ms: float,
    error: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls
            (id, run_id, agent, model, is_fallback, prompt_version, attempt, status,
             input_tokens, output_tokens, thinking_tokens, latency_ms, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            run_id,
            agent,
            model,
            int(is_fallback),
            prompt_version,
            attempt,
            status,
            input_tokens,
            output_tokens,
            thinking_tokens,
            latency_ms,
            error,
            now_iso(),
        ),
    )
    conn.commit()


def list_llm_calls(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY created_at", (run_id,)
    ).fetchall()
