"""Plain descriptions of the two query views (design section 4 and 5, Phase 11 task 1).

The query layer never lets Gemini see `app.db`'s real tables -- only `v_documents` and
`v_fields` (`gate.py` enforces that with a SQLite authorizer). Gemini still needs to know what
columns those two views have and what each one means, so it can write correct SQL. This module
is the single place that text lives, so the prompt sent to Gemini and any future documentation
of the views describe exactly the same columns, worded the same way.

Column names and meanings follow design section 4's table list: `v_documents` is one row per
run (joined to its document and shipment), and `v_fields` is one row per field result (joined
to its run, document and shipment).
"""

from __future__ import annotations

from dataclasses import dataclass

VIEW_NAMES: tuple[str, ...] = ("v_documents", "v_fields", "v_pending_shipments")


@dataclass(frozen=True)
class ColumnDoc:
    name: str
    description: str


@dataclass(frozen=True)
class ViewDoc:
    name: str
    description: str
    columns: tuple[ColumnDoc, ...]

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


V_DOCUMENTS = ViewDoc(
    name="v_documents",
    description=(
        "One row per run (one run per uploaded document in Part 1). Use this view for "
        "questions about documents, runs, outcomes, cost and time."
    ),
    columns=(
        ColumnDoc("run_id", "The run's ID. Unique per row."),
        ColumnDoc("customer_name", "The customer (shipper/importer) this document belongs to."),
        ColumnDoc(
            "doc_type",
            "The document type the Extractor identified, e.g. 'commercial_invoice', "
            "'bill_of_lading', 'packing_list', 'certificate_of_origin', or 'other'.",
        ),
        ColumnDoc("filename", "The uploaded file's original name."),
        ColumnDoc(
            "outcome",
            "The run's final decision: 'auto_approved', 'human_review', or 'amendment_requested'.",
        ),
        ColumnDoc(
            "reasoning",
            "The Router's explanation for the outcome, in 1-2 sentences. For "
            "'amendment_requested', this includes which fields need fixing and why.",
        ),
        ColumnDoc(
            "decision_source",
            "Who actually settled the outcome: 'llm' (the Router's Gemini call picked it), "
            "'code_override' (code corrected an unsafe LLM choice), or 'fallback' (Gemini was "
            "unavailable, so code picked the safest outcome from a template).",
        ),
        ColumnDoc("matched_count", "How many of the 8 fields verified as a match."),
        ColumnDoc("mismatched_count", "How many of the 8 fields verified as a mismatch."),
        ColumnDoc(
            "uncertain_count",
            "How many of the 8 fields could not be confidently verified either way.",
        ),
        ColumnDoc(
            "fallback_used",
            "1 if any Gemini call on this run was answered by the fallback (Lite) model "
            "instead of the primary model, else 0.",
        ),
        ColumnDoc("cost_usd", "The run's total LLM cost in US dollars (paid-tier equivalent)."),
        ColumnDoc("latency_ms", "How long the whole run took, in milliseconds."),
        ColumnDoc("started_at", "When the run started, as 'YYYY-MM-DD HH:MM:SS' (UTC)."),
        ColumnDoc(
            "finished_at",
            "When the run finished, as 'YYYY-MM-DD HH:MM:SS' (UTC). Use this column for "
            "'this week', 'today', 'yesterday' and similar date questions about a run.",
        ),
    ),
)

V_FIELDS = ViewDoc(
    name="v_fields",
    description=(
        "One row per field result (8 rows per run, one for each of the 8 extracted fields). "
        "Use this view for questions about individual fields, values, or verdicts."
    ),
    columns=(
        ColumnDoc("field_result_id", "The field result's own ID. Unique per row."),
        ColumnDoc("run_id", "The run this field result belongs to (joins to v_documents.run_id)."),
        ColumnDoc("customer_name", "The customer this document belongs to."),
        ColumnDoc("filename", "The document's original filename."),
        ColumnDoc("doc_type", "The document type, same meaning as in v_documents."),
        ColumnDoc("outcome", "The run's overall outcome, same meaning as in v_documents."),
        ColumnDoc(
            "field_name",
            "Which of the 8 fields this row is about, e.g. 'hs_code', 'consignee', "
            "'incoterms', 'goods_description', 'port_of_loading', 'port_of_discharge', "
            "'gross_weight', 'invoice_number'.",
        ),
        ColumnDoc("value", "The value the Extractor read for this field (may be null)."),
        ColumnDoc("found", "The value as found on the document, when the Validator compared it."),
        ColumnDoc("expected", "The value the customer's rule expected, when there is one."),
        ColumnDoc(
            "verdict",
            "The Validator's verdict for this field: 'match', 'mismatch', 'uncertain', or "
            "'not_applicable'.",
        ),
        ColumnDoc("verdict_reason", "1-2 sentences explaining the verdict."),
        ColumnDoc("rule_id", "Which customer rule was checked, when a rule applied."),
        ColumnDoc(
            "extraction_confidence", "The Extractor's confidence in this value, from 0 to 1."
        ),
        ColumnDoc("verdict_confidence", "The Validator's confidence in this verdict, from 0 to 1."),
    ),
)

V_PENDING_SHIPMENTS = ViewDoc(
    name="v_pending_shipments",
    description=(
        "One row per Part 2 shipment (one email, one or more documents, one review). Use "
        "this view for questions about pending review, review state, shipment-level status, "
        "or discrepancy/uncertain counts per shipment, rather than per document."
    ),
    columns=(
        ColumnDoc(
            "shipment_reference",
            "The SHP- reference the customer used when emailing in these documents, e.g. "
            "'SHP-5501'. Unique per shipment.",
        ),
        ColumnDoc("customer_id", "The customer's id, e.g. 'acme'."),
        ColumnDoc("customer_name", "The customer (shipper/importer) this shipment belongs to."),
        ColumnDoc("subject", "The subject line of the email the shipment arrived in."),
        ColumnDoc(
            "received_at",
            "When the email arrived, as 'YYYY-MM-DD HH:MM:SS' (UTC). Use this column for "
            "'this week', 'today', 'yesterday' and similar date questions about a shipment.",
        ),
        ColumnDoc(
            "cycle_number",
            "Which resubmission cycle this is for the reference: 1 the first time documents "
            "arrived for it, 2 the first resubmission, and so on.",
        ),
        ColumnDoc(
            "document_count",
            "How many documents are in the shipment's effective set: this email's own "
            "documents plus any carried over from an earlier cycle.",
        ),
        ColumnDoc(
            "outcome",
            "The shipment-level decision: 'auto_approve', 'human_review', or "
            "'amendment_request'. 'auto_approve' means the shipment is recommended for "
            "approval, not that anything has been sent -- only an operator's Send does that.",
        ),
        ColumnDoc(
            "discrepancy_count",
            "How many cross-document checks came back inconsistent or mismatched for this "
            "shipment.",
        ),
        ColumnDoc(
            "uncertain_count",
            "How many fields or cross-document checks for this shipment could not be "
            "confidently verified either way.",
        ),
        ColumnDoc(
            "review_state",
            "Where this shipment stands with the operator: 'pending_review' (nothing done "
            "yet, waiting on a person), 'approved_sent' (an approval reply was sent), "
            "'amendment_sent' (an amendment request was sent), 'reviewed_no_send' (the "
            "operator looked at it and chose not to send anything), or 'failed' (processing "
            "could not finish; see the shipment's failure reason). This view carries every "
            "state, not only pending ones, which is what lets a question tell 'this customer "
            "has nothing pending' (rows exist, none pending_review) apart from 'no such "
            "customer' (no rows at all).",
        ),
        ColumnDoc(
            "decided_at",
            "When the shipment-level outcome was decided, as 'YYYY-MM-DD HH:MM:SS' (UTC), or "
            "null if it has not been decided yet.",
        ),
    ),
)

VIEWS: tuple[ViewDoc, ...] = (V_DOCUMENTS, V_FIELDS, V_PENDING_SHIPMENTS)


def describe_views(views: tuple[ViewDoc, ...] = VIEWS) -> str:
    """Render every view's description and column list as plain text, for the Gemini prompt
    (design section 5 step 1: "the view definitions with a description of each column")."""
    blocks: list[str] = []
    for view in views:
        lines = [f"{view.name}: {view.description}", "Columns:"]
        lines.extend(f"  - {col.name}: {col.description}" for col in view.columns)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
