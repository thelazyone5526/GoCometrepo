"""Part 2 task 3 (requirement 2.5, signed off under 15.5 -- D5 in docs/part2-decisions.md):
bill_of_lading and packing_list now expect fields in `rules/acme.yaml`, where they expected
none before. This file proves the widening is exactly what was signed off and nothing more:
`trust/guardrails.py` itself is untouched, and its own exhaustive sweep (test_guardrails.py)
still passes unmodified.
"""

from __future__ import annotations

from app.agents.validate import FieldVerdict
from app.rules.loader import load_rules_for_customer
from app.trust.guardrails import allowed_outcomes


def _match(field_name: str) -> FieldVerdict:
    return FieldVerdict(
        field_name=field_name,
        verdict="match",
        found="x",
        expected="x",
        rule_id="r",
        reason="matches",
        verdict_confidence=0.99,
    )


def test_bill_of_lading_expects_the_signed_off_fields() -> None:
    rules = load_rules_for_customer("acme")
    assert rules.expected_fields_for("bill_of_lading") == frozenset(
        {"consignee", "hs_code", "port_of_loading", "port_of_discharge", "gross_weight"}
    )


def test_packing_list_expects_the_signed_off_fields() -> None:
    rules = load_rules_for_customer("acme")
    assert rules.expected_fields_for("packing_list") == frozenset(
        {"consignee", "hs_code", "gross_weight", "invoice_number"}
    )


def test_every_expected_field_still_has_a_rule() -> None:
    """The loader's own check (`_check_every_expected_field_has_a_rule`) must still pass --
    proven implicitly by load succeeding, asserted explicitly here so a future edit that
    removes a rule entry fails loudly rather than only inside the loader."""
    rules = load_rules_for_customer("acme")
    for doc_type in ("bill_of_lading", "packing_list"):
        for field_name in rules.expected_fields_for(doc_type):
            assert field_name in rules.rules


def test_a_clean_bill_of_lading_can_now_reach_auto_approve() -> None:
    """Before this change, bill_of_lading expected no fields, so `allowed_outcomes` returned
    only ('human_review',) regardless of verdicts. This is the intended, signed-off widening:
    a fully matching BOL now reaches auto_approve. guardrails.py itself is not edited."""
    rules = load_rules_for_customer("acme")
    expected = rules.expected_fields_for("bill_of_lading")
    fields = {name: _match(name) for name in expected}
    assert allowed_outcomes(fields) == ("auto_approve", "human_review")


def test_a_clean_packing_list_can_now_reach_auto_approve() -> None:
    rules = load_rules_for_customer("acme")
    expected = rules.expected_fields_for("packing_list")
    fields = {name: _match(name) for name in expected}
    assert allowed_outcomes(fields) == ("auto_approve", "human_review")
