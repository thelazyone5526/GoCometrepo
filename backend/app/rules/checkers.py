"""One checker per rule type (design section 3.4): `equals`, `in_list`, `pattern`, `quantity`,
`entity_name` and `llm_judgement`. Each returns a `CheckOutcome`: a verdict settled in code
(`match` / `mismatch`), or `needs_judgement` when only Gemini can decide -- an `entity_name`
rule scoring 85-99, or any `llm_judgement` rule. `app.agents.validate.validate()` is what
collects every `needs_judgement` field into one batched Gemini call and resolves them.

Every checker takes the field's *raw* extracted value (a string) and re-normalises it here,
using the same normalisers `trust/normalise.py` and `trust/value_check.py` already use, so a
checker never has to trust that the Extractor's raw string already came out in canonical form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from app.trust.normalise import (
    normalise_hs_code,
    normalise_incoterm,
    normalise_port,
    normalise_weight,
    text_comparison_key,
)

from .loader import (
    EntityNameRule,
    EqualsRule,
    InListRule,
    LlmJudgementRule,
    PatternRule,
    QuantityRule,
    Rule,
)

CheckVerdict = Literal["match", "mismatch", "needs_judgement"]

# design section 3.4: "A similarity score of 85-99 goes to Gemini... Below 85 is a mismatch."
ENTITY_NAME_JUDGEMENT_MIN = 85.0
ENTITY_NAME_EXACT_MIN = 100.0

# "Normalise suffixes and punctuation first: 'Pte. Ltd.' = 'Private Limited', 'Ltd' =
# 'Limited', 'Co.' = 'Company'." Order matters only in that each pattern is anchored to a
# whole word, so expanding "Pte." to "Private" first can't accidentally feed a second match.
_SUFFIX_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bPte\.?\b", re.IGNORECASE), "Private"),
    (re.compile(r"\bLtd\.?\b", re.IGNORECASE), "Limited"),
    (re.compile(r"\bCo\.?\b", re.IGNORECASE), "Company"),
)


def _expand_entity_suffixes(name: str) -> str:
    text = name
    for pattern, replacement in _SUFFIX_EXPANSIONS:
        text = pattern.sub(replacement, text)
    return text


def entity_name_key(name: str) -> str:
    """The comparison key for a company name: legal suffixes expanded first, then the usual
    case/punctuation-insensitive key (`trust.normalise.text_comparison_key`). Kept separate
    from that function (progress.md, "For Phase 6"): every other text field is compared
    literally, but company names commonly abbreviate their legal suffix, and "Pte Ltd" must
    compare equal to "Private Limited" only for this one rule type."""
    return text_comparison_key(_expand_entity_suffixes(name))


@dataclass(frozen=True)
class CheckOutcome:
    verdict: CheckVerdict
    found: str | None
    expected: str | None
    reason: str
    # Only meaningful for entity_name; kept on the outcome (rather than a separate return
    # value) so the judgement prompt and the failure log can both cite the actual number.
    score: float | None = None


def _normalise_for_field(field_name: str, value: str) -> str | None:
    normaliser = _STRUCTURED_NORMALISERS.get(field_name)
    if normaliser is not None:
        return normaliser(value)
    return text_comparison_key(value)


def check_equals(field_name: str, value: str, rule: EqualsRule) -> CheckOutcome:
    normalised = _normalise_for_field(field_name, value)
    expected = _normalise_for_field(field_name, rule.value) or rule.value
    if normalised is None:
        return CheckOutcome(
            verdict="mismatch", found=value, expected=expected, reason=f"{value!r} does not parse"
        )
    if normalised == expected:
        return CheckOutcome(verdict="match", found=normalised, expected=expected, reason="matches")
    return CheckOutcome(
        verdict="mismatch",
        found=normalised,
        expected=expected,
        reason=f"expected {expected!r}, found {normalised!r}",
    )


def check_in_list(field_name: str, value: str, rule: InListRule) -> CheckOutcome:
    normalised = _normalise_for_field(field_name, value)
    expected_text = ", ".join(rule.allowed)
    if normalised is None:
        return CheckOutcome(
            verdict="mismatch",
            found=value,
            expected=expected_text,
            reason=f"{value!r} does not parse",
        )
    compare_value = normalised
    allowed = list(rule.allowed)
    if rule.compare_digits is not None:
        compare_value = normalised[: rule.compare_digits]
        allowed = [a[: rule.compare_digits] for a in allowed]
    if compare_value in allowed:
        return CheckOutcome(
            verdict="match", found=normalised, expected=expected_text, reason="in list"
        )
    return CheckOutcome(
        verdict="mismatch",
        found=normalised,
        expected=expected_text,
        reason=f"{normalised!r} is not one of {rule.allowed}",
    )


def check_pattern(field_name: str, value: str, rule: PatternRule) -> CheckOutcome:
    stripped = value.strip()
    expected_text = f"matches {rule.regex}"
    if stripped in rule.reject:
        return CheckOutcome(
            verdict="mismatch",
            found=stripped,
            expected=expected_text,
            reason=f"{stripped!r} is a placeholder value",
        )
    if re.fullmatch(rule.regex, stripped):
        return CheckOutcome(
            verdict="match", found=stripped, expected=expected_text, reason="matches"
        )
    return CheckOutcome(
        verdict="mismatch",
        found=stripped,
        expected=expected_text,
        reason=f"{stripped!r} does not match the expected pattern",
    )


def check_quantity(field_name: str, value: str, rule: QuantityRule) -> CheckOutcome:
    weight = normalise_weight(value)
    if weight is None:
        return CheckOutcome(
            verdict="mismatch", found=value, expected=rule.unit, reason=f"{value!r} does not parse"
        )
    found = f"{weight.amount:g} {weight.unit}"
    if weight.unit != rule.unit:
        # design section 3.4, task 4: "A weight in LBS is a mismatch, with 'expected KG'.
        # Converting it would hide a document the customer's rules reject" -- so a unit
        # mismatch is reported exactly as read, never converted first.
        return CheckOutcome(
            verdict="mismatch",
            found=found,
            expected=rule.unit,
            reason=f"unit is {weight.unit}, expected {rule.unit}",
        )
    bounds_ok = True
    if rule.min_exclusive is not None and not weight.amount > rule.min_exclusive:
        bounds_ok = False
    if rule.max_exclusive is not None and not weight.amount < rule.max_exclusive:
        bounds_ok = False
    if rule.min is not None and not weight.amount >= rule.min:
        bounds_ok = False
    if rule.max is not None and not weight.amount <= rule.max:
        bounds_ok = False
    if not bounds_ok:
        return CheckOutcome(
            verdict="mismatch",
            found=found,
            expected=f"a valid quantity in {rule.unit}",
            reason=f"{found} is out of the expected range",
        )
    return CheckOutcome(verdict="match", found=found, expected=rule.unit, reason="matches")


def check_entity_name(field_name: str, value: str, rule: EntityNameRule) -> CheckOutcome:
    candidate_key = entity_name_key(value)
    best_score = fuzz.ratio(candidate_key, entity_name_key(rule.registered))
    best_name = rule.registered
    for alias in rule.aliases:
        score = fuzz.ratio(candidate_key, entity_name_key(alias))
        if score > best_score:
            best_score, best_name = score, alias

    if best_score >= ENTITY_NAME_EXACT_MIN:
        return CheckOutcome(
            verdict="match",
            found=value,
            expected=rule.registered,
            reason="matches",
            score=best_score,
        )
    if best_score >= ENTITY_NAME_JUDGEMENT_MIN:
        return CheckOutcome(
            verdict="needs_judgement",
            found=value,
            expected=rule.registered,
            reason=f"{best_score:.0f}% similar to {best_name!r}; needs a judgement call",
            score=best_score,
        )
    return CheckOutcome(
        verdict="mismatch",
        found=value,
        expected=rule.registered,
        reason=f"only {best_score:.0f}% similar to {best_name!r}",
        score=best_score,
    )


def check_llm_judgement(field_name: str, value: str, rule: LlmJudgementRule) -> CheckOutcome:
    return CheckOutcome(
        verdict="needs_judgement",
        found=value,
        expected=rule.criterion,
        reason="settled by the goods-description judgement call",
    )


_STRUCTURED_NORMALISERS: dict[str, object] = {
    "hs_code": normalise_hs_code,
    "port_of_loading": normalise_port,
    "port_of_discharge": normalise_port,
    "incoterms": normalise_incoterm,
}

_CHECKERS: dict[type, object] = {
    EqualsRule: check_equals,
    InListRule: check_in_list,
    PatternRule: check_pattern,
    QuantityRule: check_quantity,
    EntityNameRule: check_entity_name,
    LlmJudgementRule: check_llm_judgement,
}


def check_rule(field_name: str, value: str, rule: Rule) -> CheckOutcome:
    """Dispatch to the right checker for `rule`'s type."""
    checker = _CHECKERS.get(type(rule))
    if checker is None:
        raise ValueError(f"No checker registered for rule type {type(rule).__name__}")
    return checker(field_name, value, rule)
