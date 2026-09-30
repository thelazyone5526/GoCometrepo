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
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create every table, index and view if it doesn't already exist. Safe to call on every
    start-up (design section 3.7: resuming incomplete runs happens against this same schema).
    """
    conn.executescript(SCHEMA_SQL)
    conn.commit()
