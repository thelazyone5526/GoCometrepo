"""Tests for the LLM client wrapper (Phase 4, item 8): retries, the fallback model, the
per-run budget and the call recorder. Every test here uses `FakeTransport`, so none of them
spends Gemini quota (the `live` test that makes real calls is `test_llm_client_live.py`).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.llm.budget import DEFAULT_MAX_CALLS, CallBudget
from app.llm.client import LLMClient
from app.llm.errors import BudgetExceededError, LLMUnavailableError
from app.llm.fake_transport import (
    FakeTransport,
    bad_json_response,
    daily_quota_error,
    rate_limit_error,
    server_error,
    timeout_error,
)
from app.llm.prompt_files import Prompt
from app.llm.recorder import CallRecorder

PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash-lite"


class Answer(BaseModel):
    value: str


def _ok(value: str = "ok", *, input_tokens: int = 100, output_tokens: int = 10):
    from app.llm.transport import RawResponse

    return RawResponse(
        text=Answer(value=value).model_dump_json(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=0,
    )


def make_client(
    *, fallback_model: str = FALLBACK, max_calls: int = DEFAULT_MAX_CALLS, run_id: str = "run-1"
) -> tuple[LLMClient, FakeTransport, CallRecorder, CallBudget]:
    transport = FakeTransport()
    recorder = CallRecorder()
    budget = CallBudget(max_calls=max_calls)
    client = LLMClient(
        transport=transport,
        primary_model=PRIMARY,
        fallback_model=fallback_model,
        budget=budget,
        recorder=recorder,
        run_id=run_id,
        sleep=lambda _seconds: None,  # tests never wait for real
    )
    return client, transport, recorder, budget


def _prompt() -> Prompt:
    return Prompt(name="ping_v1", version="1", text="system instruction text")


# --- Retry and success ---------------------------------------------------------------------


def test_a_retry_followed_by_success_returns_the_validated_value() -> None:
    client, transport, recorder, _budget = make_client()
    transport.queue(PRIMARY, timeout_error(), _ok("second try"))

    result = client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    assert result.value == Answer(value="second try")
    assert result.model == PRIMARY
    assert result.is_fallback is False
    statuses = [r.status for r in recorder.for_run("run-1")]
    assert statuses == ["retryable_error", "success"]


def test_success_on_the_first_attempt_needs_only_one_record() -> None:
    client, transport, recorder, _budget = make_client()
    transport.queue(PRIMARY, _ok())

    client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    assert len(recorder.for_run("run-1")) == 1
    assert recorder.for_run("run-1")[0].attempt == 1


# --- Fallback ---------------------------------------------------------------------------------


def test_primary_retries_used_up_leads_to_one_fallback_call_that_succeeds() -> None:
    client, transport, recorder, budget = make_client()
    # 1 initial attempt + 2 retries = 3 attempts, all failing on the primary.
    transport.queue(PRIMARY, timeout_error(), timeout_error(), timeout_error())
    transport.queue(FALLBACK, _ok("from lite"))

    result = client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    assert result.model == FALLBACK
    assert result.is_fallback is True
    assert result.value == Answer(value="from lite")
    records = recorder.for_run("run-1")
    assert [r.model for r in records] == [PRIMARY, PRIMARY, PRIMARY, FALLBACK]
    assert records[-1].status == "success"
    assert budget.calls_used == 2  # one call charged for the primary sequence, one for fallback


def test_a_daily_quota_429_skips_the_primarys_remaining_retries() -> None:
    client, transport, recorder, _budget = make_client()
    transport.queue(PRIMARY, daily_quota_error(retry_delay_seconds=3600.0))
    transport.queue(FALLBACK, _ok("from lite"))

    result = client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    assert result.model == FALLBACK
    # Exactly one attempt at the primary: the daily-quota 429 went straight to the fallback.
    primary_records = [r for r in recorder.for_run("run-1") if r.model == PRIMARY]
    assert len(primary_records) == 1


def test_a_400_error_does_not_fall_back() -> None:
    from app.llm.transport import NonRetryableTransportError

    client, transport, recorder, budget = make_client()
    transport.queue(PRIMARY, NonRetryableTransportError("bad request", status_code=400))

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected LLMUnavailableError")
    except LLMUnavailableError:
        pass

    # No call was ever attempted against the fallback model.
    assert all(r.model == PRIMARY for r in recorder.for_run("run-1"))
    assert budget.calls_used == 1


def test_both_models_failing_gives_unavailable() -> None:
    client, transport, _recorder, _budget = make_client()
    transport.queue(PRIMARY, timeout_error(), timeout_error(), timeout_error())
    transport.queue(FALLBACK, timeout_error(), timeout_error(), timeout_error())

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected LLMUnavailableError")
    except LLMUnavailableError:
        pass


def test_fallback_is_off_when_fallback_model_is_empty() -> None:
    client, transport, recorder, budget = make_client(fallback_model="")
    transport.queue(PRIMARY, timeout_error(), timeout_error(), timeout_error())

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected LLMUnavailableError")
    except LLMUnavailableError:
        pass

    assert all(r.model == PRIMARY for r in recorder.for_run("run-1"))
    assert budget.calls_used == 1  # only the primary sequence was ever charged


def test_server_error_and_ordinary_rate_limit_are_both_retried() -> None:
    client, transport, _recorder, _budget = make_client()
    transport.queue(PRIMARY, server_error(), rate_limit_error(), _ok())

    result = client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
    assert result.model == PRIMARY


# --- Schema failures --------------------------------------------------------------------------


def test_bad_json_counts_as_an_attempt() -> None:
    client, transport, recorder, _budget = make_client()
    transport.queue(PRIMARY, bad_json_response(), _ok("recovered"))

    result = client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    assert result.value == Answer(value="recovered")
    statuses = [r.status for r in recorder.for_run("run-1")]
    assert statuses == ["invalid_schema", "success"]


def test_bad_json_from_every_attempt_falls_back_then_fails() -> None:
    client, transport, _recorder, _budget = make_client()
    transport.queue(PRIMARY, bad_json_response(), bad_json_response(), bad_json_response())
    transport.queue(FALLBACK, bad_json_response(), bad_json_response(), bad_json_response())

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected LLMUnavailableError")
    except LLMUnavailableError:
        pass


# --- Budget -------------------------------------------------------------------------------------


def test_a_seventh_call_is_refused() -> None:
    client, transport, _recorder, budget = make_client(max_calls=6)
    for _ in range(6):
        transport.queue(PRIMARY, _ok())

    for _ in range(6):
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
    assert budget.remaining == 0

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected BudgetExceededError")
    except BudgetExceededError:
        pass
    # The refused call never reached the transport at all.
    assert len(transport.calls) == 6


def test_a_seventh_call_is_refused_even_when_fallback_calls_used_up_the_budget() -> None:
    """Each generate() that fails on the primary and succeeds on the fallback spends 2 calls,
    so 3 such generate() calls use up a budget of 6; the 4th must be refused outright."""
    client, transport, _recorder, budget = make_client(max_calls=6)
    for _ in range(3):
        transport.queue(PRIMARY, timeout_error(), timeout_error(), timeout_error())
        transport.queue(FALLBACK, _ok())

    for _ in range(3):
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
    assert budget.calls_used == 6

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected BudgetExceededError")
    except BudgetExceededError:
        pass


def test_budget_exceeded_error_makes_no_call_at_all() -> None:
    client, transport, _recorder, _budget = make_client(max_calls=1)
    transport.queue(PRIMARY, _ok())
    client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    try:
        client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)
        raise AssertionError("expected BudgetExceededError")
    except BudgetExceededError:
        pass
    assert len(transport.calls) == 1


# --- Recording ------------------------------------------------------------------------------


def test_every_attempt_is_recorded_with_its_model() -> None:
    client, transport, recorder, _budget = make_client()
    transport.queue(PRIMARY, timeout_error(), timeout_error(), timeout_error())
    transport.queue(FALLBACK, timeout_error(), _ok())

    client.generate(agent="extract", prompt=_prompt(), text="hi", response_schema=Answer)

    records = recorder.for_run("run-1")
    assert [r.model for r in records] == [PRIMARY, PRIMARY, PRIMARY, FALLBACK, FALLBACK]
    assert [r.is_fallback for r in records] == [False, False, False, True, True]
    assert records[-1].status == "success"
    assert all(r.prompt_name == "ping_v1" and r.prompt_version == "1" for r in records)


def test_call_images_are_forwarded_to_the_transport() -> None:
    client, transport, _recorder, _budget = make_client()
    transport.queue(PRIMARY, _ok())

    client.generate(
        agent="extract",
        prompt=_prompt(),
        text="hi",
        images=[b"fake-png-bytes"],
        response_schema=Answer,
    )

    assert transport.calls[-1].image_count == 1
