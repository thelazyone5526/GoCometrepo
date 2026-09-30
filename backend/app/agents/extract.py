"""The Extractor agent (design section 3.3): one Gemini call for all 8 fields, then grounding,
value and format checks, and confidence, all in code -- plus one targeted retry at 300 DPI for
fields that didn't ground well the first time.

`extract()` is the entry point. It takes prepared pages (`app.ingest.pages.PreparedPage`) and
an `LLMClient`, and returns an `ExtractionOutcome`: one `FieldResult` per field, each carrying
everything the Validator and the UI need -- the value, its source, how it grounded, and its
confidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingest.pages import PreparedPage
from app.llm.client import LLMClient
from app.llm.prompt_files import load_prompt
from app.trust.confidence import ConfidenceInputs, compute_confidence
from app.trust.fields import FIELD_NAMES, DocumentType
from app.trust.formats import format_ok
from app.trust.grounding import GroundingResult, ground_field
from app.trust.value_check import value_matches_source

from .schema import ExtractionResult, FieldExtraction, RetryExtractionResult

# design section 3.3 step 7: retry fields that are `not_found` or `near`.
_RETRY_GROUNDING_STATUSES = frozenset({"not_found", "near"})

# Phase 3's `render_pdf_pages` default; the retry re-renders at this instead (design section
# 3.3 step 7: "one extra Gemini call at 300 DPI").
RETRY_DPI = 300


@dataclass(frozen=True)
class FieldResult:
    field_name: str
    value: str | None
    source_text: str | None
    page: int | None
    self_rating: float
    grounding: GroundingResult
    value_check_ok: bool
    format_check_ok: bool
    confidence: float
    retried: bool = False


@dataclass(frozen=True)
class ExtractionOutcome:
    document_type: DocumentType
    fields: dict[str, FieldResult]
    retried_fields: tuple[str, ...] = ()


def _page_spans(pages: list[PreparedPage]) -> list[list]:
    return [p.spans for p in pages]


def _page_images_png(pages: list[PreparedPage]) -> list[bytes]:
    return [p.to_png() for p in pages]


def _build_field_result(
    field_name: str, extraction: FieldExtraction, *, pages_spans: list[list], retried: bool
) -> FieldResult:
    grounding = ground_field(
        extraction.source_text, pages_spans=pages_spans, hinted_page=extraction.page
    )

    if extraction.value is None:
        # A null value is only ever a correct answer if the field is genuinely absent, which
        # grounding already confirms (source_text is also None, so ground_field returns
        # "absent"). There's nothing to value- or format-check, and "how sure are you about
        # this value" is meaningless when there is no value: the prompt asks the model for a
        # self_rating of 0 on a null field, but that's a statement about the value, not about
        # whether the field is genuinely absent, so it must not be treated as a limiting
        # confidence signal here. Whether an absent field is a mismatch or merely uncertain
        # is the Validator's call (design section 3.4), based on whether the page is
        # readable -- not on the Extractor's confidence.
        value_check_ok = True
        format_check_ok = True
        self_rating_for_confidence = 1.0
    else:
        source_for_check = extraction.source_text if extraction.source_text is not None else ""
        value_check_ok = value_matches_source(field_name, extraction.value, source_for_check)
        format_check_ok = format_ok(field_name, extraction.value)
        self_rating_for_confidence = extraction.self_rating

    confidence = compute_confidence(
        ConfidenceInputs(
            self_rating=self_rating_for_confidence,
            grounding=grounding,
            value_check_ok=value_check_ok,
            format_ok=format_check_ok,
        )
    )

    return FieldResult(
        field_name=field_name,
        value=extraction.value,
        source_text=extraction.source_text,
        page=extraction.page,
        self_rating=extraction.self_rating,
        grounding=grounding,
        value_check_ok=value_check_ok,
        format_check_ok=format_check_ok,
        confidence=confidence,
        retried=retried,
    )


def _needs_retry(result: FieldResult) -> bool:
    return result.value is not None and result.grounding.status in _RETRY_GROUNDING_STATUSES


def extract(
    *,
    pages: list[PreparedPage],
    client: LLMClient,
    retry_pages_at_higher_dpi: object = None,
) -> ExtractionOutcome:
    """Run the Extractor on one document's prepared pages.

    `retry_pages_at_higher_dpi`, when given, is a callable `() -> list[PreparedPage]` that
    re-renders and re-OCRs the same document at `RETRY_DPI` -- injected rather than done
    inline here, since it needs the original upload bytes that this function doesn't itself
    hold (`app.graph.extract_node`, Phase 8, is what wires the two together). If it's not
    given, or the budget has no room left, no retry is attempted and any weakly-grounded
    field is simply reported as such.
    """
    prompt = load_prompt("extract_v2")
    pages_spans = _page_spans(pages)

    result = client.generate(
        agent="extract",
        prompt=prompt,
        text="Extract the document type and all 8 fields from these page images.",
        images=_page_images_png(pages),
        response_schema=ExtractionResult,
    )
    extraction: ExtractionResult = result.value

    fields = {
        name: _build_field_result(
            name, extraction.field(name), pages_spans=pages_spans, retried=False
        )
        for name in FIELD_NAMES
    }

    retry_candidates = [name for name, r in fields.items() if _needs_retry(r)]
    retried_fields: tuple[str, ...] = ()
    if retry_candidates and retry_pages_at_higher_dpi is not None and client.calls_remaining > 0:
        retry_pages = retry_pages_at_higher_dpi()
        retry_spans = _page_spans(retry_pages)
        retry_prompt = load_prompt("extract_retry_v1")
        field_list = ", ".join(retry_candidates)
        retry_result = client.generate(
            agent="extract_retry",
            prompt=retry_prompt,
            text=f"Re-read only these fields: {field_list}.",
            images=_page_images_png(retry_pages),
            response_schema=RetryExtractionResult,
        )
        retry_extraction: RetryExtractionResult = retry_result.value

        improved: list[str] = []
        for name in retry_candidates:
            retry_field = retry_extraction.field(name)
            if retry_field is None:
                continue
            candidate = _build_field_result(
                name, retry_field, pages_spans=retry_spans, retried=True
            )
            # "Keep whichever reading grounds better" (design section 3.3 step 7): only
            # replace the original if the retry's grounding is strictly better, so a retry
            # that finds nothing new never makes things worse.
            if _grounding_rank(candidate.grounding.status) > _grounding_rank(
                fields[name].grounding.status
            ):
                fields[name] = candidate
                improved.append(name)
        retried_fields = tuple(improved)

    return ExtractionOutcome(
        document_type=extraction.document_type, fields=fields, retried_fields=retried_fields
    )


_GROUNDING_RANK = {"not_found": 0, "near": 1, "exact": 2, "absent": 2}


def _grounding_rank(status: str) -> int:
    return _GROUNDING_RANK[status]
