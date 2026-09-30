"""A hand-built SQLite fixture for the query layer's tests (Phase 11).

`app/store` (the real schema, Phase 8) doesn't exist yet in this session -- other work is
building it in parallel. So this module builds a throwaway `app.db`-shaped database directly:
the four base tables design section 4 lists (`shipments`, `documents`, `runs`,
`field_results`, trimmed to the columns the two views actually read), then `v_documents` and
`v_fields` exactly as `app.query.schema_doc` describes them. That means the query layer
(`gate.py`, `service.py`) can be built and tested end-to-end now, against the same view shape
the real storage phase is expected to produce.

**Reconciliation note for whoever wires up the real `app/store`:** this fixture's views select
straight from `runs`/`documents`/`shipments`/`field_results` with the column names design
section 4 gives them. If the real schema's column names, types, or the shape of `llm_calls`
end up differing (e.g. `cost_usd` computed from `llm_calls` rather than stored directly on
`runs`, or timestamps stored as Unix integers instead of `YYYY-MM-DD HH:MM:SS` text), the two
`CREATE VIEW` statements below are the thing to reconcile against the real table definitions --
`app/query/gate.py` and `app/query/schema_doc.py` don't need to change, since they only ever
see the view's column names, not the base tables.

Six runs are seeded, chosen so each of design section 5's 6 sample questions has an
unambiguous, hand-checkable answer (`EXPECTED` below). `TODAY` is fixed (not `date('now')`) so
every test is deterministic regardless of when it actually runs; it's a Wednesday, so "this
week" starts Monday 2026-09-28, matching the worked examples in the `query_v1` prompt.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

TODAY = "2026-09-30"  # a Wednesday
MONDAY_THIS_WEEK = "2026-09-28"

_SCHEMA_SQL = """
CREATE TABLE shipments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    reference TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    filename TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    outcome TEXT NOT NULL,
    reasoning TEXT,
    decision_source TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE field_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    field_name TEXT NOT NULL,
    value TEXT,
    found TEXT,
    expected TEXT,
    verdict TEXT NOT NULL,
    verdict_reason TEXT,
    rule_id TEXT,
    extraction_confidence REAL,
    verdict_confidence REAL
);

CREATE VIEW v_documents AS
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

CREATE VIEW v_fields AS
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


def build_fixture_db(path: str | Path) -> Path:
    """Create a fresh SQLite file at `path` with the schema above, seeded with 6 runs.
    Overwrites any existing file at `path`. Returns `path` as a `Path`, for convenience."""
    path = Path(path)
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA_SQL)

        conn.execute(
            "INSERT INTO shipments (id, customer_id, customer_name, reference, created_at) "
            "VALUES (1, 101, 'ACME Corp', 'ACME-REF-1', '2026-09-01 00:00:00'), "
            "(2, 102, 'Globex Ltd', 'GLB-REF-1', '2026-09-01 00:00:00')"
        )

        conn.execute(
            "INSERT INTO documents (id, shipment_id, filename, doc_type, created_at) VALUES "
            "(1, 1, 'inv-001.pdf', 'commercial_invoice', '2026-09-29 09:00:00'), "
            "(2, 1, 'inv-002.pdf', 'commercial_invoice', '2026-09-29 10:00:00'), "
            "(3, 2, 'inv-003.pdf', 'commercial_invoice', '2026-09-30 08:00:00'), "
            "(4, 2, 'bol-004.pdf', 'bill_of_lading', '2026-09-20 08:00:00'), "
            "(5, 1, 'inv-005.pdf', 'commercial_invoice', '2026-09-28 09:00:00'), "
            "(6, 2, 'inv-006.pdf', 'commercial_invoice', '2026-09-30 07:00:00')"
        )

        conn.execute(
            "INSERT INTO runs (id, document_id, outcome, reasoning, decision_source, "
            "fallback_used, cost_usd, latency_ms, started_at, finished_at) VALUES "
            # R1: clean, auto-approved, this week.
            "(1, 1, 'auto_approved', 'All fields matched; approved without review.', 'llm', "
            "0, 1.20, 8000, '2026-09-29 09:00:00', '2026-09-29 09:05:00'), "
            # R2: human review, answered by the fallback model, this week.
            "(2, 2, 'human_review', 'Consignee name and HS code both need review; invoice "
            "number could not be confidently read.', 'fallback', 1, 0.80, 9000, "
            "'2026-09-29 10:00:00', '2026-09-29 10:05:00'), "
            # R3: amendment requested, this week (today).
            "(3, 3, 'amendment_requested', 'Goods description is too vague for customs "
            "classification; amendment requested.', 'llm', 0, 1.50, 7000, "
            "'2026-09-30 08:00:00', '2026-09-30 08:06:00'), "
            # R4: human review, overridden by code, LAST week (before this Monday).
            "(4, 4, 'human_review', 'The router chose auto-approve but code overrode it: the "
            "HS code did not match the customer rule.', 'code_override', 0, 2.00, 10000, "
            "'2026-09-20 08:00:00', '2026-09-20 08:07:00'), "
            # R5: auto-approved, one uncertain field, this week (Monday itself: boundary).
            "(5, 5, 'auto_approved', 'HS code could not be confidently verified but all other "
            "fields matched; approved.', 'llm', 0, 0.60, 6000, "
            "'2026-09-28 09:00:00', '2026-09-28 09:04:00'), "
            # R6: human review, two uncertain fields, this week (today).
            "(6, 6, 'human_review', 'HS code and gross weight could not be confidently "
            "verified.', 'llm', 0, 0.90, 7500, '2026-09-30 07:00:00', '2026-09-30 07:05:00')"
        )

        conn.execute(
            "INSERT INTO field_results (run_id, field_name, value, found, expected, verdict, "
            "verdict_reason, rule_id, extraction_confidence, verdict_confidence) VALUES "
            # R1: everything matches.
            "(1, 'consignee', 'ACME Corp', 'ACME Corp', 'ACME Corp', 'match', 'Exact match.', "
            "'entity_name', 0.98, 0.99), "
            "(1, 'hs_code', '8471.30', '8471.30', '8471.30', 'match', 'Exact match.', "
            "'exact', 0.97, 0.99), "
            "(1, 'goods_description', 'Laptop computers', NULL, NULL, 'match', 'Specific "
            "enough.', 'llm_judgement', 0.90, 0.95), "
            "(1, 'invoice_number', 'INV-001', NULL, NULL, 'match', 'Present.', NULL, 0.99, "
            "0.99), "
            # R2: consignee and hs_code mismatch, invoice_number uncertain.
            "(2, 'consignee', 'ACME Corp Pty', 'ACME Corp Pty', 'ACME Corp', 'mismatch', "
            "'Different legal entity, not just a variant spelling.', 'entity_name', 0.80, "
            "0.85), "
            "(2, 'hs_code', '8471.31', '8471.31', '8471.30', 'mismatch', 'Does not match the "
            "customer rule.', 'exact', 0.95, 0.97), "
            "(2, 'invoice_number', NULL, NULL, NULL, 'uncertain', 'Could not be read from the "
            "page.', NULL, 0.20, 0.30), "
            "(2, 'goods_description', 'Laptop computers, 14-inch', NULL, NULL, 'match', "
            "'Specific enough.', 'llm_judgement', 0.92, 0.94), "
            # R3: goods_description mismatch (too vague), others match.
            "(3, 'goods_description', 'Electronics', NULL, NULL, 'mismatch', 'Too vague for "
            "customs classification.', 'llm_judgement', 0.85, 0.90), "
            "(3, 'hs_code', '8517.12', '8517.12', '8517.12', 'match', 'Exact match.', "
            "'exact', 0.96, 0.98), "
            "(3, 'consignee', 'Globex Ltd', 'Globex Ltd', 'Globex Ltd', 'match', 'Exact "
            "match.', 'entity_name', 0.97, 0.98), "
            # R4: hs_code mismatch (code overrode the router here), port_of_loading matches.
            "(4, 'hs_code', '8703.23', '8703.23', '8703.10', 'mismatch', 'Does not match the "
            "customer rule.', 'exact', 0.93, 0.96), "
            "(4, 'port_of_loading', 'Singapore', 'Singapore', 'Singapore', 'match', 'Exact "
            "match.', 'exact', 0.95, 0.97), "
            # R5: hs_code uncertain, others match.
            "(5, 'hs_code', NULL, NULL, NULL, 'uncertain', 'Page too degraded to read "
            "clearly.', NULL, 0.35, 0.40), "
            "(5, 'consignee', 'ACME Corp', 'ACME Corp', 'ACME Corp', 'match', 'Exact match.', "
            "'entity_name', 0.96, 0.98), "
            "(5, 'goods_description', 'Laptop computers, 16GB RAM', NULL, NULL, 'match', "
            "'Specific enough.', 'llm_judgement', 0.91, 0.93), "
            # R6: hs_code and gross_weight both uncertain, consignee matches.
            "(6, 'hs_code', NULL, NULL, NULL, 'uncertain', 'Page too degraded to read "
            "clearly.', NULL, 0.30, 0.35), "
            "(6, 'gross_weight', NULL, NULL, NULL, 'uncertain', 'Page too degraded to read "
            "clearly.', NULL, 0.25, 0.30), "
            "(6, 'consignee', 'Globex Ltd', 'Globex Ltd', 'Globex Ltd', 'match', 'Exact "
            "match.', 'entity_name', 0.94, 0.96)"
        )
        conn.commit()
    finally:
        conn.close()
    return path


# Hand-checked expected answers for design section 5's 6 sample questions, against the fixture
# seeded above. Tests assert against these rather than re-deriving them, so a test failure
# means either the SQL or the fixture data changed, not an error in the test's own arithmetic.
EXPECTED = {
    # R2 and R6 are human_review and finished this week; R4 is human_review but last week.
    "flagged_this_week_count": 2,
    # Only R3.
    "amendment_documents": [("inv-003.pdf",)],
    # hs_code mismatches on R2 and R4 (2); consignee mismatches on R2 (1); goods_description
    # mismatches on R3 (1). hs_code is the unique maximum.
    "most_common_mismatched_field": "hs_code",
    # hs_code is uncertain on R5 and R6.
    "uncertain_hs_code_run_ids": [5, 6],
    # (1.20 + 0.80 + 1.50 + 2.00 + 0.60 + 0.90) / 6
    "average_cost_usd": 7.00 / 6,
    # Only R4.
    "code_override_run_ids": [4],
}
