"""One test per rule type, and one per planted error (Phase 6 verify list), using ACME's real
rules file so the tests double as a check that the rules and the checkers agree."""

from __future__ import annotations

from pathlib import Path

from app.rules.checkers import check_rule, entity_name_key
from app.rules.loader import load_rules_for_customer

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
RULES = load_rules_for_customer("acme", rules_dir=RULES_DIR)


def _rule(field_name: str):
    return RULES.rules[field_name]


# --- One test per rule type --------------------------------------------------------------------


def test_equals_rule_matches_the_expected_value() -> None:
    outcome = check_rule("port_of_discharge", "SINGAPORE", _rule("port_of_discharge"))
    assert outcome.verdict == "match"


def test_equals_rule_rejects_a_different_value() -> None:
    outcome = check_rule("port_of_discharge", "Rotterdam", _rule("port_of_discharge"))
    assert outcome.verdict == "mismatch"


def test_in_list_rule_matches_an_allowed_hs_code() -> None:
    outcome = check_rule("hs_code", "8471.30", _rule("hs_code"))
    assert outcome.verdict == "match"


def test_in_list_rule_rejects_a_code_not_on_the_list() -> None:
    outcome = check_rule("hs_code", "8504.40", _rule("hs_code"))
    assert outcome.verdict == "mismatch"


def test_pattern_rule_matches_a_valid_invoice_number() -> None:
    outcome = check_rule("invoice_number", "INV-2026-00417", _rule("invoice_number"))
    assert outcome.verdict == "match"


def test_pattern_rule_rejects_a_placeholder_value() -> None:
    outcome = check_rule("invoice_number", "TBD", _rule("invoice_number"))
    assert outcome.verdict == "mismatch"


def test_pattern_rule_rejects_text_that_does_not_match() -> None:
    outcome = check_rule("invoice_number", "not-an-invoice-number", _rule("invoice_number"))
    assert outcome.verdict == "mismatch"


def test_quantity_rule_matches_a_positive_kg_weight() -> None:
    outcome = check_rule("gross_weight", "862.40 KG", _rule("gross_weight"))
    assert outcome.verdict == "match"


def test_quantity_rule_rejects_a_zero_weight() -> None:
    outcome = check_rule("gross_weight", "0 KG", _rule("gross_weight"))
    assert outcome.verdict == "mismatch"


def test_entity_name_rule_matches_the_registered_name_exactly() -> None:
    outcome = check_rule("consignee", "ACME Electronics Pte. Ltd.", _rule("consignee"))
    assert outcome.verdict == "match"


def test_entity_name_rule_matches_a_listed_alias_exactly() -> None:
    outcome = check_rule("consignee", "ACME Electronics Private Limited", _rule("consignee"))
    assert outcome.verdict == "match"


def test_entity_name_rule_sends_a_close_variant_to_judgement() -> None:
    # One letter off from the registered name: close enough to need a judgement call, not an
    # automatic mismatch.
    outcome = check_rule("consignee", "ACME Electronic Pte. Ltd.", _rule("consignee"))
    assert outcome.verdict == "needs_judgement"
    assert outcome.score is not None and 85.0 <= outcome.score < 100.0


def test_entity_name_rule_rejects_a_clearly_different_name() -> None:
    outcome = check_rule("consignee", "Totally Different Trading Co", _rule("consignee"))
    assert outcome.verdict == "mismatch"


def test_llm_judgement_rule_always_needs_judgement() -> None:
    outcome = check_rule(
        "goods_description", "Portable laptop computers", _rule("goods_description")
    )
    assert outcome.verdict == "needs_judgement"


# --- entity_name_key: suffix normalisation, ahead of text_comparison_key -----------------------


def test_entity_name_key_treats_abbreviated_and_spelled_out_suffixes_as_equal() -> None:
    assert entity_name_key("ACME Electronics Pte. Ltd.") == entity_name_key(
        "ACME Electronics Private Limited"
    )


# --- One test per planted error (E1-E5), against the real rules file ---------------------------


def test_e1_hs_code_not_on_the_approved_list_is_a_mismatch() -> None:
    outcome = check_rule("hs_code", "8504.40", _rule("hs_code"))
    assert outcome.verdict == "mismatch"


def test_e2_misspelled_consignee_is_a_mismatch_or_needs_judgement() -> None:
    # "ACEM Electronics Pte. Ltd." vs "ACME Electronics Pte. Ltd." -- one transposed letter.
    outcome = check_rule("consignee", "ACEM Electronics Pte. Ltd.", _rule("consignee"))
    assert outcome.verdict in ("mismatch", "needs_judgement")


def test_e3_fob_incoterm_is_a_mismatch() -> None:
    outcome = check_rule("incoterms", "FOB", _rule("incoterms"))
    assert outcome.verdict == "mismatch"


def test_e4_weight_in_lbs_is_a_mismatch_with_expected_kg() -> None:
    outcome = check_rule("gross_weight", "1900.97 LBS", _rule("gross_weight"))
    assert outcome.verdict == "mismatch"
    assert outcome.expected == "KG"


def test_e5_blank_invoice_number_is_a_mismatch() -> None:
    # E5's printed value is empty once the label is stripped; represented here as the raw
    # value that would remain, which the pattern rule rejects.
    outcome = check_rule("invoice_number", "", _rule("invoice_number"))
    assert outcome.verdict == "mismatch"
