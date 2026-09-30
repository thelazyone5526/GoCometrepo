"""The Validator agent (design section 3.4, Phase 6): a verdict for every field the document
type expects, against the customer's YAML rules. Most rules are settled entirely in code
(`app.rules.checkers`); Gemini is only asked about the goods description and any consignee
name that scored in the 85-99 similarity band, in at most one batched call per document.

`validate()` is the entry point. It takes the Extractor's output, the prepared pages (needed
only to judge whether a page missing a value was actually readable), the customer's `RuleSet`
and an `LLMClient`, and returns a `ValidationOutcome`: one `FieldVerdict` per field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.extract import ExtractionOutcome, FieldResult
from app.config import settings
from app.ingest.pages import PreparedPage
from app.llm.client import LLMClient
from app.llm.errors import BudgetExceededError, LLMUnavailableError
from app.llm.prompt_files import load_prompt
from app.rules.checkers import CheckOutcome, check_rule
from app.rules.loader import RuleSet
from app.trust.fields import FIELD_NAMES

# design section 3.4 and 10.1: "the 0.9 readable-page cut-off": a starting value, tuned later
# by the eval. A page at or above this average OCR confidence counts as readable enough that a
# field genuinely missing from it is a mismatch, not merely uncertain.
READABLE_PAGE_CONFIDENCE_THRESHOLD = 0.9

Verdict = Literal["match", "mismatch", "uncertain", "not_applicable"]

# Only these two rule types are ever resolved by Gemini (design section 3.4): an entity_name
# rule in the 85-99 similarity band, or any llm_judgement rule. Both happen to be exactly the
# fields named in the design's example rules file (consignee, goods_description), so the
# batched judgement call's schema names them directly rather than being fully generic.
_JUDGEABLE_FIELDS = ("consignee", "goods_description")


@dataclass(frozen=True)
class FieldVerdict:
    field_name: str
    verdict: Verdict
    found: str | None
    expected: str | None
    rule_id: str | None
    reason: str
    verdict_confidence: float


@dataclass(frozen=True)
class ValidationOutcome:
    fields: dict[str, FieldVerdict]
    # Fields whose rule needed a Gemini judgement but the call failed after retries and (if
    # configured) the fallback -- design section 3.4 task 6: "only those fields become
    # uncertain ('judge unavailable') and the run continues."
    judge_unavailable_fields: tuple[str, ...] = ()


class FieldJudgement(BaseModel):
    verdict: Literal["match", "mismatch"]
    reasoning: str = Field(description="1-2 sentences explaining the call.")
    self_rating: float = Field(ge=0.0, le=1.0, description="How confident you are in this call.")


class ValidationJudgementResult(BaseModel):
    """The batched judgement call's response. Both are optional: a document only asks about
    the fields that actually needed a judgement this run."""

    consignee: FieldJudgement | None = None
    goods_description: FieldJudgement | None = None


def _document_readable(pages: list[PreparedPage]) -> bool:
    """Whether at least one page reads well enough that a field genuinely absent from it is
    a mismatch rather than merely uncertain (design section 3.4 step 2). Any one readable page
    is enough: if the field isn't on the page we *could* read, that's a real absence, not
    something scan quality can be blamed for."""
    return any(p.quality.avg_confidence >= READABLE_PAGE_CONFIDENCE_THRESHOLD for p in pages)


def _uncertain(field_name: str, reason: str, *, confidence: float) -> FieldVerdict:
    return FieldVerdict(
        field_name=field_name,
        verdict="uncertain",
        found=None,
        expected=None,
        rule_id=None,
        reason=reason,
        verdict_confidence=confidence,
    )


def _not_applicable(field_name: str) -> FieldVerdict:
    return FieldVerdict(
        field_name=field_name,
        verdict="not_applicable",
        found=None,
        expected=None,
        rule_id=None,
        reason="not expected for this document type",
        verdict_confidence=1.0,
    )


def _from_check_outcome(
    field_name: str, rule_id: str, outcome: CheckOutcome, *, confidence: float
) -> FieldVerdict:
    assert outcome.verdict in ("match", "mismatch")
    return FieldVerdict(
        field_name=field_name,
        verdict=outcome.verdict,
        found=outcome.found,
        expected=outcome.expected,
        rule_id=rule_id,
        reason=outcome.reason,
        verdict_confidence=confidence,
    )


def _judgement_prompt_text(pending: dict[str, tuple[FieldResult, CheckOutcome]]) -> str:
    """The per-call content for the batched judgement (the system prompt, `validate_v1`,
    carries the fixed instructions; this is what varies document to document)."""
    parts: list[str] = []
    if "consignee" in pending:
        result, outcome = pending["consignee"]
        parts.append(
            "CONSIGNEE NAME\n"
            f'The document reads: "{result.value}"\n'
            f'The registered name on file is: "{outcome.expected}"\n'
            f"Text similarity score: {outcome.score:.0f}%\n"
            "Decide: is the document's name an acceptable variant of the same legal entity "
            "(match), or a misspelling or a different entity (mismatch)?"
        )
    if "goods_description" in pending:
        result, outcome = pending["goods_description"]
        parts.append(
            "GOODS DESCRIPTION\n"
            f'The document reads: "{result.value}"\n'
            f"Criterion: {outcome.expected}\n"
            "Decide: does this description meet the criterion (match), or is it too vague "
            "(mismatch)?"
        )
    return "\n\n".join(parts)


def validate(
    *,
    extraction: ExtractionOutcome,
    pages: list[PreparedPage],
    rules: RuleSet,
    client: LLMClient,
) -> ValidationOutcome:
    expected_fields = rules.expected_fields_for(extraction.document_type)
    document_readable = _document_readable(pages)

    field_verdicts: dict[str, FieldVerdict] = {}
    # Fields whose rule needs a Gemini call, keyed by name, holding what the call needs.
    pending: dict[str, tuple[FieldResult, CheckOutcome]] = {}

    for name in FIELD_NAMES:
        if name not in expected_fields:
            field_verdicts[name] = _not_applicable(name)
            continue

        result = extraction.fields[name]

        # Step 1 (design section 3.4): low confidence is uncertain, whatever the rules say.
        if result.confidence < settings.confidence_threshold:
            field_verdicts[name] = _uncertain(
                name, "could not read reliably", confidence=result.confidence
            )
            continue

        # Step 2: a value missing from an expected field.
        if result.value is None:
            if document_readable:
                field_verdicts[name] = FieldVerdict(
                    field_name=name,
                    verdict="mismatch",
                    found=None,
                    expected="present",
                    rule_id=rules.rules[name].id,
                    reason="expected but not found on the document",
                    verdict_confidence=result.confidence,
                )
            else:
                field_verdicts[name] = _uncertain(
                    name, "missing, but the page could not be read reliably",
                    confidence=result.confidence,
                )
            continue

        # Step 3: the rule decides.
        rule = rules.rules[name]
        outcome = check_rule(name, result.value, rule)
        if outcome.verdict == "needs_judgement":
            pending[name] = (result, outcome)
        else:
            field_verdicts[name] = _from_check_outcome(
                name, rule.id, outcome, confidence=result.confidence
            )

    judge_unavailable: list[str] = []
    if pending:
        judgement = _run_judgement_call(pending, client)
        for name, (result, outcome) in pending.items():
            rule_id = rules.rules[name].id
            if judgement is None:
                field_verdicts[name] = _uncertain(
                    name, "judge unavailable", confidence=result.confidence
                )
                judge_unavailable.append(name)
                continue
            field_judgement = getattr(judgement, name)
            if field_judgement is None:
                # Gemini answered, but skipped this particular field -- treat the same as an
                # unavailable judgement for just this field, rather than trusting a guess.
                field_verdicts[name] = _uncertain(
                    name, "judge unavailable", confidence=result.confidence
                )
                judge_unavailable.append(name)
                continue
            # design section 3.4: "Verdict confidence... for LLM-judged rules, it's the lower
            # of the extraction confidence and the LLM's own rating."
            verdict_confidence = min(result.confidence, field_judgement.self_rating)
            field_verdicts[name] = FieldVerdict(
                field_name=name,
                verdict=field_judgement.verdict,
                found=outcome.found,
                expected=outcome.expected,
                rule_id=rule_id,
                reason=field_judgement.reasoning,
                verdict_confidence=verdict_confidence,
            )

    return ValidationOutcome(
        fields=field_verdicts, judge_unavailable_fields=tuple(judge_unavailable)
    )


def _run_judgement_call(
    pending: dict[str, tuple[FieldResult, CheckOutcome]], client: LLMClient
) -> ValidationJudgementResult | None:
    """The single batched Gemini call for whichever of `_JUDGEABLE_FIELDS` need it. Returns
    None if the call fails after retries and any fallback, or if the run's budget has no room
    left for it -- the caller then marks every pending field 'judge unavailable' rather than
    raising, so the run continues (design section 3.4 task 6)."""
    prompt = load_prompt("validate_v1")
    text = _judgement_prompt_text(pending)
    try:
        result = client.generate(
            agent="validate",
            prompt=prompt,
            text=text,
            response_schema=ValidationJudgementResult,
        )
    except (LLMUnavailableError, BudgetExceededError):
        return None
    return result.value


__all__ = [
    "READABLE_PAGE_CONFIDENCE_THRESHOLD",
    "FieldJudgement",
    "FieldVerdict",
    "ValidationJudgementResult",
    "ValidationOutcome",
    "validate",
]
