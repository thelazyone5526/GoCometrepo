"""Tests for the Validator agent (Phase 6, item 5): the verdict order from design section
3.4, using `FakeTransport` so no Gemini quota is spent."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.extract import ExtractionOutcome, FieldResult
from app.agents.validate import FieldJudgement, ValidationJudgementResult, validate
from app.config import settings
from app.ingest.files import check_upload
from app.ingest.pages import PageQuality, PreparedPage, prepare_pages
from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.fake_transport import FakeTransport
from app.llm.recorder import CallRecorder
from app.llm.transport import RawResponse
from app.rules.loader import RuleSet, load_rules_for_customer
from app.trust.fields import FIELD_NAMES
from app.trust.grounding import GroundingResult

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
GRID = SAMPLES_DIR / "grid"
RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
PRIMARY = "gemini-3.8-flash"

ACME_RULES = load_rules_for_customer("acme", rules_dir=RULES_DIR)

# The correct values for V1, keyed by field name -- enough to build a fully matching
# extraction outcome without needing a real Gemini call.
V1_VALUES = {
    "consignee": "ACME Electronics Pte. Ltd.",
    "hs_code": "847130",
    "port_of_loading": "Shanghai",
    "port_of_discharge": "Singapore",
    "incoterms": "CIF",
    "goods_description": "Portable laptop computers, 14-inch, 16 GB RAM",
    "gross_weight": "862.40 KG",
    "invoice_number": "INV-2026-00417",
}


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


def make_client_with_default_judgement(
    run_id: str = "run-1",
) -> tuple[LLMClient, FakeTransport]:
    """A client whose one judgement call (consignee and goods_description both match) is
    already queued -- for tests where the point is some *other* field, not the judgement call
    itself. `V1_VALUES`'s consignee and goods_description are both exact matches, so this
    default is a faithful stand-in for "the judge would say yes" in every such test."""
    client, transport = make_client(run_id)
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
            goods_description=FieldJudgement(
                verdict="match", reasoning="specific", self_rating=1.0
            ),
        ),
    )
    return client, transport


def _field_result(
    value: str | None, *, confidence: float = 0.95, grounding_status: str = "exact"
) -> FieldResult:
    return FieldResult(
        field_name="_",
        value=value,
        source_text=value,
        page=1,
        self_rating=confidence,
        grounding=GroundingResult(status=grounding_status, score=100.0 if value else 0.0),
        value_check_ok=True,
        format_check_ok=True,
        confidence=confidence,
    )


def _extraction_outcome(overrides: dict[str, FieldResult] | None = None) -> ExtractionOutcome:
    overrides = overrides or {}
    fields = {
        name: overrides.get(name, _field_result(V1_VALUES[name])) for name in FIELD_NAMES
    }
    return ExtractionOutcome(document_type="commercial_invoice", fields=fields)


def _readable_pages() -> list[PreparedPage]:
    checked = check_upload((GRID / "V1-C0.pdf").read_bytes())
    return prepare_pages(checked)


def _unreadable_page() -> list[PreparedPage]:
    import numpy as np

    return [
        PreparedPage(
            index=0,
            image=np.zeros((10, 10, 3), dtype="uint8"),
            source="ocr",
            spans=[],
            quality=PageQuality(avg_confidence=0.2, low_confidence_share=1.0),
            prepare_seconds=0.0,
        )
    ]


def _judgement_response(**fields: FieldJudgement) -> RawResponse:
    result = ValidationJudgementResult(**fields)
    return RawResponse(
        text=result.model_dump_json(), input_tokens=200, output_tokens=50, thinking_tokens=0
    )


# --- Verdict order: step 1, low confidence is uncertain whatever the rules say -----------------


def test_low_confidence_field_is_uncertain_even_if_the_rule_would_pass() -> None:
    client, _transport = make_client_with_default_judgement()
    below_threshold = settings.confidence_threshold - 0.1
    extraction = _extraction_outcome(
        {"port_of_discharge": _field_result("Singapore", confidence=below_threshold)}
    )
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    verdict = outcome.fields["port_of_discharge"]
    assert verdict.verdict == "uncertain"


def test_no_field_below_the_threshold_is_ever_a_match() -> None:
    """Sweep every non-judged field just under the threshold and confirm none of them can
    slip through as a match."""
    below_threshold = settings.confidence_threshold - 0.01
    for name in FIELD_NAMES:
        if name in ("consignee", "goods_description"):
            continue  # these go to judgement, tested separately
        # goods_description's rule type (llm_judgement) always needs a judgement call,
        # whatever the value, so a fresh client with one queued response is needed each time.
        client, _transport = make_client_with_default_judgement()
        extraction = _extraction_outcome(
            {name: _field_result(V1_VALUES[name], confidence=below_threshold)}
        )
        outcome = validate(
            extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
        )
        assert outcome.fields[name].verdict != "match", name


# --- Verdict order: step 2, null value on a readable vs unreadable page ------------------------


def test_null_value_on_a_readable_page_is_a_mismatch() -> None:
    client, _transport = make_client_with_default_judgement()
    null_field = _field_result(None, grounding_status="absent")
    extraction = _extraction_outcome({"invoice_number": null_field})
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    verdict = outcome.fields["invoice_number"]
    assert verdict.verdict == "mismatch"
    assert verdict.found is None


def test_null_value_on_an_unreadable_page_is_uncertain() -> None:
    client, _transport = make_client_with_default_judgement()
    null_field = _field_result(None, grounding_status="absent")
    extraction = _extraction_outcome({"invoice_number": null_field})
    outcome = validate(
        extraction=extraction, pages=_unreadable_page(), rules=ACME_RULES, client=client
    )
    assert outcome.fields["invoice_number"].verdict == "uncertain"


# --- Verdict order: step 3, otherwise the rule decides ------------------------------------------


def test_a_document_with_every_field_correct_matches_everywhere() -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
            goods_description=FieldJudgement(
                verdict="match", reasoning="specific", self_rating=1.0
            ),
        ),
    )
    extraction = _extraction_outcome()
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    for name in FIELD_NAMES:
        assert outcome.fields[name].verdict == "match", name


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("hs_code", "850440"),
        ("incoterms", "FOB"),
        ("gross_weight", "1900.97 LBS"),
        ("invoice_number", "TBD"),
        ("port_of_loading", "Rotterdam"),
    ],
)
def test_a_field_that_fails_its_rule_is_a_mismatch(field: str, bad_value: str) -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
            goods_description=FieldJudgement(
                verdict="match", reasoning="specific", self_rating=1.0
            ),
        ),
    )
    extraction = _extraction_outcome({field: _field_result(bad_value)})
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    assert outcome.fields[field].verdict == "mismatch"


# --- The batched judgement call: consignee and goods_description -------------------------------


def test_a_close_consignee_variant_that_gemini_accepts_is_a_match() -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(
                verdict="match",
                reasoning="Pte Ltd is the same as Private Limited",
                self_rating=0.95,
            ),
            goods_description=FieldJudgement(
                verdict="match", reasoning="specific", self_rating=1.0
            ),
        ),
    )
    extraction = _extraction_outcome(
        {"consignee": _field_result("ACME Electronics Private Limited")}
    )
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    verdict = outcome.fields["consignee"]
    assert verdict.verdict == "match"
    # design section 3.4: verdict confidence for LLM-judged rules is the lower of extraction
    # confidence and the LLM's own rating.
    assert verdict.verdict_confidence == pytest.approx(min(0.95, 0.95))


def test_a_close_consignee_variant_that_gemini_rejects_is_a_mismatch() -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(
                verdict="mismatch", reasoning="different company entirely", self_rating=0.9
            ),
            goods_description=FieldJudgement(
                verdict="match", reasoning="specific", self_rating=1.0
            ),
        ),
    )
    extraction = _extraction_outcome({"consignee": _field_result("ACEM Electronics Pte. Ltd.")})
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    assert outcome.fields["consignee"].verdict == "mismatch"


def test_a_vague_goods_description_that_gemini_rejects_is_a_mismatch() -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _judgement_response(
            consignee=FieldJudgement(verdict="match", reasoning="exact", self_rating=1.0),
            goods_description=FieldJudgement(
                verdict="mismatch", reasoning="too vague for customs", self_rating=0.9
            ),
        ),
    )
    extraction = _extraction_outcome({"goods_description": _field_result("Electronics")})
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    assert outcome.fields["goods_description"].verdict == "mismatch"


def test_judge_unavailable_makes_only_the_judged_fields_uncertain() -> None:
    """design section 3.4 task 6: if the judgement call fails, only the fields it covers
    become uncertain, and the run continues -- every other verdict is unaffected.

    `V1_VALUES`'s consignee is an exact match to the registered name, so it never itself
    reaches judgement (`check_entity_name` settles it in code); `goods_description`'s rule
    type is `llm_judgement`, which always needs one. So a failed judgement call here only
    ever affects `goods_description`, not `consignee` -- which is itself proof that the
    Validator only calls Gemini for fields that actually need it (design section 3.4:
    "Gemini only for the goods description and unsettled consignee names")."""
    client, transport = make_client(max_calls=1)  # only room for one call, which fails
    bad = RawResponse(text="not valid json", input_tokens=10, output_tokens=0, thinking_tokens=0)
    transport.queue(PRIMARY, bad, bad, bad)  # one per attempt: 1 try + 2 retries, all bad JSON
    extraction = _extraction_outcome()
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    assert outcome.fields["consignee"].verdict == "match"
    assert outcome.fields["goods_description"].verdict == "uncertain"
    assert outcome.judge_unavailable_fields == ("goods_description",)
    for name in FIELD_NAMES:
        if name != "goods_description":
            assert outcome.fields[name].verdict == "match"


def test_judge_unavailable_for_an_unresolved_consignee_too() -> None:
    """The same failure, but with a consignee close enough to need judgement (not settled by
    code), confirming *that* path also degrades to 'judge unavailable' rather than raising."""
    client, transport = make_client(max_calls=1)
    bad = RawResponse(text="not valid json", input_tokens=10, output_tokens=0, thinking_tokens=0)
    transport.queue(PRIMARY, bad, bad, bad)
    # One letter off from the registered name: 85-99% similar, so it needs a judgement call
    # rather than being settled by code (unlike a listed alias, which matches exactly).
    extraction = _extraction_outcome({"consignee": _field_result("ACME Electronic Pte. Ltd.")})
    outcome = validate(
        extraction=extraction, pages=_readable_pages(), rules=ACME_RULES, client=client
    )
    assert outcome.fields["consignee"].verdict == "uncertain"
    assert outcome.fields["goods_description"].verdict == "uncertain"
    assert set(outcome.judge_unavailable_fields) == {"consignee", "goods_description"}


# --- Schema routing: a field the document type doesn't expect is not_applicable ----------------


def test_a_field_not_expected_for_the_document_type_is_not_applicable() -> None:
    raw = {
        "customer_id": "acme",
        "customer_name": "ACME Electronics Pte. Ltd.",
        "document_types": {
            "bill_of_lading": {"expected_fields": ["port_of_loading", "port_of_discharge"]}
        },
        "rules": {
            "port_of_loading": {"id": "R-POL-1", "type": "in_list", "allowed": ["Shanghai"]},
            "port_of_discharge": {"id": "R-POD-1", "type": "equals", "value": "Singapore"},
        },
    }
    from app.rules.loader import RuleSet as _RuleSet

    bol_rules: RuleSet = _RuleSet.model_validate(raw)
    client, _transport = make_client()
    extraction = _extraction_outcome()
    outcome = validate(
        extraction=ExtractionOutcome(document_type="bill_of_lading", fields=extraction.fields),
        pages=_readable_pages(),
        rules=bol_rules,
        client=client,
    )
    for name in FIELD_NAMES:
        if name in ("port_of_loading", "port_of_discharge"):
            assert outcome.fields[name].verdict != "not_applicable"
        else:
            assert outcome.fields[name].verdict == "not_applicable"
