"""Tests for `app.shipment.decision` (requirements 4.1-4.6, 15.3).

The key test mirrors `test_guardrails.py`'s 6561-case sweep, one level up: it sweeps every
combination of document status, Doc_Type, field verdict and cross-check result and asserts
`auto_approve` appears only when R4.2's five conditions hold.
"""

from __future__ import annotations

from itertools import product

import pytest

from app.shipment.crosscheck import CrossCheckResult
from app.shipment.decision import DocumentEntry, FieldVerdictEntry, decide_shipment

# --- the exhaustive sweep ------------------------------------------------------------------------

DOC_STATUSES = ("succeeded", "failed")
DOC_TYPES = ("commercial_invoice", "uncertain")
FIELD_VERDICTS = ("match", "mismatch", "uncertain", "not_applicable")
CROSS_CHECK_RESULTS = ("consistent", "inconsistent", "uncertain", "not_applicable")


def _document(status: str, doc_type: str, verdict: str) -> DocumentEntry:
    succeeded = status == "succeeded"
    return DocumentEntry(
        document_id="doc-1",
        filename="doc-1.pdf",
        doc_type=None if doc_type == "uncertain" else doc_type,
        succeeded=succeeded,
        failure_reason=None if succeeded else "pipeline error for testing",
        field_verdicts=(FieldVerdictEntry(field_name="hs_code", verdict=verdict),)
        if succeeded
        else (),
    )


def _cross_checks(result: str) -> dict[str, CrossCheckResult]:
    return {
        "hs_code": CrossCheckResult(
            field_name="hs_code", result=result, reason="testing", compared=[]
        )
    }


@pytest.mark.parametrize(
    "status,doc_type,verdict,cc_result",
    list(product(DOC_STATUSES, DOC_TYPES, FIELD_VERDICTS, CROSS_CHECK_RESULTS)),
)
def test_auto_approve_only_under_the_five_conditions(status, doc_type, verdict, cc_result):
    documents = [_document(status, doc_type, verdict)]
    cross_checks = _cross_checks(cc_result)

    decision = decide_shipment(documents, cross_checks)

    should_auto_approve = (
        status == "succeeded"
        and doc_type == "commercial_invoice"
        and verdict in ("match", "not_applicable")
        and cc_result in ("consistent", "not_applicable")
    )

    if should_auto_approve:
        assert decision.outcome == "auto_approve"
    else:
        assert decision.outcome != "auto_approve"


def test_single_fully_matching_document_reaches_auto_approve():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(field_name="consignee", verdict="match"),
                FieldVerdictEntry(field_name="hs_code", verdict="match"),
            ),
        )
    ]
    decision = decide_shipment(documents, {})
    assert decision.outcome == "auto_approve"
    assert decision.discrepancy_count == 0
    assert decision.uncertain_count == 0


def test_empty_effective_set_is_human_review():
    decision = decide_shipment([], {})
    assert decision.outcome == "human_review"


def test_not_applicable_never_limits_the_outcome():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(
                FieldVerdictEntry(field_name="consignee", verdict="match"),
                FieldVerdictEntry(field_name="port_of_loading", verdict="not_applicable"),
            ),
        )
    ]
    cross_checks = {
        "hs_code": CrossCheckResult(
            field_name="hs_code", result="not_applicable", reason=None, compared=[]
        )
    }
    decision = decide_shipment(documents, cross_checks)
    assert decision.outcome == "auto_approve"


def test_a_failed_document_forces_human_review_even_with_no_other_issues():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(FieldVerdictEntry(field_name="consignee", verdict="match"),),
        ),
        DocumentEntry(
            document_id="doc-2",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            succeeded=False,
            failure_reason="budget exceeded",
        ),
    ]
    decision = decide_shipment(documents, {})
    assert decision.outcome == "human_review"
    assert "bol.pdf" in decision.reasoning
    assert "budget exceeded" in decision.reasoning


def test_uncertain_doc_type_forces_human_review():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="mystery.pdf",
            doc_type=None,
            succeeded=True,
            field_verdicts=(),
        )
    ]
    decision = decide_shipment(documents, {})
    assert decision.outcome == "human_review"
    assert "mystery.pdf" in decision.reasoning


def test_a_mismatch_with_no_uncertainty_is_amendment_request():
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
    decision = decide_shipment(documents, {})
    assert decision.outcome == "amendment_request"
    assert decision.discrepancy_count == 1
    assert "hs_code" in decision.reasoning
    assert "850440" in decision.reasoning


def test_an_inconsistent_cross_check_is_amendment_request():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(FieldVerdictEntry(field_name="consignee", verdict="match"),),
        )
    ]
    cross_checks = {
        "consignee": CrossCheckResult(
            field_name="consignee",
            result="inconsistent",
            reason="consignee differs across documents",
            compared=[],
        )
    }
    decision = decide_shipment(documents, cross_checks)
    assert decision.outcome == "amendment_request"
    assert decision.discrepancy_count == 1
    assert "consignee" in decision.reasoning


def test_mismatch_and_uncertain_together_is_human_review_not_amendment():
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
    decision = decide_shipment(documents, {})
    assert decision.outcome == "human_review"


def test_uncertain_count_includes_fields_checks_and_doc_types():
    documents = [
        DocumentEntry(
            document_id="doc-1",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            succeeded=True,
            field_verdicts=(FieldVerdictEntry(field_name="incoterms", verdict="uncertain"),),
        ),
        DocumentEntry(
            document_id="doc-2",
            filename="mystery.pdf",
            doc_type=None,
            succeeded=True,
            field_verdicts=(),
        ),
    ]
    cross_checks = {
        "hs_code": CrossCheckResult(
            field_name="hs_code", result="uncertain", reason="unresolved", compared=[]
        )
    }
    decision = decide_shipment(documents, cross_checks)
    assert decision.uncertain_count == 3  # 1 field + 1 cross-check + 1 doc_type
    assert decision.outcome == "human_review"


def test_rerunning_on_unchanged_results_gives_the_same_outcome_and_text():
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
    first = decide_shipment(documents, {})
    second = decide_shipment(documents, {})
    assert first.outcome == second.outcome
    assert first.reasoning == second.reasoning
    assert first.discrepancy_count == second.discrepancy_count
    assert first.uncertain_count == second.uncertain_count


def test_module_does_not_import_app_llm():
    import ast

    import app.shipment.decision as module

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
