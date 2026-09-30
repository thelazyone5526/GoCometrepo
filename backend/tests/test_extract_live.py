"""A `live` smoke run of the Extractor against the three submission samples (Phase 5 verify
list), compared with their answer files. Every miss is meant to be looked at and, if real,
added to `docs/failure-log.md` -- this test reports misses rather than asserting perfection,
since some are expected (the messy scan is *designed* to test uncertainty, not to be read
perfectly).

Marked `live`: skipped by default, run explicitly with `pytest -m live`. Uses the dev response
cache, so a rerun replays the same three calls instead of spending quota again.

Calls: up to 1 per sample (3 total), plus one more only if a field needs the 300 DPI retry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.extract import extract
from app.config import settings
from app.ingest.files import check_upload
from app.ingest.pages import prepare_pages
from app.llm.budget import CallBudget
from app.llm.cache import CachingTransport
from app.llm.client import LLMClient
from app.llm.gemini_transport import GeminiTransport
from app.llm.recorder import CallRecorder
from app.trust.normalise import Weight, normalise_hs_code, normalise_incoterm, normalise_port
from samples.answers import load_answer

pytestmark = pytest.mark.live

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples" / "submission"
LIVE_SMOKE_CACHE_DIR = settings.data_dir / "llm_cache" / "extract_smoke"

SUBMISSION_SAMPLES = [
    "01-clean-correct",
    "02-clean-two-errors",
    "03-messy-scan",
]

_STRUCTURED = {
    "hs_code": normalise_hs_code,
    "port_of_loading": normalise_port,
    "port_of_discharge": normalise_port,
    "incoterms": normalise_incoterm,
}


def _matches_expected(field_name: str, value: str | None, expected) -> bool:
    """Compare the Extractor's raw `value` against the answer file's already-normalised
    `expected.value` (a `WeightValue` for gross_weight, a plain string for everything else --
    see `samples/answers.py`), normalising `value` the same way before comparing."""
    if expected.printed is None:
        return value is None
    if value is None:
        return False
    if field_name == "gross_weight":
        from app.trust.normalise import normalise_weight

        parsed = normalise_weight(value)
        if parsed is None:
            return False
        return parsed.unit == expected.value.unit and abs(parsed.amount - expected.value.amount) < 0.01
    if field_name in _STRUCTURED:
        return _STRUCTURED[field_name](value) == expected.value
    return value.strip() == str(expected.value).strip()


@pytest.fixture
def client() -> LLMClient:
    if not settings.has_gemini_key:
        pytest.skip("GEMINI_API_KEY is not set in backend/.env")
    transport = CachingTransport(
        GeminiTransport(api_key=settings.gemini_api_key), cache_dir=LIVE_SMOKE_CACHE_DIR
    )
    return LLMClient(
        transport=transport,
        primary_model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
        budget=CallBudget(max_calls=6),
        recorder=CallRecorder(),
        run_id="extract-smoke",
    )


@pytest.mark.parametrize("sample_name", SUBMISSION_SAMPLES)
def test_extract_smoke_on_submission_sample(sample_name: str, client: LLMClient, capsys) -> None:
    answer = load_answer(SAMPLES_DIR / f"{sample_name}.answer.json")
    doc_path = SAMPLES_DIR / answer.file

    checked = check_upload(doc_path.read_bytes())
    pages = prepare_pages(checked)
    outcome = extract(pages=pages, client=client)

    misses = []
    for field_name, expected in answer.fields.items():
        result = outcome.fields[field_name]
        ok = _matches_expected(field_name, result.value, expected)
        if not ok:
            misses.append(
                f"  {field_name}: got value={result.value!r} source={result.source_text!r} "
                f"grounding={result.grounding.status} confidence={result.confidence:.2f} "
                f"-- expected printed={expected.printed!r} value={expected.value!r}"
            )

    with capsys.disabled():
        print(f"\n--- {sample_name} ---")
        print(f"document_type: {outcome.document_type}")
        if misses:
            print(f"{len(misses)} field(s) did not match the answer file:")
            for line in misses:
                print(line)
        else:
            print("all 8 fields matched the answer file")
