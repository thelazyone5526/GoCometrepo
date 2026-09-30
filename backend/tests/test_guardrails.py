"""Tests for the Router's guardrails (Phase 7, design section 3.5): the code that decides
which outcomes are allowed *before* Gemini picks one, and checks whatever it picks.

The key test (Phase 7 verify list) sweeps every combination of match/mismatch/uncertain
across the 8 fields (3**8 = 6561 cases) with a model that always tries to propose
auto_approve, and asserts it never succeeds unless every field matched.
"""

from __future__ import annotations

from itertools import product

import pytest

from app.agents.validate import FieldVerdict
from app.trust.fields import FIELD_NAMES
from app.trust.guardrails import (
    allowed_outcomes,
    apply_guardrails,
    complete_amendment_draft,
    fallback_decision,
)

VERDICTS = ("match", "mismatch", "uncertain")


def _verdict(name: str, verdict: str) -> FieldVerdict:
    return FieldVerdict(
        field_name=name,
        verdict=verdict,
        found="x" if verdict != "match" else None,
        expected="y",
        rule_id="R-1",
        reason=f"{verdict} for testing",
        verdict_confidence=0.9,
    )


def _fields(combo: tuple[str, ...]) -> dict[str, FieldVerdict]:
    return {name: _verdict(name, v) for name, v in zip(FIELD_NAMES, combo, strict=True)}


# --- The key combinatorial test: auto_approve must never slip through -------------------------


@pytest.mark.parametrize("combo", list(product(VERDICTS, repeat=len(FIELD_NAMES))))
def test_auto_approve_never_succeeds_unless_every_field_matches(combo: tuple[str, ...]) -> None:
    fields = _fields(combo)
    all_match = all(v == "match" for v in combo)

    decision = apply_guardrails(
        proposed_outcome="auto_approve",
        proposed_reasoning="the model always tries this, regardless of the verdicts",
        proposed_cited_fields=(),
        proposed_draft=None,
        fields=fields,
    )

    if all_match:
        assert decision.outcome == "auto_approve"
        assert decision.decision_source == "llm"
    else:
        assert decision.outcome != "auto_approve"
        assert decision.decision_source == "code_override"


@pytest.mark.parametrize("combo", list(product(VERDICTS, repeat=len(FIELD_NAMES))))
def test_auto_approve_is_only_ever_in_the_allowed_list_when_every_field_matches(
    combo: tuple[str, ...],
) -> None:
    fields = _fields(combo)
    all_match = all(v == "match" for v in combo)
    allowed = allowed_outcomes(fields)
    assert ("auto_approve" in allowed) == all_match


# --- allowed_outcomes: the table from design section 3.5 ---------------------------------------


def test_every_field_matching_allows_auto_approve_and_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    assert set(allowed_outcomes(fields)) == {"auto_approve", "human_review"}


def test_mismatches_only_allow_amendment_request_and_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    assert set(allowed_outcomes(fields)) == {"amendment_request", "human_review"}


def test_uncertain_fields_only_allow_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "uncertain")
    assert set(allowed_outcomes(fields)) == {"human_review"}


def test_mismatch_and_uncertain_together_allow_amendment_request_and_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    fields["incoterms"] = _verdict("incoterms", "uncertain")
    assert set(allowed_outcomes(fields)) == {"amendment_request", "human_review"}


def test_not_applicable_fields_are_ignored_when_deciding_allowed_outcomes() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = FieldVerdict(
        field_name="hs_code",
        verdict="not_applicable",
        found=None,
        expected=None,
        rule_id=None,
        reason="not expected",
        verdict_confidence=1.0,
    )
    assert set(allowed_outcomes(fields)) == {"auto_approve", "human_review"}


# --- apply_guardrails: overriding a disallowed outcome ------------------------------------------


def test_a_disallowed_outcome_is_overridden_to_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")  # allowed: amendment_request, human_review
    decision = apply_guardrails(
        proposed_outcome="auto_approve",
        proposed_reasoning="looks fine to me",
        proposed_cited_fields=(),
        proposed_draft=None,
        fields=fields,
    )
    assert decision.outcome == "human_review"
    assert decision.decision_source == "code_override"
    assert decision.override_reason is not None


def test_an_allowed_outcome_passes_through_as_llm_sourced() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    decision = apply_guardrails(
        proposed_outcome="auto_approve",
        proposed_reasoning="every field matched",
        proposed_cited_fields=(),
        proposed_draft=None,
        fields=fields,
    )
    assert decision.outcome == "auto_approve"
    assert decision.decision_source == "llm"


# --- complete_amendment_draft: draft completeness -----------------------------------------------


def test_a_draft_missing_a_mismatch_gets_it_appended() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = FieldVerdict(
        field_name="hs_code",
        verdict="mismatch",
        found="850440",
        expected="one of the approved codes",
        rule_id="R-HS-1",
        reason="not on ACME's approved list",
        verdict_confidence=0.95,
    )
    draft, appended = complete_amendment_draft("Please review the incoterms field.", fields)
    assert appended is True
    assert "hs_code" in draft
    assert "850440" in draft


def test_a_draft_that_already_covers_every_mismatch_is_left_alone() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    draft = "The hs_code field needs correcting."
    result, appended = complete_amendment_draft(draft, fields)
    assert appended is False
    assert result == draft


def test_apply_guardrails_marks_an_appended_draft_as_code_override() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    decision = apply_guardrails(
        proposed_outcome="amendment_request",
        proposed_reasoning="the incoterm looks wrong",
        proposed_cited_fields=("incoterms",),
        proposed_draft="Please correct the incoterm on your invoice.",
        fields=fields,
    )
    assert decision.outcome == "amendment_request"
    assert decision.decision_source == "code_override"
    assert "hs_code" in (decision.amendment_draft or "")


# --- fallback_decision: always safe, never calls Gemini -----------------------------------------


def test_fallback_decision_is_always_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    decision = fallback_decision(fields)
    assert decision.outcome == "human_review"
    assert decision.decision_source == "fallback"
    assert "hs_code" in decision.reasoning


def test_fallback_decision_on_an_all_match_document_still_goes_to_human_review() -> None:
    fields = {name: _verdict(name, "match") for name in FIELD_NAMES}
    decision = fallback_decision(fields)
    assert decision.outcome == "human_review"
    assert decision.decision_source == "fallback"
