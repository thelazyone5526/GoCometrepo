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

VIEW_NAMES: tuple[str, ...] = ("v_documents", "v_fields")


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
            "The run's final decision: 'auto_approved', 'human_review', or "
            "'amendment_requested'.",
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
        ColumnDoc(
            "verdict_confidence", "The Validator's confidence in this verdict, from 0 to 1."
        ),
    ),
)

VIEWS: tuple[ViewDoc, ...] = (V_DOCUMENTS, V_FIELDS)


def describe_views(views: tuple[ViewDoc, ...] = VIEWS) -> str:
    """Render every view's description and column list as plain text, for the Gemini prompt
    (design section 5 step 1: "the view definitions with a description of each column")."""
    blocks: list[str] = []
    for view in views:
        lines = [f"{view.name}: {view.description}", "Columns:"]
        lines.extend(f"  - {col.name}: {col.description}" for col in view.columns)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
