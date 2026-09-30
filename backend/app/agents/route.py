"""The Router agent (design section 3.5): decides the final outcome for a document, inside
limits code has already worked out.

`route()` is the entry point. Step 1 (`app.trust.guardrails.allowed_outcomes`) and step 3
(`apply_guardrails` / `fallback_decision`) are both plain code with no LLM dependency; only
step 2 -- picking one outcome from the allowed list, writing the reasoning, and drafting an
amendment when needed -- goes through Gemini, in exactly one call per document.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.validate import FieldVerdict
from app.llm.client import LLMClient
from app.llm.errors import BudgetExceededError, LLMUnavailableError
from app.llm.prompt_files import load_prompt
from app.trust.guardrails import (
    Decision,
    allowed_outcomes,
    apply_guardrails,
    fallback_decision,
)


class RouteDecision(BaseModel):
    """Gemini's proposed decision, before code checks it. `outcome` is a plain string, not a
    Literal restricted to this document's *allowed* outcomes: the whole point of the
    guardrail is to catch the model picking something outside that list, which a schema-level
    restriction would silently prevent from ever being observed (and therefore ever caught by
    a test) in the first place."""

    outcome: Literal["auto_approve", "human_review", "amendment_request"]
    reasoning: str = Field(description="2-5 sentences citing the actual field names involved.")
    cited_fields: list[str] = Field(default_factory=list)
    amendment_draft: str | None = Field(
        default=None, description="Required if outcome is amendment_request, else null."
    )


def _verdict_summary(fields: dict[str, FieldVerdict]) -> str:
    lines = []
    for v in fields.values():
        if v.verdict == "not_applicable":
            continue
        lines.append(
            f"- {v.field_name}: {v.verdict} (found {v.found!r}, expected {v.expected!r}; "
            f"{v.reason})"
        )
    return "\n".join(lines)


def _prompt_text(fields: dict[str, FieldVerdict], allowed: tuple[str, ...]) -> str:
    return (
        f"Field verdicts:\n{_verdict_summary(fields)}\n\n"
        f"Allowed outcomes for this document: {list(allowed)}\n\n"
        "Decide the outcome, write your reasoning, and (if amendment_request) the draft."
    )


def route(*, fields: dict[str, FieldVerdict], client: LLMClient) -> Decision:
    """Run the Router on one document's field verdicts.

    Never raises: a failed or budget-exhausted Gemini call falls through to
    `fallback_decision`, which always returns `human_review` -- the one outcome that's always
    safe and never needs code to have understood anything about *why* the model was
    unreachable.
    """
    allowed = allowed_outcomes(fields)
    prompt = load_prompt("route_v1")
    text = _prompt_text(fields, allowed)

    try:
        result = client.generate(
            agent="route", prompt=prompt, text=text, response_schema=RouteDecision
        )
    except (LLMUnavailableError, BudgetExceededError):
        return fallback_decision(fields)

    proposed: RouteDecision = result.value
    return apply_guardrails(
        proposed_outcome=proposed.outcome,
        proposed_reasoning=proposed.reasoning,
        proposed_cited_fields=tuple(proposed.cited_fields),
        proposed_draft=proposed.amendment_draft,
        fields=fields,
    )


__all__ = ["RouteDecision", "route"]
