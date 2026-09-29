"""Checks on the generated sample documents and their answer files (Phase 2, item 13).

These read the committed files in samples/, so they also catch a stale regeneration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest
import yaml

from app.config import BACKEND_DIR
from samples.answers import AnswerFile, build_answer, load_answer
from samples.degrade import Degradation
from samples.generate import ALL_SPECS, SAMPLES_DIR, Spec, build
from samples.shipments import E1E4, ERROR_VERSIONS, FIELD_NAMES, V1, Invoice, Weight

ANSWER_FILES = sorted(SAMPLES_DIR.rglob("*.answer.json"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # the API's upload limit (design section 6)


def _doc_path(answer_file: Path, answer: AnswerFile) -> Path:
    return answer_file.parent / answer.file


def _pdf_text(path: Path) -> str:
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def _collapse(s: str) -> str:
    return " ".join(s.split())


def _loaded() -> list[tuple[Path, AnswerFile]]:
    return [(p, load_answer(p)) for p in ANSWER_FILES]


def _changed_fields(inv: Invoice, base: Invoice) -> list[str]:
    return [n for n in FIELD_NAMES if getattr(inv.fields, n) != getattr(base.fields, n)]


# --- The set of documents -------------------------------------------------------------------


def test_every_spec_has_a_document_and_an_answer_file() -> None:
    by_folder: dict[str, int] = {}
    for p in ANSWER_FILES:
        by_folder[p.parent.name] = by_folder.get(p.parent.name, 0) + 1
    assert by_folder == {"grid": 28, "submission": 3, "jpg": 1}
    assert len(ANSWER_FILES) == len(ALL_SPECS)

    for path, answer in _loaded():
        doc = _doc_path(path, answer)
        assert doc.exists(), f"{path.name} points at a missing file {answer.file}"
        assert doc.stat().st_size < MAX_UPLOAD_BYTES
        if doc.suffix == ".pdf":
            with pymupdf.open(doc) as pdf:
                assert pdf.page_count == 1


@pytest.mark.parametrize("path", ANSWER_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_answer_file_passes_schema(path: Path) -> None:
    answer = load_answer(path)
    assert answer.doc_id
    if answer.condition == "C1":
        assert answer.degradation is not None
        assert 2.0 <= abs(answer.degradation.rotation_deg) <= 4.0


def test_schema_rejects_an_error_document_that_allows_auto_approve() -> None:
    path = SAMPLES_DIR / "grid" / "E3-C0.answer.json"
    data = load_answer(path).model_dump()
    data["acceptable_outcomes"] = ["auto_approve", "amendment_request"]
    with pytest.raises(ValueError, match="amendment_request or human_review only"):
        AnswerFile.model_validate(data)


def test_answer_files_match_the_ground_truth() -> None:
    """Rebuild each answer from shipments.py (reusing the stored degradation, which needs the
    image pipeline) and compare with the committed file."""
    specs = {(s.folder, s.name): s for s in ALL_SPECS}
    for path, committed in _loaded():
        spec = specs[(path.parent.name, path.name.removesuffix(".answer.json"))]
        stored = committed.degradation
        rebuilt = build_answer(
            spec.invoice,
            doc_id=spec.doc_id,
            file=committed.file,
            condition=spec.condition,
            description=spec.description,
            degradation=Degradation(**stored.model_dump()) if stored else None,
        )
        assert committed == rebuilt, path.name


# --- Planted errors -------------------------------------------------------------------------


@pytest.mark.parametrize("inv", ERROR_VERSIONS, ids=lambda inv: inv.version)
def test_each_error_version_differs_from_v1_in_exactly_one_field(inv: Invoice) -> None:
    assert inv.base_version == "V1"
    assert len(inv.planted_errors) == 1
    assert _changed_fields(inv, V1) == [inv.planted_errors[0].field]


def test_two_error_sample_differs_from_v1_in_exactly_its_two_fields() -> None:
    assert _changed_fields(E1E4, V1) == ["hs_code", "gross_weight"]
    assert [e.field for e in E1E4.planted_errors] == ["hs_code", "gross_weight"]


def test_expected_verdicts_follow_acme_rules() -> None:
    """The expected verdicts agree with rules/acme.yaml for every rule plain code can settle.
    The goods description needs an LLM judgement; both descriptions are specific on purpose."""
    rules = yaml.safe_load((BACKEND_DIR / "rules" / "acme.yaml").read_text(encoding="utf-8"))[
        "rules"
    ]
    names = [rules["consignee"]["registered"], *rules["consignee"]["aliases"]]
    checks = {
        "consignee": lambda v: v in names,
        "hs_code": lambda v: v[:6] in rules["hs_code"]["allowed"],
        "port_of_loading": lambda v: v in rules["port_of_loading"]["allowed"],
        "port_of_discharge": lambda v: v == rules["port_of_discharge"]["value"],
        "incoterms": lambda v: v == rules["incoterms"]["value"],
        "gross_weight": lambda v: isinstance(v, Weight) and v.unit == "KG" and v.amount > 0,
        "invoice_number": lambda v: bool(re.match(rules["invoice_number"]["regex"], v)),
    }
    for spec in ALL_SPECS:
        planted = {e.field for e in spec.invoice.planted_errors}
        for name, check in checks.items():
            value = getattr(spec.invoice.fields, name).value
            ok = value is not None and check(value)
            assert ok == (name not in planted), f"{spec.doc_id} {name}={value!r}"


# --- Text layer -----------------------------------------------------------------------------


def test_only_c0_documents_have_a_text_layer() -> None:
    for path, answer in _loaded():
        doc = _doc_path(path, answer)
        if doc.suffix != ".pdf":
            continue
        chars = len("".join(_pdf_text(doc).split()))
        if answer.condition == "C0":
            assert chars >= 30, f"{doc.name} should have a text layer"
        else:
            assert chars == 0, f"{doc.name} is a scan but has {chars} text characters"


def test_c0_text_layer_shows_every_printed_value_exactly() -> None:
    for path, answer in _loaded():
        if answer.condition != "C0":
            continue
        text = _collapse(_pdf_text(_doc_path(path, answer)))
        for name, field in answer.fields.items():
            if field.printed is not None:
                assert _collapse(field.printed) in text, f"{answer.doc_id}: {name}"


def test_missing_invoice_number_is_really_missing() -> None:
    text = _pdf_text(SAMPLES_DIR / "grid" / "E5-C0.pdf")
    assert "Invoice No." in text  # the label stays; only the value is blank
    assert not re.search(r"INV-\d{4}-\d{4,}", text)


# --- Repeatability --------------------------------------------------------------------------


@pytest.mark.parametrize("seed_key", ["V1-C0", "V1-C1", "V2-C2", "V2-C3"])
def test_generation_is_repeatable(seed_key: str) -> None:
    spec: Spec = next(s for s in ALL_SPECS if s.seed_key == seed_key and not s.as_jpg)
    first, second = build(spec), build(spec)
    assert first.data == second.data
    assert first.answer == second.answer


def test_jpg_is_the_same_scan_as_its_pdf() -> None:
    jpg = (SAMPLES_DIR / "jpg" / "V1-C1.jpg").read_bytes()
    with pymupdf.open(SAMPLES_DIR / "grid" / "V1-C1.pdf") as pdf:
        xref = pdf[0].get_images()[0][0]
        embedded = pdf.extract_image(xref)["image"]
    assert jpg == embedded
    assert (
        load_answer(SAMPLES_DIR / "jpg" / "V1-C1.answer.json").degradation
        == load_answer(SAMPLES_DIR / "grid" / "V1-C1.answer.json").degradation
    )
