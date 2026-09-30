"""Format checks (design section 3.3 step 5)."""

from __future__ import annotations

import pytest

from app.trust.formats import (
    format_ok,
    hs_code_format_ok,
    incoterm_format_ok,
    invoice_number_format_ok,
    weight_format_ok,
)


@pytest.mark.parametrize(("value", "expected"), [("8471.30", True), ("84.3", False), ("", False)])
def test_hs_code_format(value: str, expected: bool) -> None:
    assert hs_code_format_ok(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("CIF Singapore", True), ("FOB", True), ("XYZ Singapore", False), ("", False)],
)
def test_incoterm_format(value: str, expected: bool) -> None:
    assert incoterm_format_ok(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"), [("862.40 KGS", True), ("no unit here", False), ("", False)]
)
def test_weight_format(value: str, expected: bool) -> None:
    assert weight_format_ok(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"), [("INV-2026-00417", True), ("", False), ("   ", False)]
)
def test_invoice_number_format(value: str, expected: bool) -> None:
    assert invoice_number_format_ok(value) is expected


def test_format_ok_dispatches_by_field_name() -> None:
    assert format_ok("hs_code", "8471.30") is True
    assert format_ok("hs_code", "bad") is False
    assert format_ok("consignee", "ACME Electronics") is True
