"""Unit tests for every normaliser (Phase 5 verify list: "Unit tests for every normaliser")."""

from __future__ import annotations

import pytest

from app.trust.normalise import (
    Weight,
    normalise_hs_code,
    normalise_incoterm,
    normalise_port,
    normalise_text,
    normalise_weight,
    text_comparison_key,
)


def test_normalise_text_trims_and_collapses_whitespace_but_keeps_case() -> None:
    assert normalise_text("  ACME   Electronics  Pte. Ltd.  ") == "ACME Electronics Pte. Ltd."


def test_text_comparison_key_ignores_case_spacing_and_punctuation() -> None:
    a = text_comparison_key("ACME Electronics Pte. Ltd.")
    b = text_comparison_key("acme electronics pte ltd")
    assert a == b


def test_text_comparison_key_distinguishes_different_names() -> None:
    assert text_comparison_key("ACME Electronics") != text_comparison_key("ACEM Electronics")


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("8471.30", "847130"), ("8504.40", "850440"), ("8471-30", "847130")],
)
def test_normalise_hs_code_keeps_digits_only(printed: str, expected: str) -> None:
    assert normalise_hs_code(printed) == expected


def test_normalise_hs_code_rejects_too_few_digits() -> None:
    assert normalise_hs_code("84.3") is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("862.40 KGS", Weight(862.40, "KG")),
        ("12,450.00 KGS", Weight(12450.00, "KG")),
        ("1,901.27 LBS", Weight(1901.27, "LB")),
        ("500 kg", Weight(500.0, "KG")),
        ("500 Kilograms", Weight(500.0, "KG")),
    ],
)
def test_normalise_weight_parses_amount_and_unit(printed: str, expected: Weight) -> None:
    assert normalise_weight(printed) == expected


def test_normalise_weight_rejects_an_unrecognised_unit() -> None:
    assert normalise_weight("500 stone") is None


def test_normalise_weight_rejects_text_with_no_number() -> None:
    assert normalise_weight("heavy") is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("CIF Singapore", "CIF"), ("FOB Shanghai", "FOB"), ("cif", "CIF")],
)
def test_normalise_incoterm_splits_off_the_place(printed: str, expected: str) -> None:
    assert normalise_incoterm(printed) == expected


def test_normalise_incoterm_rejects_a_non_letter_token() -> None:
    assert normalise_incoterm("123 Singapore") is None


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("SHANGHAI, CHINA", "Shanghai"),
        ("Shanghai", "Shanghai"),
        ("YANTIAN, CHINA", "Yantian"),
        ("SINGAPORE", "Singapore"),
    ],
)
def test_normalise_port_maps_printed_forms_to_canonical_names(printed: str, expected: str) -> None:
    assert normalise_port(printed) == expected


def test_normalise_port_returns_none_for_an_unknown_port() -> None:
    assert normalise_port("ROTTERDAM") is None
