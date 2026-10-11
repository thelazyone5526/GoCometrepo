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
    return conn.execute("SELECT * FROM field_results WHERE run_id = ?", (run_id,)).fetchall()


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


# --- part 2: emails, shipment reviews, cross-checks, drafts and the outbox ---------------------
# Part 2 design section 3. Same rule as above: every read and write to the Part 2 tables goes
# through this module. These functions only ever INSERT new rows (R12.6) -- the Part 1 tables
# gain rows here (`shipments`, `documents`) but no Part 1 row is ever modified.

ReviewState = Literal[
    "pending_review", "approved_sent", "amendment_sent", "reviewed_no_send", "failed"
]
AttachmentStatus = Literal["accepted", "duplicate", "rejected"]
DocumentOrigin = Literal["own_email", "carried_over"]
CrossCheckVerdict = Literal["consistent", "inconsistent", "uncertain", "not_applicable"]
DraftKind = Literal["approval", "amendment", "empty"]
DraftSource = Literal["generated", "operator_edit"]


# --- emails and attachments --------------------------------------------------------------------


def record_email(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    eml_path: str,
    sender: str | None,
    subject: str | None,
    received_at: str | None,
    attachment_count: int,
    attachment_filenames: list[str],
) -> str:
    """Record one `.eml` before any of its attachments is processed (R1.2), and return its id.

    `emails.message_id` is UNIQUE, so a rescan of the same file after a restart raises
    `sqlite3.IntegrityError` here rather than creating a second shipment (R1.3). Callers that
    expect a rescan check `find_email_by_message_id` first; the index is the real guarantee.
    """
    email_id = new_id()
    conn.execute(
        """
        INSERT INTO emails
            (id, message_id, eml_path, sender, subject, received_at, attachment_count,
             attachment_filenames, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            email_id,
            message_id,
            eml_path,
            sender,
            subject,
            received_at,
            attachment_count,
            json.dumps(attachment_filenames),
            now_iso(),
        ),
    )
    conn.commit()
    return email_id


def find_email_by_message_id(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row | None:
    """The watcher's skip lookup (R1.3): an email already recorded is not processed again."""
    return conn.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,)).fetchone()


def record_attachment(
    conn: sqlite3.Connection,
    *,
    email_id: str,
    filename: str,
    file_hash: str | None,
    status: AttachmentStatus,
    reason: str | None,
    document_id: str | None,
) -> None:
    """Record every attachment, including the ones that never became documents (R2.1, R2.2).

    A rejected attachment keeps its reason so the screen can say why, rather than the file
    simply vanishing. A byte-identical duplicate points at the document it duplicates.
    """
    conn.execute(
        """
        INSERT INTO shipment_attachments
            (id, email_id, filename, file_hash, status, reason, document_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id(), email_id, filename, file_hash, status, reason, document_id, now_iso()),
    )
    conn.commit()


def list_attachments(conn: sqlite3.Connection, email_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM shipment_attachments WHERE email_id = ? ORDER BY created_at, filename",
        (email_id,),
    ).fetchall()


# --- shipments and their documents -------------------------------------------------------------


def create_shipment_for_email(
    conn: sqlite3.Connection,
    *,
    customer_id: str,
    customer_name: str,
    reference: str | None,
    email_id: str,
    cycle_number: int,
    parent_shipment_id: str | None,
    review_state: ReviewState,
    failure_reason: str | None = None,
) -> str:
    """One email becomes one shipment: a `shipments` row (Part 1 table, new row only, this time
    with `reference` actually set -- the column existed and went unused in Part 1) plus its
    `shipment_reviews` row carrying the Part 2 state. Both in one transaction, so a crash
    between them can't leave a shipment with no review row.

    A failed email still gets its shipment (R1.5, R1.6): `review_state = 'failed'` with the
    reason, so it appears in the list rather than disappearing silently.
    """
    shipment_id = new_id()
    now = now_iso()
    conn.execute(
        "INSERT INTO shipments (id, customer_id, customer_name, reference, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (shipment_id, customer_id, customer_name, reference, now),
    )
    conn.execute(
        """
        INSERT INTO shipment_reviews
            (id, shipment_id, email_id, reference, cycle_number, parent_shipment_id, outcome,
             reasoning, discrepancy_count, uncertain_count, review_state, failure_reason,
             operator_decision, operator_decided_at, created_at, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, 0, ?, ?, NULL, NULL, ?, NULL)
        """,
        (
            new_id(),
            shipment_id,
            email_id,
            reference,
            cycle_number,
            parent_shipment_id,
            review_state,
            failure_reason,
            now,
        ),
    )
    conn.commit()
    return shipment_id


def create_document_for_shipment(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    filename: str,
    file_hash: str,
    mime_type: str,
    page_count: int,
    has_text_layer: bool,
    stored_path: Path,
    doc_type: str | None,
) -> DocumentRecord:
    """The missing sibling of `create_shipment_and_document`: a `documents` row against an
    **existing** shipment, because Part 2 attaches several documents to one shipment (design
    section 4: "no schema change").

    Unlike Part 1, this writes `doc_type`. Part 1 always left that column NULL; filling it for
    Part 2 rows is decision 3 of the Part 2 design (the column was built for exactly this, and
    `v_documents` / `v_fields` already select it, so the query layer gains document types for
    free). No Part 1 test asserts it is NULL.
    """
    document_id = new_id()
    conn.execute(
        """
        INSERT INTO documents
            (id, shipment_id, filename, file_hash, mime_type, page_count, has_text_layer,
             stored_path, doc_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            doc_type,
            now_iso(),
        ),
    )
    conn.commit()
    return DocumentRecord(
        id=document_id,
        shipment_id=shipment_id,
        file_hash=file_hash,
        stored_path=str(stored_path),
    )


def find_document_by_hash_in_chain(
    conn: sqlite3.Connection, *, shipment_ids: list[str], file_hash: str
) -> DocumentRecord | None:
    """R2.2: an attachment byte-identical to one already in this resubmission chain reuses that
    document instead of being processed again. `shipment_ids` is the chain (this shipment plus
    its ancestors); an empty chain can match nothing, so it answers None without a query."""
    if not shipment_ids:
        return None
    placeholders = ",".join("?" for _ in shipment_ids)
    row = conn.execute(
        f"""
        SELECT id, shipment_id, file_hash, stored_path
        FROM documents
        WHERE file_hash = ? AND shipment_id IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (file_hash, *shipment_ids),
    ).fetchone()
    if row is None:
        return None
    return DocumentRecord(
        id=row["id"],
        shipment_id=row["shipment_id"],
        file_hash=row["file_hash"],
        stored_path=row["stored_path"],
    )


def add_shipment_document(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    document_id: str,
    run_id: str | None,
    doc_type: str | None,
    origin: DocumentOrigin,
    source_shipment_id: str | None,
    source_cycle: int | None,
) -> None:
    """Materialise one entry of the effective set (R5.2-R5.4).

    `INSERT OR IGNORE` against `UNIQUE(shipment_id, document_id)`: the set is rebuilt after a
    rerun and after a restart-resume, so this is called again for pairs already stored, and
    calling it twice must leave one row, not two (R5.3). Ignoring the conflict keeps the first
    recorded origin, which is the one the operator was shown.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO shipment_documents
            (id, shipment_id, document_id, run_id, doc_type, origin, source_shipment_id,
             source_cycle, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            shipment_id,
            document_id,
            run_id,
            doc_type,
            origin,
            source_shipment_id,
            source_cycle,
            now_iso(),
        ),
    )
    conn.commit()


def update_shipment_document_run(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    document_id: str,
    run_id: str,
    doc_type: str | None,
) -> None:
    """Point an existing effective-set entry at a new run (requirement 13.3: a rerun of one
    failed document). `add_shipment_document`'s `INSERT OR IGNORE` deliberately keeps the
    first-recorded row on a conflict (design section 2), so it cannot be reused here -- a
    rerun needs the *same* `(shipment_id, document_id)` row to end up pointing at the new
    `run_id` and `doc_type`, not a second row next to the old one. This is the one other write
    against `ix_shipment_documents_once`'s row shape, kept beside `add_shipment_document` so
    both callers of that index are visible together."""
    conn.execute(
        """
        UPDATE shipment_documents SET run_id = ?, doc_type = ?
        WHERE shipment_id = ? AND document_id = ?
        """,
        (run_id, doc_type, shipment_id, document_id),
    )
    conn.commit()


def update_document_doc_type(conn: sqlite3.Connection, *, document_id: str, doc_type: str) -> None:
    """Write back the Doc_Type the Extractor reported, once the Part 1 pipeline has actually
    run (requirement 2.3). `create_document_for_shipment` only sets `doc_type` once, at
    creation, when it is usually still unknown -- this is the one other place it changes.
    Promoted from `inbox/watcher.py`'s private `_set_document_doc_type` once the rerun route
    needed the same statement (D8 in `docs/part2-decisions.md`); no behaviour change from the
    original."""
    conn.execute("UPDATE documents SET doc_type = ? WHERE id = ?", (doc_type, document_id))
    conn.commit()


def list_shipment_documents(conn: sqlite3.Connection, shipment_id: str) -> list[sqlite3.Row]:
    """The effective set, with each document's filename and its run's status, in the stable
    order the cross-checker sorts by (R3.1: input order cannot change the answer)."""
    return conn.execute(
        """
        SELECT sd.*, d.filename, d.stored_path, d.file_hash, d.page_count,
               r.status AS run_status, r.outcome AS run_outcome,
               r.escalation_reason AS run_escalation_reason,
               r.current_step AS run_current_step
        FROM shipment_documents sd
        JOIN documents d ON d.id = sd.document_id
        LEFT JOIN runs r ON r.id = sd.run_id
        WHERE sd.shipment_id = ?
        ORDER BY sd.doc_type, d.filename, sd.document_id
        """,
        (shipment_id,),
    ).fetchall()


def find_latest_shipment_by_reference(
    conn: sqlite3.Connection, *, customer_id: str, reference: str
) -> sqlite3.Row | None:
    """The most recent cycle for a `SHP-` reference (R5.1): what a resubmission attaches to, and
    where the effective-set builder starts walking `parent_shipment_id` backwards."""
    return conn.execute(
        """
        SELECT sr.*, s.customer_id, s.customer_name
        FROM shipment_reviews sr
        JOIN shipments s ON s.id = sr.shipment_id
        WHERE s.customer_id = ? AND sr.reference = ?
        ORDER BY sr.cycle_number DESC, sr.created_at DESC
        LIMIT 1
        """,
        (customer_id, reference),
    ).fetchone()


_SHIPMENT_SELECT = """
SELECT
    sr.id AS review_id, sr.shipment_id, sr.email_id, sr.reference, sr.cycle_number,
    sr.parent_shipment_id, sr.outcome, sr.reasoning, sr.discrepancy_count,
    sr.uncertain_count, sr.review_state, sr.failure_reason, sr.operator_decision,
    sr.operator_decided_at, sr.created_at, sr.decided_at,
    s.customer_id, s.customer_name,
    e.message_id, e.eml_path, e.sender, e.subject, e.received_at, e.attachment_count,
    e.attachment_filenames,
    (SELECT COUNT(*) FROM shipment_documents sd
        WHERE sd.shipment_id = sr.shipment_id) AS document_count
FROM shipment_reviews sr
JOIN shipments s ON s.id = sr.shipment_id
JOIN emails e ON e.id = sr.email_id
"""


def list_shipments(conn: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    """State 1's list (R8.1-R8.4). Newest first, every review_state included -- a failed
    shipment must still be visible (R13.1)."""
    return conn.execute(
        f"{_SHIPMENT_SELECT} ORDER BY sr.created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def get_shipment(conn: sqlite3.Connection, shipment_id: str) -> sqlite3.Row | None:
    return conn.execute(f"{_SHIPMENT_SELECT} WHERE sr.shipment_id = ?", (shipment_id,)).fetchone()


def set_shipment_outcome(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    outcome: str,
    reasoning: str,
    discrepancy_count: int,
    uncertain_count: int,
) -> None:
    """Store the shipment-level decision `decide_shipment` computed in plain code (R4.1-R4.6).
    `decided_at` is stamped here, so a shipment with an outcome always has a time to show."""
    conn.execute(
        """
        UPDATE shipment_reviews SET
            outcome = ?, reasoning = ?, discrepancy_count = ?, uncertain_count = ?,
            decided_at = ?
        WHERE shipment_id = ?
        """,
        (outcome, reasoning, discrepancy_count, uncertain_count, now_iso(), shipment_id),
    )
    conn.commit()


def set_review_state(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    review_state: ReviewState,
    failure_reason: str | None = None,
) -> None:
    """Move a shipment between the five review states. `failure_reason` is passed only with
    `failed`, and is stored so the list can show a plain-language reason (R13.1)."""
    conn.execute(
        "UPDATE shipment_reviews SET review_state = ?, failure_reason = ? WHERE shipment_id = ?",
        (review_state, failure_reason, shipment_id),
    )
    conn.commit()


def record_operator_decision(conn: sqlite3.Connection, *, shipment_id: str, decision: str) -> None:
    """R7.6: record what the operator decided, and **only** that.

    This writes `operator_decision` and `operator_decided_at` and nothing else. The system's own
    `outcome` and `reasoning` are never overwritten, so the audit trail always shows both what
    the pipeline concluded and what the person did about it -- including when they disagreed.
    """
    conn.execute(
        """
        UPDATE shipment_reviews SET operator_decision = ?, operator_decided_at = ?
        WHERE shipment_id = ?
        """,
        (decision, now_iso(), shipment_id),
    )
    conn.commit()


# --- cross-document checks ---------------------------------------------------------------------


def upsert_cross_check_result(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    field_name: str,
    result: CrossCheckVerdict,
    reason: str | None,
    compared: list[dict],
) -> None:
    """Store one field's cross-document verdict (R3.1-R3.6).

    Upsert, not insert: after a document is rerun the cross-check runs again over the effective
    set, and the field must end with one current row, not an append-only history the screen would
    have to disambiguate (R13.3). `UNIQUE(shipment_id, field_name)` is what the conflict clause
    keys on.
    """
    conn.execute(
        """
        INSERT INTO cross_check_results
            (id, shipment_id, field_name, result, reason, compared, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shipment_id, field_name) DO UPDATE SET
            result = excluded.result,
            reason = excluded.reason,
            compared = excluded.compared,
            created_at = excluded.created_at
        """,
        (
            new_id(),
            shipment_id,
            field_name,
            result,
            reason,
            json.dumps(compared),
            now_iso(),
        ),
    )
    conn.commit()


def list_cross_check_results(conn: sqlite3.Connection, shipment_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cross_check_results WHERE shipment_id = ? ORDER BY field_name",
        (shipment_id,),
    ).fetchall()


# --- drafts, versions and the outbox -----------------------------------------------------------


def create_draft(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    kind: DraftKind,
    render_error: str | None,
    recipient: str | None,
    subject: str | None,
    body: str | None,
) -> tuple[str, str]:
    """Create a shipment's draft and its version 1, returning `(draft_id, version_id)`.

    Version 1 is always the generated text, stored unchanged, because "has the operator edited
    this?" and the reset button are both answered by comparing v1 with the latest version
    (R6.6, R11.1). A render failure still gets a draft row (`kind = 'empty'` with
    `render_error`), so the operator gets an editable draft and the reason rather than a blank
    screen (R6.5).
    """
    draft_id = new_id()
    version_id = new_id()
    now = now_iso()
    conn.execute(
        "INSERT INTO drafts (id, shipment_id, kind, render_error, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (draft_id, shipment_id, kind, render_error, now),
    )
    conn.execute(
        """
        INSERT INTO draft_versions
            (id, draft_id, version_number, recipient, subject, body, source, created_at)
        VALUES (?, ?, 1, ?, ?, ?, 'generated', ?)
        """,
        (version_id, draft_id, recipient, subject, body, now),
    )
    conn.commit()
    return draft_id, version_id


def add_draft_version(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    recipient: str | None,
    subject: str | None,
    body: str | None,
    source: DraftSource,
) -> str:
    """Append a version (an operator edit, R11.1) and return its id. The number is max + 1, so
    nothing is overwritten and the generated version 1 stays readable for the reset."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next "
        "FROM draft_versions WHERE draft_id = ?",
        (draft_id,),
    ).fetchone()
    version_id = new_id()
    conn.execute(
        """
        INSERT INTO draft_versions
            (id, draft_id, version_number, recipient, subject, body, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (version_id, draft_id, row["next"], recipient, subject, body, source, now_iso()),
    )
    conn.commit()
    return version_id


def get_draft(conn: sqlite3.Connection, shipment_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM drafts WHERE shipment_id = ?", (shipment_id,)).fetchone()


def list_draft_versions(conn: sqlite3.Connection, draft_id: str) -> list[sqlite3.Row]:
    """Every version in order: `[0]` is the generated text, `[-1]` is what the operator sees."""
    return conn.execute(
        "SELECT * FROM draft_versions WHERE draft_id = ? ORDER BY version_number",
        (draft_id,),
    ).fetchall()


def insert_outbox(
    conn: sqlite3.Connection,
    *,
    shipment_id: str,
    draft_id: str,
    draft_version_id: str,
    recipient: str,
    subject: str | None,
    body: str,
) -> str:
    """Write the one simulated sent message for a shipment, returning its id.

    No pre-check for an existing row: `UNIQUE(shipment_id)` means a second send raises
    `sqlite3.IntegrityError`, which the single send path turns into "already sent" (R7.2). A
    `SELECT` first would leave a window where two requests both see nothing and both insert;
    letting the database refuse the write closes it. This function does not mark anything sent
    and sends no mail -- Part 2 has exactly one send path, built on top of this (R7.1, R15.1).
    """
    outbox_id = new_id()
    conn.execute(
        """
        INSERT INTO outbox
            (id, shipment_id, draft_id, draft_version_id, recipient, subject, body, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (outbox_id, shipment_id, draft_id, draft_version_id, recipient, subject, body, now_iso()),
    )
    conn.commit()
    return outbox_id


def get_outbox(conn: sqlite3.Connection, shipment_id: str) -> sqlite3.Row | None:
    """The sent state the draft screen shows: present means sent, with `sent_at` (R11.3)."""
    return conn.execute("SELECT * FROM outbox WHERE shipment_id = ?", (shipment_id,)).fetchone()
