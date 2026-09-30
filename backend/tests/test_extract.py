"""Tests for the Extractor agent (Phase 5, items 3 and 4): one call, grounding, checks,
confidence, and the 300 DPI targeted retry. Uses `FakeTransport`, so no Gemini quota is spent;
the `live` smoke run against the three submission samples is `test_extract_live.py`.
"""

from __future__ import annotations

from pathlib import Path

from app.agents.extract import extract
from app.agents.schema import ExtractionResult, FieldExtraction, RetryExtractionResult
from app.ingest.files import check_upload
from app.ingest.pages import prepare_pages
from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.fake_transport import FakeTransport
from app.llm.recorder import CallRecorder
from app.llm.transport import RawResponse

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
GRID = SAMPLES_DIR / "grid"

PRIMARY = "gemini-3.8-flash"

# The correct extraction for V1-C0, used as the base for every fake response below.
V1_FIELDS = {
    "consignee": ("ACME Electronics Pte. Ltd.", "ACME Electronics Pte. Ltd."),
    "hs_code": ("847130", "8471.30"),
    "port_of_loading": ("Shanghai", "SHANGHAI, CHINA"),
    "port_of_discharge": ("Singapore", "SINGAPORE"),
    "incoterms": ("CIF", "CIF Singapore"),
    "goods_description": (
        "Portable laptop computers, 14-inch, 16 GB RAM",
        "Portable laptop computers, 14-inch, 16 GB RAM",
    ),
    "gross_weight": ("862.40 KG", "862.40 KGS"),
    "invoice_number": ("INV-2026-00417", "INV-2026-00417"),
}


def _extraction_result(overrides: dict[str, FieldExtraction] | None = None) -> ExtractionResult:
    overrides = overrides or {}
    fields = {}
    for name, (value, source_text) in V1_FIELDS.items():
        fields[name] = overrides.get(
            name, FieldExtraction(value=value, source_text=source_text, page=1, self_rating=0.95)
        )
    return ExtractionResult(document_type="commercial_invoice", **fields)


def _raw_response(result) -> RawResponse:
    return RawResponse(
        text=result.model_dump_json(), input_tokens=500, output_tokens=100, thinking_tokens=0
    )


def make_client(run_id: str = "run-1", *, max_calls: int = 6) -> tuple[LLMClient, FakeTransport]:
    transport = FakeTransport()
    client = LLMClient(
        transport=transport,
        primary_model=PRIMARY,
        fallback_model="",
        budget=CallBudget(max_calls=max_calls),
        recorder=CallRecorder(),
        run_id=run_id,
        sleep=lambda _seconds: None,
    )
    return client, transport


def _prepare_v1_c0():
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    return prepare_pages(checked)


def test_extract_on_the_clean_correct_invoice_grounds_every_field_exactly() -> None:
    client, transport = make_client()
    transport.queue(PRIMARY, _raw_response(_extraction_result()))

    outcome = extract(pages=_prepare_v1_c0(), client=client)

    assert outcome.document_type == "commercial_invoice"
    for name in V1_FIELDS:
        result = outcome.fields[name]
        assert result.grounding.status == "exact", f"{name}: {result.grounding.status}"
        # confidence = min(self_rating=0.95, grounding=1.0, source=1.0) = 0.95: the model's
        # own self-rating is the weakest signal here, and weakest-signal confidence respects
        # that rather than overriding it just because everything else checks out.
        assert result.confidence == 0.95, f"{name}: {result.confidence}"
    assert outcome.retried_fields == ()


def test_extract_flags_an_invented_value_as_not_found_and_low_confidence() -> None:
    client, transport = make_client()
    bad_field = FieldExtraction(
        value="Rotterdam", source_text="Something nobody printed", page=1, self_rating=0.9
    )
    transport.queue(PRIMARY, _raw_response(_extraction_result({"port_of_loading": bad_field})))

    outcome = extract(pages=_prepare_v1_c0(), client=client)

    result = outcome.fields["port_of_loading"]
    assert result.grounding.status == "not_found"
    assert result.confidence <= 0.1


def test_extract_detects_a_quietly_corrected_typo_as_near_with_lower_confidence() -> None:
    """V1-C0 actually prints "ACME Electronics Pte. Ltd.". Simulate the model claiming its
    source text was "ACEM" (a misread) while reporting the value as the corrected "ACME" --
    the classic case of the model quietly "fixing" what it thinks is a typo."""
    client, transport = make_client()
    corrected_field = FieldExtraction(
        value="ACME Electronics Pte. Ltd.",
        source_text="ACEM Electronics Pte. Ltd.",  # not what's actually printed on V1-C0
        page=1,
        self_rating=0.9,
    )
    transport.queue(PRIMARY, _raw_response(_extraction_result({"consignee": corrected_field})))

    outcome = extract(pages=_prepare_v1_c0(), client=client)

    result = outcome.fields["consignee"]
    # The claimed source "ACEM..." is a near miss against the page's real "ACME..." text
    # (one letter off), so grounding is "near" -- and since the model's *value* ("ACME")
    # doesn't match its own *claimed source* ("ACEM"), the value check also fails. Either
    # signal alone would already block a match; together they cap confidence hard.
    assert result.grounding.status == "near"
    assert result.value_check_ok is False
    assert result.confidence <= 0.3


def test_extract_on_a_null_field_reports_it_as_absent_with_full_confidence() -> None:
    client, transport = make_client()
    null_field = FieldExtraction(value=None, source_text=None, page=None, self_rating=0.0)
    transport.queue(PRIMARY, _raw_response(_extraction_result({"invoice_number": null_field})))

    outcome = extract(pages=_prepare_v1_c0(), client=client)

    result = outcome.fields["invoice_number"]
    assert result.value is None
    assert result.grounding.status == "absent"
    assert result.confidence == 1.0


def test_extract_retries_a_not_found_field_at_higher_dpi_and_keeps_the_better_reading() -> None:
    client, transport = make_client()
    # First pass: the model claims a source text that doesn't ground.
    weak_field = FieldExtraction(
        value="847130", source_text="totally illegible smudge", page=1, self_rating=0.4
    )
    transport.queue(PRIMARY, _raw_response(_extraction_result({"hs_code": weak_field})))

    # The retry, at higher DPI, reads it correctly this time.
    retry_result = RetryExtractionResult(
        hs_code=FieldExtraction(value="847130", source_text="8471.30", page=1, self_rating=0.95)
    )
    transport.queue(PRIMARY, _raw_response(retry_result))

    retry_pages_called = {"count": 0}

    def retry_pages_factory():
        retry_pages_called["count"] += 1
        checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
        return prepare_pages(checked, dpi=300)

    outcome = extract(
        pages=_prepare_v1_c0(), client=client, retry_pages_at_higher_dpi=retry_pages_factory
    )

    assert retry_pages_called["count"] == 1
    assert outcome.retried_fields == ("hs_code",)
    result = outcome.fields["hs_code"]
    assert result.grounding.status == "exact"
    assert result.retried is True


def test_extract_never_retries_when_no_field_needs_it() -> None:
    client, transport = make_client()
    transport.queue(PRIMARY, _raw_response(_extraction_result()))

    calls = {"count": 0}

    def retry_pages_factory():
        calls["count"] += 1
        return _prepare_v1_c0()

    outcome = extract(
        pages=_prepare_v1_c0(), client=client, retry_pages_at_higher_dpi=retry_pages_factory
    )

    assert calls["count"] == 0
    assert outcome.retried_fields == ()


def test_extract_does_not_retry_when_the_budget_has_no_room() -> None:
    client, transport = make_client(max_calls=1)  # only the first call fits
    bad_field = FieldExtraction(
        value="Rotterdam", source_text="nothing printed", page=1, self_rating=0.9
    )
    transport.queue(PRIMARY, _raw_response(_extraction_result({"port_of_loading": bad_field})))

    calls = {"count": 0}

    def retry_pages_factory():
        calls["count"] += 1
        return _prepare_v1_c0()

    outcome = extract(
        pages=_prepare_v1_c0(), client=client, retry_pages_at_higher_dpi=retry_pages_factory
    )

    assert calls["count"] == 0  # the budget check happened before the retry did any work
    assert outcome.retried_fields == ()
    assert outcome.fields["port_of_loading"].grounding.status == "not_found"
