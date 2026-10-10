"""Tests for `app.shipment.crosscheck` (requirements 3.1-3.6, 15.3)."""

from __future__ import annotations

import itertools
import random

import pytest

from app.shipment.crosscheck import DocumentFieldValue, cross_check

EXPECTED = {
    "commercial_invoice": frozenset({"consignee", "hs_code", "gross_weight", "invoice_number"}),
    "bill_of_lading": frozenset({"consignee", "hs_code", "gross_weight"}),
    "packing_list": frozenset({"consignee", "hs_code", "gross_weight", "invoice_number"}),
}


def _entry(**kwargs) -> DocumentFieldValue:
    defaults = {
        "document_id": "doc-1",
        "filename": "invoice.pdf",
        "doc_type": "commercial_invoice",
        "field_name": "consignee",
        "value": "ACME Electronics Pte. Ltd.",
        "source_text": None,
        "page": 1,
        "field_result_id": "fr-1",
        "uncertain": False,
    }
    defaults.update(kwargs)
    return DocumentFieldValue(**defaults)


def test_order_independence_permutations():
    entries = [
        _entry(
            document_id="a",
            filename="a.pdf",
            doc_type="commercial_invoice",
            field_name="hs_code",
            value="8471.30.00",
        ),
        _entry(
            document_id="b",
            filename="b.pdf",
            doc_type="bill_of_lading",
            field_name="hs_code",
            value="847130",
        ),
        _entry(
            document_id="c",
            filename="c.pdf",
            doc_type="packing_list",
            field_name="hs_code",
            value="847130",
        ),
    ]
    baseline = cross_check(entries, EXPECTED)
    for perm in itertools.permutations(entries):
        result = cross_check(list(perm), EXPECTED)
        assert result == baseline


def test_order_independence_shuffle_many_times():
    entries = [
        _entry(
            document_id="a",
            filename="a.pdf",
            doc_type="commercial_invoice",
            field_name="gross_weight",
            value="100 KG",
        ),
        _entry(
            document_id="b",
            filename="b.pdf",
            doc_type="bill_of_lading",
            field_name="gross_weight",
            value="100 KG",
        ),
        _entry(
            document_id="c",
            filename="c.pdf",
            doc_type="packing_list",
            field_name="gross_weight",
            value="220.46 LB",
        ),
    ]
    baseline = cross_check(entries, EXPECTED)
    rng = random.Random(42)
    for _ in range(50):
        shuffled = list(entries)
        rng.shuffle(shuffled)
        assert cross_check(shuffled, EXPECTED) == baseline
    # it should actually be inconsistent (LB vs KG, no conversion)
    assert baseline["gross_weight"].result == "inconsistent"


def test_kg_vs_lb_same_real_weight_is_inconsistent_no_conversion():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="gross_weight",
            value="5 KG",
        ),
        _entry(
            document_id="b",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            field_name="gross_weight",
            value="11.02 LB",
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["gross_weight"].result == "inconsistent"
    assert "gross_weight" in result["gross_weight"].reason


def test_consignee_pte_ltd_vs_private_limited_is_consistent():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="consignee",
            value="ACME Electronics Pte. Ltd.",
        ),
        _entry(
            document_id="b",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            field_name="consignee",
            value="ACME Electronics Private Limited",
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["consignee"].result == "consistent"
    assert len(result["consignee"].compared) == 2


def test_one_uncertain_other_two_agree_is_uncertain_never_consistent():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="hs_code",
            value="847130",
        ),
        _entry(
            document_id="b",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            field_name="hs_code",
            value="847130",
        ),
        _entry(
            document_id="c",
            filename="packing.pdf",
            doc_type="packing_list",
            field_name="hs_code",
            value="847199",
            uncertain=True,
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["hs_code"].result == "uncertain"


def test_carrying_document_with_no_value_is_inconsistent_names_document():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="invoice_number",
            value="INV-2026-0001",
        ),
        _entry(
            document_id="b",
            filename="packing.pdf",
            doc_type="packing_list",
            field_name="invoice_number",
            value=None,
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["invoice_number"].result == "inconsistent"
    assert "packing.pdf" in result["invoice_number"].reason
    assert "invoice_number" in result["invoice_number"].reason


def test_fewer_than_two_carriers_is_not_applicable():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="invoice_number",
            value="INV-2026-0001",
        ),
        # bill_of_lading doesn't carry invoice_number per EXPECTED, so this row should be
        # ignored entirely, leaving only one carrier.
        _entry(
            document_id="b",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            field_name="invoice_number",
            value="INV-2026-0001",
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["invoice_number"].result == "not_applicable"
    assert result["invoice_number"].compared == []


def test_genuinely_different_consignee_is_inconsistent_with_values_listed():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="consignee",
            value="ACME Electronics Pte. Ltd.",
        ),
        _entry(
            document_id="b",
            filename="bol.pdf",
            doc_type="bill_of_lading",
            field_name="consignee",
            value="Globex Trading Co.",
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["consignee"].result == "inconsistent"
    compared_values = {row["filename"]: row["value"] for row in result["consignee"].compared}
    assert compared_values["invoice.pdf"] == "ACME Electronics Pte. Ltd."
    assert compared_values["bol.pdf"] == "Globex Trading Co."


def test_not_applicable_fields_still_present_for_all_checked_fields():
    result = cross_check([], EXPECTED)
    assert set(result.keys()) == {"consignee", "hs_code", "gross_weight", "invoice_number"}
    for outcome in result.values():
        assert outcome.result == "not_applicable"
        assert outcome.compared == []


def test_unknown_doc_type_never_carries_anything():
    entries = [
        _entry(
            document_id="a",
            filename="invoice.pdf",
            doc_type="commercial_invoice",
            field_name="hs_code",
            value="847130",
        ),
        _entry(
            document_id="b",
            filename="mystery.pdf",
            doc_type=None,
            field_name="hs_code",
            value="847130",
        ),
    ]
    result = cross_check(entries, EXPECTED)
    assert result["hs_code"].result == "not_applicable"


def test_module_does_not_import_app_llm():
    import ast

    import app.shipment.crosscheck as module

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


@pytest.mark.parametrize("field_name", ["consignee", "hs_code", "gross_weight", "invoice_number"])
def test_all_four_checked_fields_present_in_output(field_name):
    result = cross_check([], EXPECTED)
    assert field_name in result
