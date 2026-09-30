"""Router guardrails (design section 3.5): code decides which outcomes are on the table
*before* Gemini ever picks one, checks whatever it picks against that list, and can complete
an amendment draft or replace the whole decision with a safe template if Gemini can't be
reached at all.

This is Nova's "code provides guarantees" half of the pipeline (Ansh/00-architecture-
overview.md section 4): nothing here ever needs an LLM call to run, so a document can always
be routed to a safe outcome even when Gemini is completely unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agents.validate import FieldVerdict

Outcome = Literal["auto_approve", "human_review", "amendment_request"]

# design section 3.5's allowed-outcomes table. Order matters only for readability; membership
# is what `allowed_outcomes` checks.
_ALL_MISMATCH_OR_UNCERTAIN_ALLOWED: tuple[Outcome, ...] = ("human_review",)
_ONLY_MISMATCHES_ALLOWED: tuple[Outcome, ...] = ("amendment_request", "human_review")
_HAS_UNCERTAIN_ALLOWED: tuple[Outcome, ...] = ("human_review",)
_MIXED_ALLOWED: tuple[Outcome, ...] = ("amendment_request", "human_review")
_ALL_MATCH_ALLOWED: tuple[Outcome, ...] = ("auto_approve", "human_review")


def _expected_verdicts(fields: dict[str, FieldVerdict]) -> list[FieldVerdict]:
    """Every field the document type actually expects (design section 3.4's `not_applicable`
    fields never affect the decision, the same way they never affect the Validator's own
    confidence gate)."""
    return [v for v in fields.values() if v.verdict != "not_applicable"]


def allowed_outcomes(fields: dict[str, FieldVerdict]) -> tuple[Outcome, ...]:
    """Design section 3.5's table: what outcomes the Router is even allowed to consider,
    decided entirely from the verdicts, before Gemini sees anything. `human_review` is always
    on the list -- the model can always escalate something that merely looks odd, but it can
    never manufacture a path to `auto_approve` or `amendment_request` that isn't already
    justified by the verdicts themselves.
    """
    expected = _expected_verdicts(fields)
    if not expected:
        # No fields expected for this document type at all: nothing to check, so there's
        # nothing safe to auto-approve either. Send it to a person.
        return _HAS_UNCERTAIN_ALLOWED

    has_mismatch = any(v.verdict == "mismatch" for v in expected)
    has_uncertain = any(v.verdict == "uncertain" for v in expected)

    if has_mismatch and has_uncertain:
        return _MIXED_ALLOWED
    if has_uncertain:
        return _HAS_UNCERTAIN_ALLOWED
    if has_mismatch:
        return _ONLY_MISMATCHES_ALLOWED
    return _ALL_MATCH_ALLOWED


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reasoning: str
    cited_fields: tuple[str, ...]
    amendment_draft: str | None
    decision_source: Literal["llm", "code_override", "fallback"]
    override_reason: str | None = None


def _mismatched_fields(fields: dict[str, FieldVerdict]) -> list[FieldVerdict]:
    return [v for v in _expected_verdicts(fields) if v.verdict == "mismatch"]


def _draft_line(verdict: FieldVerdict) -> str:
    return (
        f"- {verdict.field_name}: found {verdict.found!r}, expected {verdict.expected!r} "
        f"({verdict.reason})"
    )


def complete_amendment_draft(draft: str, fields: dict[str, FieldVerdict]) -> tuple[str, bool]:
    """Design section 3.5 step 3: "Draft is missing a mismatched field, or its found or
    expected value -> code appends a line for it." Returns the (possibly appended) draft and
    whether anything was appended, so the caller can log it.

    A field counts as "missing" from the draft if its name doesn't appear in the draft text at
    all -- a cheap, deliberately generous check (it can't tell whether the draft's mention of
    the field actually states the right found/expected values), which is exactly why this is a
    safety net under the model's draft, not a replacement for it: it guarantees every mismatch
    is at least named, never that the model's own wording about it was accurate.
    """
    missing = [v for v in _mismatched_fields(fields) if v.field_name not in draft]
    if not missing:
        return draft, False
    appended = "\n".join(_draft_line(v) for v in missing)
    separator = "\n\n" if draft.strip() else ""
    note = "The following discrepancies were not mentioned above:\n" if draft.strip() else ""
    return f"{draft}{separator}{note}{appended}", True


def apply_guardrails(
    *,
    proposed_outcome: str,
    proposed_reasoning: str,
    proposed_cited_fields: tuple[str, ...],
    proposed_draft: str | None,
    fields: dict[str, FieldVerdict],
) -> Decision:
    """Design section 3.5 step 3: check whatever Gemini picked against `allowed_outcomes`,
    and complete its draft if it's missing a discrepancy. Never called for a fallback
    decision (see `fallback_decision`) -- this function assumes Gemini actually answered."""
    allowed = allowed_outcomes(fields)

    if proposed_outcome not in allowed:
        safe_outcome: Outcome = "human_review"
        return Decision(
            outcome=safe_outcome,
            reasoning=proposed_reasoning,
            cited_fields=proposed_cited_fields,
            amendment_draft=None,
            decision_source="code_override",
            override_reason=(
                f"model proposed {proposed_outcome!r}, which is not allowed given the "
                f"verdicts (allowed: {list(allowed)})"
            ),
        )

    outcome: Outcome = proposed_outcome  # type: ignore[assignment]  # validated above
    draft = proposed_draft
    if outcome == "amendment_request":
        draft = proposed_draft or ""
        draft, appended = complete_amendment_draft(draft, fields)
        override_reason = "draft was missing a discrepancy; code appended it" if appended else None
        return Decision(
            outcome=outcome,
            reasoning=proposed_reasoning,
            cited_fields=proposed_cited_fields,
            amendment_draft=draft,
            decision_source="code_override" if appended else "llm",
            override_reason=override_reason,
        )

    return Decision(
        outcome=outcome,
        reasoning=proposed_reasoning,
        cited_fields=proposed_cited_fields,
        amendment_draft=None,
        decision_source="llm",
    )


def fallback_decision(fields: dict[str, FieldVerdict]) -> Decision:
    """Design section 3.5 step 3 / section 8: "Gemini fails after retries, or the budget is
    used up -> decision_source = fallback. The outcome is the safest allowed one
    (human_review), with reasoning from a template listing each non-matching field." Never
    raises, never calls Gemini -- this is what keeps a document safe to route even when the
    model is completely unreachable.
    """
    non_matching = [v for v in _expected_verdicts(fields) if v.verdict != "match"]
    if non_matching:
        lines = "; ".join(f"{v.field_name} is {v.verdict} ({v.reason})" for v in non_matching)
        reasoning = f"Sent to human review because the AI decision step was unavailable. {lines}."
    else:
        reasoning = (
            "Sent to human review because the AI decision step was unavailable, even though "
            "every field matched -- a person should confirm before this is approved."
        )
    return Decision(
        outcome="human_review",
        reasoning=reasoning,
        cited_fields=tuple(v.field_name for v in non_matching),
        amendment_draft=None,
        decision_source="fallback",
    )


__all__ = [
    "Decision",
    "Outcome",
    "allowed_outcomes",
    "apply_guardrails",
    "complete_amendment_draft",
    "fallback_decision",
]
