"""The one wrapper every Gemini call goes through (design section 3.6 and 8, Phase 4).

`LLMClient.generate` is the single entry point: give it the agent name, a prompt, the inputs
(text and page images) and a Pydantic output schema, and it returns a validated instance of
that schema. Everything else -- retries, the fallback model, the per-run budget, and logging
every attempt -- happens inside, so a node (Phase 5 onward) never touches the transport, the
budget counter or the recorder directly.

Failure modes a caller can see, and nothing else:
- `BudgetExceededError`: no call was made at all, the run is out of calls.
- `LLMUnavailableError`: every model tried failed for a real reason.
- `pydantic.ValidationError`: never escapes `generate` -- a schema failure is retried like any
  other failure, and only turns into `LLMUnavailableError` once every attempt is exhausted.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .budget import CallBudget
from .errors import LLMUnavailableError
from .prompt_files import Prompt
from .recorder import CallRecorder
from .transport import NonRetryableTransportError, RetryableTransportError, Transport


class _NonRetryableFailure(Exception):
    """Internal signal: this model rejected the request outright (a 4xx other than 429).

    Design section 3.6: "no fallback on other 4xx errors (a bad request fails on any
    model)". This is raised out of `_try_model` and caught in `generate` specifically so a
    non-retryable failure short-circuits straight to `LLMUnavailableError`, instead of being
    treated the same as "retries exhausted, worth trying the fallback".
    """


SchemaT = TypeVar("SchemaT", bound=BaseModel)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2  # retries *after* the first attempt, per model (design section 3.6)

# Exponential backoff between retries of the *same* model, when the server gave no better hint.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MULTIPLIER = 2.0


class GenerateResult:
    """What `LLMClient.generate` hands back: the validated object, plus which model actually
    answered (design section 3.6: "the returned object says which model answered")."""

    def __init__(self, value: BaseModel, *, model: str, is_fallback: bool) -> None:
        self.value = value
        self.model = model
        self.is_fallback = is_fallback


def _sleep_seconds_for_retry(attempt: int, suggested: float | None) -> float:
    """How long to wait before the next attempt at the *same* model.

    The API's own suggested delay (from `google.rpc.RetryInfo` on a 429) wins when it gave
    one; otherwise a plain exponential backoff, indexed from the attempt that just failed
    (design section 3.6: "2 retries with backoff ... using the API's suggested retry delay
    when it gives one").
    """
    if suggested is not None:
        return suggested
    return _BACKOFF_BASE_SECONDS * (_BACKOFF_MULTIPLIER**attempt)


class LLMClient:
    def __init__(
        self,
        *,
        transport: Transport,
        primary_model: str,
        fallback_model: str,
        budget: CallBudget,
        recorder: CallRecorder,
        run_id: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._primary_model = primary_model
        self._fallback_model = fallback_model  # "" disables the fallback
        self._budget = budget
        self._recorder = recorder
        self._run_id = run_id
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep

    @property
    def fallback_enabled(self) -> bool:
        return bool(self._fallback_model)

    @property
    def calls_remaining(self) -> int:
        """How many calls this run's budget has left. Lets a node decide whether an optional
        extra call (Phase 5's 300 DPI retry) is worth attempting at all, before doing the
        work of preparing its inputs."""
        return self._budget.remaining

    def generate(
        self,
        *,
        agent: str,
        prompt: Prompt,
        text: str,
        images: Sequence[bytes] = (),
        response_schema: type[SchemaT],
    ) -> GenerateResult:
        """One logical call: try the primary model (with retries), then the fallback model
        (with its own retries) if the primary never succeeded. Raises `BudgetExceededError`
        before any request goes out if the run has no calls left, and `LLMUnavailableError`
        if every model tried still failed."""
        self._budget.charge()
        try:
            primary_outcome = self._try_model(
                model=self._primary_model,
                is_fallback=False,
                agent=agent,
                prompt=prompt,
                text=text,
                images=images,
                response_schema=response_schema,
            )
        except _NonRetryableFailure as exc:
            raise LLMUnavailableError(f"{self._primary_model} rejected the request: {exc}") from exc
        if primary_outcome is not None:
            return primary_outcome

        if not self.fallback_enabled:
            raise LLMUnavailableError(
                f"{self._primary_model} failed and no fallback model is configured"
            )

        self._budget.charge()
        try:
            fallback_outcome = self._try_model(
                model=self._fallback_model,
                is_fallback=True,
                agent=agent,
                prompt=prompt,
                text=text,
                images=images,
                response_schema=response_schema,
            )
        except _NonRetryableFailure as exc:
            raise LLMUnavailableError(
                f"{self._fallback_model} rejected the request: {exc}"
            ) from exc
        if fallback_outcome is not None:
            return fallback_outcome

        raise LLMUnavailableError(f"Both {self._primary_model} and {self._fallback_model} failed")

    def _try_model(
        self,
        *,
        model: str,
        is_fallback: bool,
        agent: str,
        prompt: Prompt,
        text: str,
        images: Sequence[bytes],
        response_schema: type[SchemaT],
    ) -> GenerateResult | None:
        """Every attempt at one model: up to `1 + max_retries` tries. Returns None if they're
        all exhausted through retryable failures, so the caller can decide whether to fall
        back. Raises `_NonRetryableFailure` immediately on a non-retryable failure (a 4xx
        other than 429), since that never leads anywhere better on retry -- or, per design
        section 3.6, on any other model."""
        attempt = 0
        while attempt <= self._max_retries:
            attempt += 1
            start = time.perf_counter()
            try:
                raw = self._transport.call(
                    model=model,
                    system_instruction=prompt.text,
                    text=text,
                    images=images,
                    response_schema=response_schema,
                    timeout_seconds=self._timeout_seconds,
                    prompt_version=prompt.version,
                )
            except NonRetryableTransportError as exc:
                self._record(
                    agent=agent,
                    model=model,
                    is_fallback=is_fallback,
                    prompt=prompt,
                    attempt=attempt,
                    status="non_retryable_error",
                    tokens=(0, 0, 0),
                    latency_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                raise _NonRetryableFailure(str(exc)) from exc
            except RetryableTransportError as exc:
                self._record(
                    agent=agent,
                    model=model,
                    is_fallback=is_fallback,
                    prompt=prompt,
                    attempt=attempt,
                    status="retryable_error",
                    tokens=(0, 0, 0),
                    latency_ms=_elapsed_ms(start),
                    error=str(exc),
                )
                is_daily_quota = (
                    exc.status_code == 429
                    and exc.retry_delay_seconds is not None
                    and exc.retry_delay_seconds > self._timeout_seconds
                )
                if is_daily_quota:
                    return None  # go straight to the fallback; waiting this out isn't realistic
                if attempt <= self._max_retries:
                    self._sleep(_sleep_seconds_for_retry(attempt, exc.retry_delay_seconds))
                continue

            validated = self._validate(raw.text, response_schema)
            if validated is None:
                self._record(
                    agent=agent,
                    model=model,
                    is_fallback=is_fallback,
                    prompt=prompt,
                    attempt=attempt,
                    status="invalid_schema",
                    tokens=(raw.input_tokens, raw.output_tokens, raw.thinking_tokens),
                    latency_ms=_elapsed_ms(start),
                    error="response did not match the expected schema",
                )
                if attempt <= self._max_retries:
                    self._sleep(_sleep_seconds_for_retry(attempt, None))
                continue

            self._record(
                agent=agent,
                model=model,
                is_fallback=is_fallback,
                prompt=prompt,
                attempt=attempt,
                status="success_cached" if raw.cached else "success",
                tokens=(raw.input_tokens, raw.output_tokens, raw.thinking_tokens),
                latency_ms=_elapsed_ms(start),
                error=None,
            )
            return GenerateResult(value=validated, model=model, is_fallback=is_fallback)

        return None

    def _validate(self, text: str, response_schema: type[SchemaT]) -> SchemaT | None:
        try:
            return response_schema.model_validate_json(text)
        except ValidationError:
            return None

    def _record(
        self,
        *,
        agent: str,
        model: str,
        is_fallback: bool,
        prompt: Prompt,
        attempt: int,
        status: str,
        tokens: tuple[int, int, int],
        latency_ms: float,
        error: str | None,
    ) -> None:
        input_tokens, output_tokens, thinking_tokens = tokens
        self._recorder.record(
            run_id=self._run_id,
            agent=agent,
            model=model,
            is_fallback=is_fallback,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            attempt=attempt,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            latency_ms=latency_ms,
            error=error,
        )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0
