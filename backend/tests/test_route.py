"""Tests for the Router agent (Phase 7, item 6): `route()` wires Gemini's proposal through
the guardrails, and falls back safely when Gemini is unavailable. Uses `FakeTransport`, so no
Gemini quota is spent."""

from __future__ import annotations

from app.agents.route import RouteDecision, route
from app.agents.validate import FieldVerdict
from app.llm.budget import CallBudget
from app.llm.client import LLMClient
from app.llm.fake_transport import FakeTransport
from app.llm.recorder import CallRecorder
from app.llm.transport import RawResponse
from app.trust.fields import FIELD_NAMES

PRIMARY = "gemini-3.8-flash"


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


def _verdict(name: str, verdict: str, **overrides: object) -> FieldVerdict:
    base = dict(
        field_name=name,
        verdict=verdict,
        found="x" if verdict != "match" else None,
        expected="y",
        rule_id="R-1",
        reason=f"{verdict} for testing",
        verdict_confidence=0.9,
    )
    base.update(overrides)
    return FieldVerdict(**base)


def _all_match_fields() -> dict[str, FieldVerdict]:
    return {name: _verdict(name, "match") for name in FIELD_NAMES}


def _decision_response(**kwargs: object) -> RawResponse:
    decision = RouteDecision(**kwargs)
    return RawResponse(
        text=decision.model_dump_json(), input_tokens=300, output_tokens=80, thinking_tokens=0
    )


def test_route_approves_a_document_with_every_field_matching() -> None:
    client, transport = make_client()
    transport.queue(
        PRIMARY,
        _decision_response(
            outcome="auto_approve",
            reasoning="Every field matched the customer's rules.",
            cited_fields=[],
            amendment_draft=None,
        ),
    )
    decision = route(fields=_all_match_fields(), client=client)
    assert decision.outcome == "auto_approve"
    assert decision.decision_source == "llm"


def test_route_overrides_an_auto_approve_the_model_should_not_have_picked() -> None:
    """Even if Gemini somehow proposes auto_approve on a document with a mismatch (it
    shouldn't, since the prompt tells it the allowed list, but the guardrail is the real
    protection, not the prompt), code overrides it."""
    client, transport = make_client()
    fields = _all_match_fields()
    fields["hs_code"] = _verdict("hs_code", "mismatch")
    transport.queue(
        PRIMARY,
        _decision_response(
            outcome="auto_approve",
            reasoning="Looks fine.",
            cited_fields=[],
            amendment_draft=None,
        ),
    )
    decision = route(fields=fields, client=client)
    assert decision.outcome == "human_review"
    assert decision.decision_source == "code_override"


def test_route_completes_an_incomplete_amendment_draft() -> None:
    client, transport = make_client()
    fields = _all_match_fields()
    fields["hs_code"] = _verdict("hs_code", "mismatch", found="850440", expected="847130")
    transport.queue(
        PRIMARY,
        _decision_response(
            outcome="amendment_request",
            reasoning="The HS code is not on the approved list.",
            cited_fields=["hs_code"],
            amendment_draft="Please review the HS code on your invoice.",
        ),
    )
    decision = route(fields=fields, client=client)
    assert decision.outcome == "amendment_request"
    assert decision.amendment_draft is not None
    assert "850440" in decision.amendment_draft


def test_route_falls_back_to_human_review_when_gemini_is_unavailable() -> None:
    client, transport = make_client()
    bad = RawResponse(text="not valid json", input_tokens=10, output_tokens=0, thinking_tokens=0)
    transport.queue(PRIMARY, bad, bad, bad)  # 1 try + 2 retries, all bad JSON
    fields = _all_match_fields()
    fields["hs_code"] = _verdict("hs_code", "mismatch")

    decision = route(fields=fields, client=client)

    assert decision.outcome == "human_review"
    assert decision.decision_source == "fallback"
    assert "hs_code" in decision.reasoning


def test_route_falls_back_when_the_budget_is_already_exhausted() -> None:
    client, _transport = make_client(max_calls=0)
    decision = route(fields=_all_match_fields(), client=client)
    assert decision.outcome == "human_review"
    assert decision.decision_source == "fallback"


def test_route_never_raises_even_when_gemini_fails_on_an_all_match_document() -> None:
    """A fallback decision on an all-match document still sends it to a person -- the safe
    default never silently becomes an approval just because it's the "nice" outcome."""
    client, transport = make_client()
    bad = RawResponse(text="not valid json", input_tokens=10, output_tokens=0, thinking_tokens=0)
    transport.queue(PRIMARY, bad, bad, bad)
    decision = route(fields=_all_match_fields(), client=client)
    assert decision.outcome == "human_review"
