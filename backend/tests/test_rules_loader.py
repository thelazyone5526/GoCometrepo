"""Tests for `rules/loader.py` (Phase 6): the YAML rules file becomes a validated `RuleSet`,
and a bad file fails loudly at load time, not partway through a run."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.rules.loader import (
    EntityNameRule,
    InListRule,
    RuleLoadError,
    load_rules,
    load_rules_for_customer,
)

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"


def test_acme_rules_file_loads() -> None:
    rules = load_rules_for_customer("acme", rules_dir=RULES_DIR)
    assert rules.customer_id == "acme"
    assert rules.customer_name == "ACME Electronics Pte. Ltd."


def test_expected_fields_for_commercial_invoice_matches_the_8_fields() -> None:
    rules = load_rules_for_customer("acme", rules_dir=RULES_DIR)
    expected = rules.expected_fields_for("commercial_invoice")
    assert expected == {
        "consignee",
        "hs_code",
        "port_of_loading",
        "port_of_discharge",
        "incoterms",
        "goods_description",
        "gross_weight",
        "invoice_number",
    }


def test_expected_fields_for_an_unlisted_document_type_is_empty() -> None:
    rules = load_rules_for_customer("acme", rules_dir=RULES_DIR)
    assert rules.expected_fields_for("bill_of_lading") == frozenset()


def test_hs_code_rule_parses_as_in_list_with_compare_digits() -> None:
    rules = load_rules_for_customer("acme", rules_dir=RULES_DIR)
    rule = rules.rules["hs_code"]
    assert isinstance(rule, InListRule)
    assert rule.compare_digits == 6
    assert "847130" in rule.allowed


def test_consignee_rule_parses_as_entity_name() -> None:
    rules = load_rules_for_customer("acme", rules_dir=RULES_DIR)
    rule = rules.rules["consignee"]
    assert isinstance(rule, EntityNameRule)
    assert rule.registered == "ACME Electronics Pte. Ltd."


def test_missing_file_raises_rule_load_error(tmp_path: Path) -> None:
    with pytest.raises(RuleLoadError):
        load_rules(tmp_path / "does-not-exist.yaml")


def test_invalid_yaml_raises_rule_load_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("customer_id: acme\nrules: [this is not a mapping\n", encoding="utf-8")
    with pytest.raises(RuleLoadError):
        load_rules(path)


def test_unknown_rule_type_raises_rule_load_error(tmp_path: Path) -> None:
    raw = {
        "customer_id": "acme",
        "customer_name": "ACME",
        "document_types": {"commercial_invoice": {"expected_fields": ["hs_code"]}},
        "rules": {"hs_code": {"id": "R-HS-1", "type": "not_a_real_type", "allowed": ["1"]}},
    }
    path = tmp_path / "acme.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(RuleLoadError):
        load_rules(path)


def test_expected_field_with_no_matching_rule_raises_rule_load_error(tmp_path: Path) -> None:
    raw = {
        "customer_id": "acme",
        "customer_name": "ACME",
        "document_types": {
            "commercial_invoice": {"expected_fields": ["hs_code", "invoice_number"]}
        },
        "rules": {"hs_code": {"id": "R-HS-1", "type": "in_list", "allowed": ["847130"]}},
    }
    path = tmp_path / "acme.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(RuleLoadError, match="invoice_number"):
        load_rules(path)


def test_a_rule_missing_a_required_field_raises_rule_load_error(tmp_path: Path) -> None:
    raw = {
        "customer_id": "acme",
        "customer_name": "ACME",
        "document_types": {"commercial_invoice": {"expected_fields": ["hs_code"]}},
        # in_list requires "allowed"; it's missing here.
        "rules": {"hs_code": {"id": "R-HS-1", "type": "in_list"}},
    }
    path = tmp_path / "acme.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(RuleLoadError):
        load_rules(path)
