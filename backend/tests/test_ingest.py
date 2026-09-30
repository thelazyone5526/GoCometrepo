"""Tests for page preparation (Phase 3, item 2): file checks, rendering, text layer, scan
clean-up and OCR. Run against the generated samples in samples/grid, samples/submission and
samples/jpg, whose exact printed text and degradation parameters are in each .answer.json.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from fpdf import FPDF
from PIL import Image

from app.ingest.cleanup import clean_scan, estimate_skew_deg, straighten, to_grayscale
from app.ingest.files import (
    FileTooLarge,
    TooManyPages,
    UnsupportedFileType,
    check_upload,
    count_pdf_pages,
    file_hash,
    sniff_file_type,
    store_upload,
    upload_dir,
)
from app.ingest.ocr import run_ocr
from app.ingest.pages import (
    LOW_CONFIDENCE_SPAN_THRESHOLD,
    page_quality,
    prepare_page,
    prepare_pages,
)
from app.ingest.render import render_image_page, render_pages, render_pdf_pages
from app.ingest.textlayer import is_digital_page, page_char_count, text_layer_spans
from samples.answers import load_answer

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
GRID = SAMPLES_DIR / "grid"
JPG = SAMPLES_DIR / "jpg"
SUBMISSION = SAMPLES_DIR / "submission"


def _load(doc_path: Path) -> tuple[bytes, dict]:
    data = doc_path.read_bytes()
    answer = load_answer(doc_path.with_name(doc_path.stem + ".answer.json"))
    return data, answer


def _collapse(s: str) -> str:
    return " ".join(s.split())


# --- File checks (design section 6 upload limits) --------------------------------------------


def test_sniff_file_type_reads_magic_bytes_not_extension() -> None:
    assert sniff_file_type((GRID / "V1-C0.pdf").read_bytes()) == "pdf"
    assert sniff_file_type((JPG / "V1-C1.jpg").read_bytes()) == "jpg"
    png_bytes = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png_bytes, "PNG")
    assert sniff_file_type(png_bytes.getvalue()) == "png"
    assert sniff_file_type(b"not a real document") is None


def test_check_upload_rejects_wrong_file_type() -> None:
    with pytest.raises(UnsupportedFileType):
        check_upload(b"plain text, not one of the three accepted types")


def test_check_upload_rejects_oversize_file() -> None:
    oversize = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024)
    with pytest.raises(FileTooLarge):
        check_upload(oversize)


def test_check_upload_rejects_too_many_pages() -> None:
    pdf = FPDF()
    for _ in range(6):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(text="page")
    data = bytes(pdf.output())
    assert count_pdf_pages(data) == 6
    with pytest.raises(TooManyPages):
        check_upload(data)


def test_check_upload_accepts_a_five_page_pdf() -> None:
    pdf = FPDF()
    for _ in range(5):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(text="page")
    data = bytes(pdf.output())
    checked = check_upload(data)
    assert checked.page_count == 5
    assert checked.file_type == "pdf"


def test_check_upload_accepts_every_committed_sample() -> None:
    for path in [*GRID.glob("*.pdf"), *SUBMISSION.glob("*.pdf"), *JPG.glob("*.jpg")]:
        checked = check_upload(path.read_bytes())
        assert checked.file_type in ("pdf", "jpg")
        assert checked.page_count == 1


def test_file_hash_is_stable_and_content_addressed() -> None:
    data = (GRID / "V1-C0.pdf").read_bytes()
    assert file_hash(data) == file_hash(data)
    assert file_hash(data) != file_hash(data + b"\x00")


def test_store_upload_writes_under_hash_named_folder(tmp_path: Path) -> None:
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    path = store_upload(checked, data_dir=tmp_path)
    assert path == upload_dir(checked.file_hash, data_dir=tmp_path) / "original.pdf"
    assert path.read_bytes() == checked.data
    # A second store of the identical file doesn't error and points at the same place.
    assert store_upload(checked, data_dir=tmp_path) == path


# --- Rendering --------------------------------------------------------------------------------


def test_render_pdf_pages_matches_the_answer_files_pixel_size() -> None:
    for doc_id in ["V1-C0", "V1-C1", "V1-C3"]:
        data, answer = _load(GRID / f"{doc_id}.pdf")
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            dpi = answer.degradation.dpi if answer.degradation else 200
            pages = render_pdf_pages(doc, dpi=dpi)
            assert len(pages) == 1
            page = pages[0]
            expected_w = answer.degradation.width_px if answer.degradation else None
            expected_h = answer.degradation.height_px if answer.degradation else None
            if expected_w is not None:
                assert page.width_px == expected_w
                assert page.height_px == expected_h
            assert page.image.shape == (page.height_px, page.width_px, 3)


def test_render_image_page_decodes_jpg_to_rgb() -> None:
    page = render_image_page((JPG / "V1-C1.jpg").read_bytes())
    assert page.pdf_page is None
    assert page.image.ndim == 3
    assert page.image.shape[2] == 3


def test_render_pages_dispatches_on_sniffed_type() -> None:
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    pages = render_pages(checked)
    assert len(pages) == 1
    assert pages[0].pdf_page is not None

    checked = check_upload((JPG / "V1-C1.jpg").read_bytes())
    pages = render_pages(checked)
    assert len(pages) == 1
    assert pages[0].pdf_page is None


# --- Text layer (digital PDFs) ----------------------------------------------------------------


@pytest.mark.parametrize("doc_id", ["V1-C0", "V2-C0", "E1E4"])
def test_c0_pages_are_digital(doc_id: str) -> None:
    path = GRID / f"{doc_id}.pdf" if doc_id != "E1E4" else SUBMISSION / "02-clean-two-errors.pdf"
    with pymupdf.open(path) as doc:
        assert is_digital_page(doc[0])
        assert page_char_count(doc[0]) >= 30


@pytest.mark.parametrize("doc_id", ["V1-C1", "V1-C2", "V1-C3", "V2-C3"])
def test_scans_are_not_digital(doc_id: str) -> None:
    with pymupdf.open(GRID / f"{doc_id}.pdf") as doc:
        assert not is_digital_page(doc[0])
        assert page_char_count(doc[0]) == 0


def test_c0_text_layer_spans_contain_the_consignee_name_exactly_at_full_confidence() -> None:
    data, answer = _load(GRID / "V1-C0.pdf")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        spans = text_layer_spans(doc[0], dpi=200)
    consignee = answer.fields["consignee"].printed
    assert consignee is not None
    match = next((s for s in spans if consignee in s.text), None)
    assert match is not None, f"consignee {consignee!r} not found in any span"
    assert match.confidence == 1.0


def test_c0_text_layer_spans_cover_every_printed_field_value() -> None:
    data, answer = _load(GRID / "V1-C0.pdf")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        spans = text_layer_spans(doc[0], dpi=200)
    joined = _collapse(" ".join(s.text for s in spans))
    for name, field in answer.fields.items():
        if field.printed is not None:
            assert _collapse(field.printed) in joined, f"{name} missing from text-layer spans"


def test_text_layer_span_boxes_scale_with_dpi() -> None:
    with pymupdf.open(GRID / "V1-C0.pdf") as doc:
        spans_200 = text_layer_spans(doc[0], dpi=200)
        spans_300 = text_layer_spans(doc[0], dpi=300)
    box_200 = next(s.box for s in spans_200 if "INV-2026-00417" in s.text)
    box_300 = next(s.box for s in spans_300 if "INV-2026-00417" in s.text)
    ratio = 300 / 200
    for a, b in zip(box_200, box_300, strict=True):
        assert b == pytest.approx(a * ratio, rel=1e-6)


# --- Scan clean-up (OpenCV) --------------------------------------------------------------------


def test_straighten_brings_c1_within_half_a_degree_of_level(ocr_engine) -> None:
    data, answer = _load(GRID / "V1-C1.pdf")
    assert answer.degradation is not None
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        page = render_pdf_pages(doc, dpi=200)[0]
    gray = to_grayscale(page.image)
    measured = estimate_skew_deg(gray)
    assert abs(measured - answer.degradation.rotation_deg) <= 0.5

    _, corrected_angle = straighten(gray)
    assert corrected_angle == measured


@pytest.mark.parametrize("doc_id", ["V1-C1", "V1-C2", "V1-C3"])
def test_clean_scan_returns_a_single_channel_image(doc_id: str) -> None:
    with pymupdf.open(GRID / f"{doc_id}.pdf") as doc:
        page = render_pdf_pages(doc, dpi=200)[0]
    cleaned, _angle = clean_scan(page.image)
    assert cleaned.ndim == 2
    assert cleaned.shape[:2] == page.image.shape[:2]


# --- OCR (RapidOCR) -----------------------------------------------------------------------------


@pytest.mark.parametrize("doc_id", ["V1-C1", "V1-C2", "V1-C3", "V2-C3"])
def test_ocr_spans_contain_the_invoice_number(doc_id: str, ocr_engine) -> None:
    data, answer = _load(GRID / f"{doc_id}.pdf")
    invoice_number = answer.fields["invoice_number"].printed
    assert invoice_number is not None  # every doc_id here keeps its invoice number (not E5)
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        page = render_pdf_pages(doc, dpi=200)[0]
    cleaned, _angle = clean_scan(page.image)
    spans = run_ocr(cleaned, engine=ocr_engine)
    assert any(invoice_number in s.text for s in spans), (
        f"{doc_id}: {invoice_number!r} not read from any OCR span"
    )


def test_ocr_on_a_blank_image_returns_no_spans(ocr_engine) -> None:
    import numpy as np

    blank = np.full((400, 300), 255, dtype=np.uint8)
    assert run_ocr(blank, engine=ocr_engine) == []


# --- Page assembly (pages.py): quality stats and dispatch --------------------------------------


def test_page_quality_of_no_spans_is_worst_case() -> None:
    quality = page_quality([])
    assert quality.avg_confidence == 0.0
    assert quality.low_confidence_share == 1.0


def test_page_quality_averages_and_counts_low_confidence_spans() -> None:
    from app.ingest.textlayer import TextSpan

    spans = [
        TextSpan(text="a", box=(0, 0, 1, 1), confidence=1.0),
        TextSpan(text="b", box=(0, 0, 1, 1), confidence=0.5),
    ]
    quality = page_quality(spans, low_confidence_threshold=LOW_CONFIDENCE_SPAN_THRESHOLD)
    assert quality.avg_confidence == pytest.approx(0.75)
    assert quality.low_confidence_share == pytest.approx(0.5)


def test_prepare_page_uses_text_layer_for_a_digital_pdf_page() -> None:
    with pymupdf.open(GRID / "V1-C0.pdf") as doc:
        rendered = render_pdf_pages(doc, dpi=200)[0]
        prepared = prepare_page(rendered)
    assert prepared.source == "text_layer"
    assert prepared.quality.avg_confidence == 1.0
    assert prepared.prepare_seconds >= 0.0


def test_prepare_page_uses_ocr_for_a_scan(ocr_engine) -> None:
    with pymupdf.open(GRID / "V1-C1.pdf") as doc:
        rendered = render_pdf_pages(doc, dpi=200)[0]
        prepared = prepare_page(rendered, ocr_engine=ocr_engine)
    assert prepared.source == "ocr"
    assert prepared.prepare_seconds > 0.0
    assert len(prepared.spans) > 0


def test_prepare_page_uses_ocr_for_an_uploaded_jpg(ocr_engine) -> None:
    rendered = render_image_page((JPG / "V1-C1.jpg").read_bytes())
    prepared = prepare_page(rendered, ocr_engine=ocr_engine)
    assert prepared.source == "ocr"
    assert prepared.index == 0


@pytest.mark.parametrize(
    ("doc_id", "expected_source"),
    [("V1-C0", "text_layer"), ("V1-C1", "ocr"), ("V2-C3", "ocr")],
)
def test_prepare_pages_end_to_end_on_grid_samples(
    doc_id: str, expected_source: str, ocr_engine
) -> None:
    data, answer = _load(GRID / f"{doc_id}.pdf")
    checked = check_upload(data)
    pages = prepare_pages(checked, ocr_engine=ocr_engine)
    assert len(pages) == 1
    page = pages[0]
    assert page.source == expected_source
    invoice_number = answer.fields["invoice_number"].printed
    if invoice_number is not None:
        assert any(invoice_number in s.text for s in page.spans)
    # Time per page is recorded, for the write-up's latency section (design section 3.3).
    assert page.prepare_seconds > 0.0
    # Boxes refer to the stored page image: none should fall outside its bounds.
    h, w = page.image.shape[:2]
    for span in page.spans:
        x0, y0, x1, y1 = span.box
        assert -1.0 <= x0 <= x1 <= w + 1.0
        assert -1.0 <= y0 <= y1 <= h + 1.0


def test_prepare_pages_end_to_end_on_the_jpg_upload(ocr_engine) -> None:
    checked = check_upload((JPG / "V1-C1.jpg").read_bytes())
    pages = prepare_pages(checked, ocr_engine=ocr_engine)
    assert len(pages) == 1
    assert pages[0].source == "ocr"
    _, answer = _load(JPG / "V1-C1.jpg")
    invoice_number = answer.fields["invoice_number"].printed
    assert invoice_number is not None
    assert any(invoice_number in s.text for s in pages[0].spans)


def test_e5_missing_invoice_number_is_skipped(ocr_engine) -> None:
    """E5's invoice number is null (design section 10.1); nothing to look for on the page."""
    _, answer = _load(GRID / "E5-C0.pdf")
    assert answer.fields["invoice_number"].printed is None
