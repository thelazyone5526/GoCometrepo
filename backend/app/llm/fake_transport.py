"""A scripted `Transport` for tests (design section 3.6, Phase 4 verify list): no network call
is ever made, so unit tests never spend Gemini quota.

Usage: queue up what should happen on each successive call to a given model with `queue()`,
then pass the `FakeTransport` to `LLMClient`. Each queued item is either a `RawResponse`
(success) or a `TransportError` to raise. Calls to a model with an empty queue raise
`AssertionError`, so a test that queues too few responses fails loudly instead of hanging.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .transport import RawResponse, RetryableTransportError, TransportError


@dataclass
class RecordedCall:
    model: str
    system_instruction: str
    text: str
    image_count: int


def timeout_error() -> RetryableTransportError:
    return RetryableTransportError("simulated timeout", status_code=None)


def server_error(status_code: int = 503) -> RetryableTransportError:
    return RetryableTransportError(f"simulated {status_code}", status_code=status_code)


def rate_limit_error() -> RetryableTransportError:
    """An ordinary (short) rate-limit 429: worth a normal retry, not a fallback trigger."""
    return RetryableTransportError("simulated 429 (rate limit)", status_code=429)


def daily_quota_error(retry_delay_seconds: float = 3600.0) -> RetryableTransportError:
    """A 429 whose suggested delay is too long to wait out (design section 3.6): `LLMClient`
    treats a retry delay past its own timeout as the daily quota being gone."""
    return RetryableTransportError(
        "simulated 429 (daily quota)", status_code=429, retry_delay_seconds=retry_delay_seconds
    )


def bad_json_response(*, input_tokens: int = 100, output_tokens: int = 10) -> RawResponse:
    """A response that arrives fine over HTTP but isn't valid JSON. `LLMClient` must count
    this as a failed attempt (design section 3.6: "a schema failure counts as a failed
    attempt"), never as a `TransportError`."""
    return RawResponse(
        text="not valid json {{{",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=0,
    )


class FakeTransport:
    """Queues of `RawResponse | TransportError`, one queue per model name."""

    def __init__(self) -> None:
        self._queues: dict[str, list[RawResponse | TransportError]] = defaultdict(list)
        self.calls: list[RecordedCall] = []

    def queue(self, model: str, *items: RawResponse | TransportError) -> None:
        self._queues[model].extend(items)

    def call(
        self,
        *,
        model: str,
        system_instruction: str,
        text: str,
        images: Sequence[bytes],
        response_schema: type,
        timeout_seconds: float,
        prompt_version: str = "",
    ) -> RawResponse:
        self.calls.append(
            RecordedCall(
                model=model,
                system_instruction=system_instruction,
                text=text,
                image_count=len(images),
            )
        )
        queue = self._queues[model]
        if not queue:
            raise AssertionError(
                f"FakeTransport: no more queued responses for model {model!r}. "
                f"Queue more with transport.queue({model!r}, ...) before this call."
            )
        item = queue.pop(0)
        if isinstance(item, TransportError):
            raise item
        return item
