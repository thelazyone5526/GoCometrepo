"""The `app.db` schema (design section 4): tables plus the two read-only query views.

Kept as one file of plain `CREATE TABLE` / `CREATE VIEW` SQL, run once at start-up
(`init_db`), so the schema is exactly what's in the design doc and easy to read end to end.
SQLite has no real migration story worth building for Part 1 -- `data/app.db` is git-ignored
and can just be deleted to reset.
"""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shipments (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    reference TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    has_text_layer INTEGER NOT NULL,
    stored_path TEXT NOT NULL,
    doc_type TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_hash_customer
    ON documents(file_hash, shipment_id);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    status TEXT NOT NULL,                 -- processing, completed
    current_step TEXT,
    outcome TEXT,                         -- auto_approved, human_review, amendment_requested
    reasoning TEXT,
    decision_source TEXT,                 -- llm, code_override, fallback
    override_reason TEXT,
    escalation_reason TEXT,
    amendment_draft TEXT,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    -- Reserved and empty in Part 1 (design section 4): the online-metric columns.
    reviewed_by TEXT,
    final_outcome TEXT,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS field_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    field_name TEXT NOT NULL,
    value TEXT,
    source_text TEXT,
    page INTEGER,
    box TEXT,                             -- JSON [x0, y0, x1, y1], null if not grounded
    model_rating REAL,
    grounding TEXT,                       -- exact, near, not_found, absent
    ocr_reading TEXT,
    format_ok INTEGER,
    extraction_confidence REAL,
    verdict TEXT,                         -- match, mismatch, uncertain, not_applicable
    found TEXT,
    expected TEXT,
    rule_id TEXT,
    verdict_reason TEXT,
    verdict_confidence REAL
);

CREATE INDEX IF NOT EXISTS ix_field_results_run ON field_results(run_id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    is_fallback INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    thinking_tokens INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_llm_calls_run ON llm_calls(run_id);

-- ---------------------------------------------------------------------------------------
-- Part 2 (part2-shipment-workflow design section 3): the email trigger, the shipment layer
-- and the reply layer. New tables only -- R12.6: no Part 1 column is added, altered or
-- dropped, and no existing row is modified, so every Part 1 behaviour is preserved.
--
-- Three of the unique indexes below are load-bearing safety guarantees, not conveniences:
--   * ix_emails_message_id        -- the restart guarantee (R1.3). A rescan of the inbox
--                                    cannot create a second shipment for the same email,
--                                    because the database refuses the row.
--   * ix_outbox_shipment          -- the double-send guarantee (R7.2). A second send is
--                                    refused by storage, not by an `if` someone can delete.
--   * ix_shipment_documents_once  -- the at-most-once guarantee (R5.3). A document appears
--                                    in a shipment's effective set exactly once, however
--                                    many times the builder runs.
-- Those three are relied on by code that catches the IntegrityError instead of pre-checking,
-- so removing them would silently turn a refused write into a duplicate one.
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    eml_path TEXT NOT NULL,
    sender TEXT,
    subject TEXT,
    received_at TEXT,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    attachment_filenames TEXT,            -- JSON array of filenames, as they arrived
    recorded_at TEXT NOT NULL
);

-- Load-bearing (R1.3): the watcher records the email before processing any attachment, so
-- this index is what makes a rescan after a restart a no-op rather than a duplicate run.
CREATE UNIQUE INDEX IF NOT EXISTS ix_emails_message_id ON emails(message_id);

CREATE TABLE IF NOT EXISTS shipment_reviews (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    email_id TEXT NOT NULL REFERENCES emails(id),
    reference TEXT,                       -- the SHP- reference parsed from the subject
    cycle_number INTEGER NOT NULL DEFAULT 1,
    parent_shipment_id TEXT REFERENCES shipments(id),
    outcome TEXT,                         -- auto_approve, human_review, amendment_request
    reasoning TEXT,
    discrepancy_count INTEGER NOT NULL DEFAULT 0,
    uncertain_count INTEGER NOT NULL DEFAULT 0,
    -- pending_review, approved_sent, amendment_sent, reviewed_no_send, failed
    review_state TEXT NOT NULL,
    failure_reason TEXT,
    -- R7.6: the operator's own call, stored beside the system's `outcome`, never over it.
    operator_decision TEXT,
    operator_decided_at TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_shipment_reviews_shipment
    ON shipment_reviews(shipment_id);

CREATE TABLE IF NOT EXISTS shipment_attachments (
    id TEXT PRIMARY KEY,
    email_id TEXT NOT NULL REFERENCES emails(id),
    filename TEXT NOT NULL,
    file_hash TEXT,
    status TEXT NOT NULL,                 -- accepted, duplicate, rejected
    reason TEXT,
    document_id TEXT REFERENCES documents(id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_shipment_attachments_email ON shipment_attachments(email_id);

CREATE TABLE IF NOT EXISTS shipment_documents (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    run_id TEXT REFERENCES runs(id),
    doc_type TEXT,
    origin TEXT NOT NULL,                 -- own_email, carried_over
    source_shipment_id TEXT,
    source_cycle INTEGER,
    created_at TEXT NOT NULL
);

-- Load-bearing (R5.3): the at-most-once guarantee for the effective set. `add_shipment_document`
-- relies on it to be safe to call again after a rerun or a resume.
CREATE UNIQUE INDEX IF NOT EXISTS ix_shipment_documents_once
    ON shipment_documents(shipment_id, document_id);

CREATE TABLE IF NOT EXISTS cross_check_results (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    field_name TEXT NOT NULL,
    result TEXT NOT NULL,                 -- consistent, inconsistent, uncertain, not_applicable
    reason TEXT,
    -- JSON array of objects: document_id, filename, doc_type, value, normalised, source_text,
    -- page, field_result_id. This is what the discrepancy screen shows (R3.6, R10.3).
    compared TEXT,
    created_at TEXT NOT NULL
);

-- A rerun must replace a field's verdict, not append a second one (R13.3).
CREATE UNIQUE INDEX IF NOT EXISTS ix_cross_check_results_field
    ON cross_check_results(shipment_id, field_name);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    kind TEXT NOT NULL,                   -- approval, amendment, empty
    render_error TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_drafts_shipment ON drafts(shipment_id);

CREATE TABLE IF NOT EXISTS draft_versions (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    version_number INTEGER NOT NULL,
    recipient TEXT,
    subject TEXT,
    body TEXT,
    source TEXT NOT NULL,                 -- generated, operator_edit
    created_at TEXT NOT NULL
);

-- Version 1 is the generated text, kept unchanged; each operator edit is a new row. v1 vs
-- latest is what drives the "edited" markers and the reset button (R6.6, R11.1).
CREATE UNIQUE INDEX IF NOT EXISTS ix_draft_versions_number
    ON draft_versions(draft_id, version_number);

CREATE INDEX IF NOT EXISTS ix_draft_versions_draft ON draft_versions(draft_id);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    draft_id TEXT NOT NULL REFERENCES drafts(id),
    draft_version_id TEXT NOT NULL REFERENCES draft_versions(id),
    recipient TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

-- Load-bearing (R7.2, R15.1): one sent message per shipment, enforced by the database. The
-- single send path catches the violation rather than checking first, so a race cannot send twice.
CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_shipment ON outbox(shipment_id);

-- design section 4: the only tables the query layer (Phase 11) may read, and only through
-- these views, never the raw tables directly.
CREATE VIEW IF NOT EXISTS v_documents AS
SELECT
    r.id AS run_id,
    s.customer_name AS customer_name,
    d.doc_type AS doc_type,
    d.filename AS filename,
    r.outcome AS outcome,
    r.reasoning AS reasoning,
    r.decision_source AS decision_source,
    (SELECT COUNT(*) FROM field_results fr
        WHERE fr.run_id = r.id AND fr.verdict = 'match') AS matched_count,
    (SELECT COUNT(*) FROM field_results fr
        WHERE fr.run_id = r.id AND fr.verdict = 'mismatch') AS mismatched_count,
    (SELECT COUNT(*) FROM field_results fr
        WHERE fr.run_id = r.id AND fr.verdict = 'uncertain') AS uncertain_count,
    r.fallback_used AS fallback_used,
    r.cost_usd AS cost_usd,
    r.latency_ms AS latency_ms,
    r.started_at AS started_at,
    r.finished_at AS finished_at
FROM runs r
JOIN documents d ON d.id = r.document_id
JOIN shipments s ON s.id = d.shipment_id;

CREATE VIEW IF NOT EXISTS v_fields AS
SELECT
    fr.id AS field_result_id,
    fr.run_id AS run_id,
    s.customer_name AS customer_name,
    d.filename AS filename,
    d.doc_type AS doc_type,
    r.outcome AS outcome,
    fr.field_name AS field_name,
    fr.value AS value,
    fr.found AS found,
    fr.expected AS expected,
    fr.verdict AS verdict,
    fr.verdict_reason AS verdict_reason,
    fr.rule_id AS rule_id,
    fr.extraction_confidence AS extraction_confidence,
    fr.verdict_confidence AS verdict_confidence
FROM field_results fr
JOIN runs r ON r.id = fr.run_id
JOIN documents d ON d.id = r.document_id
JOIN shipments s ON s.id = d.shipment_id;

-- Part 2, R12.1 and R12.4: the third and last read-only view -- one row per Part 2 shipment.
-- `GET /api/shipments` reads the same rows, so the screen and the plain-English query layer
-- can never disagree about what is pending.
--
-- Deliberately unfiltered: every review_state is carried, not just pending_review, and the
-- caller filters. That is what lets the query layer tell "this customer has nothing pending"
-- (rows exist, none pending) apart from "no such customer" (no rows at all) -- R12.2.
CREATE VIEW IF NOT EXISTS v_pending_shipments AS
SELECT
    sr.reference AS shipment_reference,
    s.customer_id AS customer_id,
    s.customer_name AS customer_name,
    e.subject AS subject,
    e.received_at AS received_at,
    sr.cycle_number AS cycle_number,
    (SELECT COUNT(*) FROM shipment_documents sd
        WHERE sd.shipment_id = sr.shipment_id) AS document_count,
    sr.outcome AS outcome,
    sr.discrepancy_count AS discrepancy_count,
    sr.uncertain_count AS uncertain_count,
    sr.review_state AS review_state,
    sr.decided_at AS decided_at
FROM shipment_reviews sr
JOIN shipments s ON s.id = sr.shipment_id
JOIN emails e ON e.id = sr.email_id;
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create every table, index and view if it doesn't already exist. Safe to call on every
    start-up (design section 3.7: resuming incomplete runs happens against this same schema).
    """
    conn.executescript(SCHEMA_SQL)
    conn.commit()
