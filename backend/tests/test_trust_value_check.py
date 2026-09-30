"""Value-check tests: does the extracted value follow from its source text (Phase 5 verify
list: "12,450.00 KGS" gives 12450 KG; a value that quietly changed something fails)."""

from __future__ import annotations

from app.trust.value_check import value_matches_source


def test_weight_value_follows_from_its_source_text() -> None:
    assert value_matches_source("gross_weight", "12450 KG", "12,450.00 KGS")


def test_weight_value_that_changed_the_unit_fails() -> None:
    assert not value_matches_source("gross_weight", "12450 KG", "12,450.00 LBS")


def test_weight_value_that_changed_the_amount_fails() -> None:
    assert not value_matches_source("gross_weight", "9999 KG", "12,450.00 KGS")


def test_hs_code_value_follows_from_its_source_text() -> None:
    assert value_matches_source("hs_code", "847130", "8471.30")


def test_hs_code_value_that_does_not_match_fails() -> None:
    assert not value_matches_source("hs_code", "850440", "8471.30")


def test_incoterm_value_follows_from_its_source_text() -> None:
    assert value_matches_source("incoterms", "CIF", "CIF Singapore")


def test_incoterm_value_that_does_not_match_fails() -> None:
    assert not value_matches_source("incoterms", "FOB", "CIF Singapore")


def test_port_value_follows_from_its_source_text() -> None:
    assert value_matches_source("port_of_loading", "Shanghai", "SHANGHAI, CHINA")


def test_text_field_value_matching_its_own_source_passes() -> None:
    name = "ACME Electronics Pte. Ltd."
    assert value_matches_source("consignee", name, name)


def test_text_field_value_that_corrected_a_typo_fails() -> None:
    """The classic "model quietly fixed a misspelling" case: source says ACEM, value claims
    ACME. The value check must fail so grounding's `near` result is what drives confidence,
    not a value check that papers over the difference."""
    assert not value_matches_source(
        "consignee", "ACME Electronics Pte. Ltd.", "ACEM Electronics Pte. Ltd."
    )


def test_unparseable_structured_value_fails_rather_than_raising() -> None:
    assert not value_matches_source("hs_code", "not a code", "also not a code")
