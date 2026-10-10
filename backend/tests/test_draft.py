"""Tests for `app.shipment.draft` (requirements 6.1-6.6, 15.3)."""

from __future__ import annotations

from app.shipment.crosscheck import CrossCheckResult
from app.shipment.decision import DocumentEntry, FieldVerdictEntry
from app.shipment.draft import render_draft


def _clean_document() -> DocumentEntry:
    return DocumentEntry(
        document_id="doc-1",
        filename="invoice.pdf",
        doc_type="commercial_invoice",
        succeeded=True,
        field_verdicts=(FieldVerdictEntry(field_name="consignee", verdict="match"),),
    )


def test_auto_approve_renders_approval_draft_addressed_to_sender():
    draft = render_draft(
        outcome="auto_approve",
        sender="ops@su.example",
        subject="SHP-5501 acme documents",
        documents=[_clean_document()],
        cross_checks={},
    )
    assert draft.kind == "approval"
    assert draft.recipient == "ops@su.example"
    assert "SHP-5501 acme documents" in draft.subject
    assert draft.render_error is None
    assert draft.uncertain_items == ()


def test_amendment_request_lists_every_discrepancy_with_document_and_values():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(
                    field_name="hs_code", verdict="mismatch", found="850440", expected="847130"
                ),
            ),
        )
    ]
    draft = render_draft(
        outcome="amendment_request",
        sender="ops@su.example",
        subject="SHP-5501 acme documents",
        documents=documents,
        cross_checks={},
    )
    assert draft.kind == "amendment"
    assert "invoice.pdf" in draft.body
    assert "hs_code" in draft.body
    assert "850440" in draft.body
    assert "847130" in draft.body


def test_cross_document_discrepancy_lists_value_from_each_document():
    documents = [_clean_document()]
    cross_checks = {
        "consignee": CrossCheckResult(
            field_name="consignee",
            result="inconsistent",
            reason="consignee differs across documents",
            compared=[
                {"filename": "invoice.pdf", "value": "ACME Electronics"},
                {"filename": "bol.pdf", "value": "Globex Trading"},
            ],
        )
    }
    draft = render_draft(
        outcome="amendment_request",
        sender="ops@su.example",
        subject="SHP-5501",
        documents=documents,
        cross_checks=cross_checks,
    )
    assert draft.kind == "amendment"
    assert "invoice.pdf" in draft.body
    assert "ACME Electronics" in draft.body
    assert "bol.pdf" in draft.body
    assert "Globex Trading" in draft.body


def test_human_review_with_discrepancy_gets_amendment_draft():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(field_name="hs_code", verdict="mismatch"),
                FieldVerdictEntry(field_name="incoterms", verdict="uncertain"),
            ),
        )
    ]
    draft = render_draft(
        outcome="human_review",
        sender="ops@su.example",
        subject="SHP-5501",
        documents=documents,
        cross_checks={},
    )
    assert draft.kind == "amendment"
    assert "hs_code" in draft.body


def test_human_review_with_no_discrepancy_gets_empty_editable_draft():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="mystery.pdf",
            doc_type=None,
            succeeded=True,
            field_verdicts=(),
        )
    ]
    draft = render_draft(
        outcome="human_review",
        sender="ops@su.example",
        subject="SHP-5501",
        documents=documents,
        cross_checks={},
    )
    assert draft.kind == "empty"
    assert draft.body == ""
    assert draft.render_error is None


def test_uncertain_items_are_returned_separately_and_never_in_the_body():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(field_name="hs_code", verdict="mismatch"),
                FieldVerdictEntry(
                    field_name="incoterms", verdict="uncertain", reason="low confidence"
                ),
            ),
        )
    ]
    draft = render_draft(
        outcome="amendment_request",
        sender="ops@su.example",
        subject="SHP-5501",
        documents=documents,
        cross_checks={},
    )
    assert len(draft.uncertain_items) == 1
    assert "incoterms" in draft.uncertain_items[0]
    assert "incoterms" not in draft.body


def test_no_value_in_draft_is_absent_from_stored_results():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(
                    field_name="hs_code",
                    verdict="mismatch",
                    found="850440",
                    expected="847130",
                ),
            ),
        )
    ]
    draft = render_draft(
        outcome="amendment_request",
        sender="ops@su.example",
        subject="SHP-5501",
        documents=documents,
        cross_checks={},
    )
    # every value that appears in the body must trace back to the found/expected on the input
    assert "850440" in draft.body
    assert "847130" in draft.body
    # nothing invented: no other digit sequence resembling a code appears
    assert "999999" not in draft.body


def test_missing_sender_produces_empty_draft_with_render_error():
    draft = render_draft(
        outcome="auto_approve",
        sender=None,
        subject="SHP-5501",
        documents=[_clean_document()],
        cross_checks={},
    )
    assert draft.kind == "empty"
    assert draft.render_error is not None


def test_module_does_not_import_app_llm():
    import ast

    import app.shipment.draft as module

    with open(module.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=module.__file__)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(name == "app.llm" or name.startswith("app.llm.") for name in imported_modules)
    assert not hasattr(module, "llm")
